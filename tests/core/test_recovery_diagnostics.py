"""Recovery inspection is bounded, authorized and does not expose stored bodies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, effect_request_digest
from open_workflow_agent.errors import WorkflowExecutionError
from open_workflow_agent.recovery_diagnostics import recovery_diagnostics
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.storage import StorageConnection

SECRET = "private-input-output-database-credential"


def _handle(services: RuntimeServices, *, owner: str | None = None) -> Any:
    handle = services.invocations.create(
        engine="portable",
        session_id=None,
        user_id="correlation",
        workflow_name="diagnostic",
        workflow_version="1.0.0",
        workflow_fingerprint="definition",
        owner_principal=owner,
    )
    services.invocations.bind_input(handle, {"secret": SECRET})
    return handle


def _dump(services: RuntimeServices) -> list[str]:
    assert not services.invocations.connection.is_postgresql
    return list(services.invocations.connection.raw.iterdump())


def _compatible(services: RuntimeServices, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        services.recovery_definitions,
        "diagnostic_safe_snapshot",
        lambda _handle: {"status": "compatible", "reason": "original_definition_retained"},
    )


def test_common_diagnostics_distinguish_pending_known_and_held_owner_without_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    try:
        handle = _handle(services)
        _compatible(services, monkeypatch)
        services.execution_ownership.acquire(handle, seconds=60)
        for visit, status in enumerate(("completed", "pending"), start=1):
            identity = EffectIdentity(
                handle.invocation_id,
                handle.workflow_fingerprint,
                "/do/0/action",
                (("/do/0/action", visit),),
                "call:http",
            )
            receipt = services.effect_journal.begin(
                identity, effect_request_digest("http", {"secret": SECRET}), "original-owner"
            )
            if status == "completed":
                services.effect_journal.finish(identity, receipt.owner, output={"secret": SECRET})
        before = _dump(services)
        execute = StorageConnection.execute

        def reject_writes(
            connection: StorageConnection, statement: str, parameters: tuple[Any, ...] = ()
        ) -> Any:
            assert statement.lstrip().upper().startswith("SELECT"), statement
            return execute(connection, statement, parameters)

        with monkeypatch.context() as patch:
            patch.setattr(StorageConnection, "execute", reject_writes)
            report = recovery_diagnostics(services, handle)
        assert report["actions"] == {"known": 1, "pending": 1, "complete": True}
        assert report["ownership"] == "held" and report["native"] == "not_inspected"
        assert report["continuation"] == "common_checked"
        assert len(report["checks"]) <= 6
        assert SECRET not in json.dumps(report) and "original-owner" not in json.dumps(report)
        assert _dump(services) == before and not services.model.calls
    finally:
        services.close()


@pytest.mark.parametrize("damage", ["raised", "untrusted_reason", "changed_profile"])
def test_diagnostic_errors_return_only_allowlisted_recovery_conditions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    try:
        handle = _handle(services)

        def broken(_handle: Any) -> dict[str, Any]:
            if damage == "raised":
                raise WorkflowExecutionError(SECRET, details={"native_blob": SECRET})
            return {
                "status": "unavailable",
                "reason": "runtime_profile_changed" if damage == "changed_profile" else SECRET,
                "source": SECRET,
                "input": SECRET,
            }

        monkeypatch.setattr(services.recovery_definitions, "diagnostic_safe_snapshot", broken)
        before = _dump(services)
        report = recovery_diagnostics(services, handle)
        assert report["continuation"] == "unavailable" and report["native"] == "not_inspected"
        assert report["checks"][0]["reason"] == (
            "runtime_profile_changed"
            if damage == "changed_profile"
            else "definition_evidence_unavailable"
        )
        assert SECRET not in json.dumps(report) and len(json.dumps(report)) < 4096
        assert _dump(services) == before
    finally:
        services.close()


def test_diagnostic_sampling_bound_is_explicit_and_does_not_decode_all_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    try:
        handle = _handle(services)
        _compatible(services, monkeypatch)
        for visit in range(1, 102):
            services.effect_journal.begin(
                EffectIdentity(
                    handle.invocation_id,
                    handle.workflow_fingerprint,
                    "/do/0/action",
                    (("/do/0/action", visit),),
                    "call:http",
                ),
                effect_request_digest("http", {}),
                "original-owner",
            )
        before = _dump(services)
        monkeypatch.setattr(
            services.effect_journal,
            "get",
            lambda _identity: pytest.fail("an exceeded sample must not inspect every effect"),
        )
        report = recovery_diagnostics(services, handle)
        assert report["actions"] == {"known": 0, "pending": 0, "complete": False}
        assert any(
            row["reason"] == "inspection_limit_or_missing_evidence" for row in report["checks"]
        )
        assert SECRET not in json.dumps(report) and len(json.dumps(report)) < 4096
        assert _dump(services) == before
    finally:
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["resume", "cancel", "recovery"])
async def test_invocation_routes_hide_other_principals_before_archive_or_native_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    monkeypatch.setenv("OWA_DIAGNOSTIC_TOKEN", "test-api-token")
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "server": {"api_security_profile": "api"},
            "security": {
                "profiles": {
                    "api": {
                        "type": "bearer",
                        "principal": "api-owner",
                        "token": {"from_env": "OWA_DIAGNOSTIC_TOKEN"},
                    }
                }
            },
        }
    )
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    app = create_app(config=config, services=services)
    try:
        async with app.router.lifespan_context(app):
            foreign = _handle(services, owner="other-owner")
            before = _dump(services)
            monkeypatch.setattr(
                services,
                "resolve_original_definition",
                lambda _handle: pytest.fail("foreign invocation must not read its archive"),
            )
            monkeypatch.setattr(
                services.recovery_definitions,
                "diagnostic_safe_snapshot",
                lambda _handle: pytest.fail("foreign invocation must not inspect its archive"),
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                path = f"/v1/invocations/{foreign.invocation_id}/{route}"
                call = client.get if route == "recovery" else client.post
                request_body = {"json": {"input": None}} if route == "resume" else {}
                assert (await call(path, **request_body)).status_code == 401
                response = await call(
                    path, headers={"Authorization": "Bearer test-api-token"}, **request_body
                )
                assert response.status_code == 404
                assert response.json()["error"]["code"] == "invocation_not_found"
                assert SECRET not in response.text and "other-owner" not in response.text
                assert _dump(services) == before
    finally:
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["value", "null", "fault", "cancelled"])
async def test_legacy_terminal_result_reads_without_archive_or_native_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    config = RuntimeConfig.model_validate({"model": {"provider": "fake"}})
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    app = create_app(config=config, services=services)
    try:
        async with app.router.lifespan_context(app):
            handle = _handle(services)
            status = (
                "faulted"
                if outcome == "fault"
                else "cancelled"
                if outcome == "cancelled"
                else "completed"
            )
            services.invocations.update(
                handle,
                status=status,
                output={"original": True} if outcome == "value" else None,
                error={"code": outcome, "message": "original outcome", "details": {}}
                if outcome in {"fault", "cancelled"}
                else None,
            )
            before = _dump(services)
            monkeypatch.setattr(
                services,
                "resolve_original_definition",
                lambda _handle: pytest.fail("terminal legacy result needs no executable archive"),
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                result = await client.post(
                    f"/v1/invocations/{handle.invocation_id}/resume", json={"input": {"late": True}}
                )
                assert result.status_code == 200 and result.json()["status"] == status
                assert (
                    result.json()["output"] == handle.output
                    and result.json().get("error") == handle.error
                )
                report = await client.get(f"/v1/invocations/{handle.invocation_id}/recovery")
                assert (
                    report.status_code == 200 and report.json()["continuation"] == "terminal_result"
                )
                assert report.json()["native"] == "not_inspected" and SECRET not in report.text
                assert _dump(services) == before and not services.model.calls
    finally:
        services.close()
