"""Deterministic contracts for sandbox request preparation and manager forwarding."""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from open_workflow_agent.config import McpStdioConfig, SandboxConfig
from open_workflow_agent.errors import (
    SandboxPolicyError,
    WorkflowExecutionError,
    WorkflowSemanticError,
)
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.sandbox.contract import (
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxSecretReference,
)
from open_workflow_agent.sandbox.executor import SandboxWorkflowExecutor
from open_workflow_agent.sandbox.job_backend import SandboxJobLaunch
from open_workflow_agent.sandbox.job_validation import backend_mutation_fence, verify_backend_launch
from open_workflow_agent.sandbox.jobs import SandboxJobLease, SandboxJobProfile
from open_workflow_agent.sandbox.manager import SandboxManager
from open_workflow_agent.sandbox.stdio_policy import validate_stdio_transport
from open_workflow_agent.storage import StorageConnection
from open_workflow_agent.workflow import (
    _RECONCILE_EXPIRED_RETRY,
    ExecutionState,
    WorkflowExecutor,
)


def _state() -> ExecutionState:
    return ExecutionState(
        data={"name": "Ada", "count": 3},
        variables={"_task_reference": "/do/0"},
        metadata={"invocation_id": "invocation", "_workflow_fingerprint": "fingerprint"},
        execution_path=(("/do/0", 1),),
    )


def test_request_helpers_evaluate_values_and_keep_secret_references_typed() -> None:
    executor = SandboxWorkflowExecutor(None)
    state = _state()

    assert executor._arguments({"arguments": ["${ .name }", "${ .count }"]}, state) == (
        "Ada",
        "3",
    )
    assert executor._stdin({"stdin": "${ .name }"}, state) == "Ada"
    assert executor._stdin({}, state) is None
    assert executor._environment(
        {
            "environment": {
                "GREETING": "${ .name }",
                "TOKEN": {"fromEnv": "DEPLOYMENT_TOKEN"},
            }
        },
        state,
    ) == (
        ("GREETING", "Ada"),
        ("TOKEN", SandboxSecretReference("DEPLOYMENT_TOKEN")),
    )


@pytest.mark.parametrize(
    ("method", "definition", "message"),
    [
        ("_arguments", {"arguments": "not-an-array"}, "arguments must evaluate to an array"),
        ("_environment", {"environment": []}, "environment must be an object"),
        (
            "_environment",
            {"environment": {"TOKEN": {"fromEnv": 7}}},
            "fromEnv reference must be a string",
        ),
    ],
)
def test_request_helpers_reject_malformed_evaluated_configuration(
    method: str, definition: dict[str, Any], message: str
) -> None:
    with pytest.raises(WorkflowSemanticError, match=message):
        getattr(SandboxWorkflowExecutor(None), method)(definition, _state())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("stdout", "out"),
        ("stderr", "err"),
        ("code", 4),
        ("all", {"code": 4, "stdout": "out", "stderr": "err"}),
        ("none", None),
    ],
)
async def test_run_projection_uses_completed_result_fields(mode: str, expected: Any) -> None:
    executor = SandboxWorkflowExecutor(None)
    seen: list[tuple[dict[str, Any], bool]] = []

    async def run_script(definition: Any, state: ExecutionState, *, background: bool) -> Any:
        seen.append((definition, background))
        return {"code": 4, "stdout": "out", "stderr": "err"}

    executor._run_script = run_script  # type: ignore[method-assign]
    result = await executor._run_subworkflow(
        {"script": {"language": "python", "code": "pass"}, "return": mode}, _state()
    )
    assert result == expected
    assert seen == [({"language": "python", "code": "pass"}, False)]


@pytest.mark.asyncio
async def test_run_projection_keeps_background_acceptance_and_rejects_bad_mode() -> None:
    executor = SandboxWorkflowExecutor(None)

    async def accepted(definition: Any, state: ExecutionState, *, background: bool) -> str:
        assert background
        return "accepted"

    executor._run_script = accepted  # type: ignore[method-assign]
    result = await executor._run_subworkflow(
        {"script": {}, "await": False, "return": "none"}, _state()
    )
    assert result == "accepted"
    with pytest.raises(WorkflowSemanticError, match="return mode"):
        await executor._run_subworkflow({"script": {}, "return": "object"}, _state())
    with pytest.raises(WorkflowSemanticError, match="configuration must be an object"):
        await executor._run_subworkflow([], _state())


class _EventSink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)


class _DispatchExecutor(SandboxWorkflowExecutor):
    async def _run_effect(
        self, call: str, payload: Any, state: ExecutionState, dispatch: Any, **kwargs: Any
    ) -> Any:
        assert call == "run:script"
        assert payload["execution_id"] == "invocation:/do/0/run:script"
        return await dispatch()


@pytest.mark.asyncio
async def test_execute_sandbox_records_completion_and_returns_public_output() -> None:
    request = SandboxExecutionRequest("invocation:/do/0/run:script", "script")

    class Backend:
        async def execute(self, actual: SandboxExecutionRequest) -> SandboxExecutionResult:
            assert actual == request
            return SandboxExecutionResult(actual.execution_id, 0, "done", "", 0.125)

    sink = _EventSink()
    executor = _DispatchExecutor(None, services=SimpleNamespace(sandbox=Backend()), event_sink=sink)
    state = ExecutionState(
        data={},
        variables={"_task_reference": "/do/0", "_task_name": "run"},
        metadata={"invocation_id": "invocation"},
    )

    result = await executor._execute_sandbox(request, state)

    assert result == {"code": 0, "stdout": "done", "stderr": ""}
    assert [event.event_type for event in sink.events] == [
        "SandboxExecutionStarted",
        "TaskProgress",
        "SandboxExecutionCompleted",
        "TaskProgress",
    ]
    assert sink.events[-1].progress == {"phase": "sandbox_finished"}


@pytest.mark.asyncio
async def test_execute_sandbox_emits_failure_without_echoing_error_details() -> None:
    request = SandboxExecutionRequest("invocation:/do/0/run:script", "script")

    class Backend:
        async def execute(self, actual: SandboxExecutionRequest) -> Any:
            raise WorkflowExecutionError("private runtime detail")

    sink = _EventSink()
    executor = _DispatchExecutor(None, services=SimpleNamespace(sandbox=Backend()), event_sink=sink)
    state = ExecutionState(
        data={},
        variables={"_task_reference": "/do/0", "_task_name": "run"},
        metadata={"invocation_id": "invocation"},
    )

    with pytest.raises(WorkflowExecutionError, match="private runtime detail"):
        await executor._execute_sandbox(request, state)
    assert sink.events[-1].event_type == "SandboxExecutionFailed"
    assert sink.events[-1].error == {"code": "workflow_execution_error"}
    assert all("private runtime detail" not in repr(event) for event in sink.events)


@pytest.mark.asyncio
async def test_execute_sandbox_emits_cancellation_before_propagating_it() -> None:
    request = SandboxExecutionRequest("invocation:/do/0/run:script", "script")

    class Backend:
        async def execute(self, actual: SandboxExecutionRequest) -> Any:
            raise asyncio.CancelledError

    sink = _EventSink()
    executor = _DispatchExecutor(None, services=SimpleNamespace(sandbox=Backend()), event_sink=sink)
    state = ExecutionState(
        data={},
        variables={"_task_reference": "/do/0", "_task_name": "run"},
        metadata={"invocation_id": "invocation"},
    )

    with pytest.raises(asyncio.CancelledError):
        await executor._execute_sandbox(request, state)
    assert sink.events[-1].event_type == "SandboxExecutionCancelled"
    assert sink.events[-1].error == {"code": "invocation_cancelled"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "definition", "message"),
    [
        ("_run_script", None, "run.script must be an object"),
        ("_run_shell", "echo", "run.shell must be an object"),
        ("_run_container", [], "run.container must be an object"),
    ],
)
async def test_executable_run_variants_reject_non_object_definitions(
    method: str, definition: Any, message: str
) -> None:
    with pytest.raises(WorkflowSemanticError, match=message):
        await getattr(SandboxWorkflowExecutor(None), method)(definition, _state())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "definition"),
    [
        ("_run_script", {"language": "python", "code": "print('safe')"}),
        ("_run_shell", {"command": "approved-command", "arguments": ["--check"]}),
        ("_run_container", {"image": "approved-image", "command": "run"}),
    ],
)
async def test_executable_run_variants_fail_closed_without_sandbox_service(
    method: str, definition: dict[str, Any]
) -> None:
    with pytest.raises(SandboxPolicyError, match="sandbox service is unavailable"):
        await getattr(SandboxWorkflowExecutor(None), method)(definition, _state())


def _captured_script() -> Any:
    from open_workflow_agent.executable_sources import ExecutableScriptBinding, ExecutableSource

    code = b"print('deployment-approved')\n"
    source = ExecutableSource.capture(
        id="approved",
        version="1.0.0",
        uri="https://scripts.example.test/approved.py",
        code=code,
        digest=hashlib.sha256(code).hexdigest(),
    )
    return ExecutableScriptBinding("/do/0", source)


def _script_admission_plan(captured: Any) -> Any:
    task = SimpleNamespace(reference="/do/0", execution_reference=None)
    required = SimpleNamespace(reference="/do/0", as_value=captured.as_value)
    return SimpleNamespace(scopes=[SimpleNamespace(tasks=[task])], script_resources=[required])


@pytest.mark.asyncio
async def test_admitted_script_source_requires_a_live_execution_lease() -> None:
    captured = _captured_script()
    handle = ExecutionHandle(
        "invocation", "portable", "native", None, "session", "workflow", "1.0.0", "fingerprint"
    )
    services = SimpleNamespace(
        recovery_definitions=SimpleNamespace(
            verify_admission=lambda actual: _script_admission_plan(captured)
        )
    )
    state = _state()
    state.metadata.update(
        {
            "_invocation_handle": handle,
            "_script_resources": {"/do/0": captured.as_value()},
        }
    )

    with pytest.raises(SandboxPolicyError, match="source admission is unavailable"):
        await SandboxWorkflowExecutor(None, services=services)._run_script(
            {"language": "python", "code": "untrusted inline code"}, state
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("plan", "state_metadata", "message"),
    [
        (
            SimpleNamespace(scopes=[SimpleNamespace(tasks=[])], script_resources=[]),
            {},
            "differs from original admission",
        ),
        (
            SimpleNamespace(
                scopes=[
                    SimpleNamespace(
                        tasks=[SimpleNamespace(reference="/do/0", execution_reference=None)]
                    )
                ],
                script_resources=[
                    SimpleNamespace(reference="/do/0", as_value=lambda: {"approved": True})
                ],
            ),
            {},
            "source capture differs from original admission",
        ),
    ],
)
async def test_original_script_admission_rejects_changed_task_or_capture(
    plan: Any, state_metadata: dict[str, Any], message: str
) -> None:
    handle = ExecutionHandle(
        "invocation", "portable", "native", None, "session", "workflow", "1.0.0", "fingerprint"
    )
    services = SimpleNamespace(
        recovery_definitions=SimpleNamespace(verify_admission=lambda actual: plan)
    )
    state = _state()
    state.metadata.update({"_invocation_handle": handle, **state_metadata})

    with pytest.raises(SandboxPolicyError, match=message):
        await SandboxWorkflowExecutor(None, services=services)._run_script(
            {"language": "python", "code": "untrusted inline code"}, state
        )


@pytest.mark.asyncio
async def test_secret_environment_references_are_authorized_once_in_stable_order() -> None:
    handle = ExecutionHandle(
        "invocation", "portable", "native", None, "session", "workflow", "1.0.0", "fingerprint"
    )
    authorized: list[tuple[str, str]] = []

    async def authorize(
        actual_handle: Any,
        identity: Any,
        action: str,
        resource: str,
        *,
        observer: Any,
    ) -> None:
        assert actual_handle is handle
        assert identity.operation == "run:shell"
        authorized.append((action, resource))

    state = _state()
    state.metadata["_invocation_handle"] = handle
    request = SandboxExecutionRequest(
        "run",
        "shell",
        command="approved-command",
        environment=(
            ("SECOND", SandboxSecretReference("Z_SECRET")),
            ("FIRST", SandboxSecretReference("A_SECRET")),
            ("COPY", SandboxSecretReference("Z_SECRET")),
        ),
    )

    await SandboxWorkflowExecutor(
        None, services=SimpleNamespace(authorize_action_for=authorize)
    )._authorize_environment(request, state)

    assert authorized == [
        ("credential.resolve", "sandbox-secret:A_SECRET"),
        ("credential.resolve", "sandbox-secret:Z_SECRET"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("background", [False, True])
async def test_admitted_script_executes_only_verified_capture_bytes(background: bool) -> None:
    captured = _captured_script()
    handle = ExecutionHandle(
        "invocation", "portable", "native", None, "session", "workflow", "1.0.0", "fingerprint"
    )
    handle._execution_lease = object()  # type: ignore[assignment]
    prepared: list[Any] = []
    identity = object()
    receipt = object()

    class Admission:
        def prepare(self, *args: Any, **kwargs: Any) -> Any:
            prepared.append((args, kwargs))
            return SimpleNamespace(identity=identity, receipt=receipt, source=captured.source)

    services = SimpleNamespace(
        recovery_definitions=SimpleNamespace(
            verify_admission=lambda actual: _script_admission_plan(captured)
        ),
        executable_source_admissions=Admission(),
    )

    class CaptureExecutor(SandboxWorkflowExecutor):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.captured_requests: list[SandboxExecutionRequest] = []

        async def _execute_sandbox(
            self, request: SandboxExecutionRequest, state: ExecutionState
        ) -> Any:
            self.captured_requests.append(request)
            return request.script_code

        async def _execute_background(
            self, request: SandboxExecutionRequest, state: ExecutionState
        ) -> Any:
            self.captured_requests.append(request)
            return request.script_code

    state = _state()
    state.metadata.update(
        {
            "_invocation_handle": handle,
            "_script_resources": {"/do/0": captured.as_value()},
        }
    )

    executor = CaptureExecutor(None, services=services)
    result = await executor._run_script(
        {"language": "python", "code": "untrusted inline code"}, state, background=background
    )

    assert result == captured.source.code
    assert state.metadata["_source_script_business_key"] == prepared[0][0][0].key
    assert prepared[0][0][2] == captured.source
    assert prepared[0][1]["allow_new"] is True
    assert executor.captured_requests[0].script_code == captured.source.code
    payload = prepared[0][0][1]
    if background:
        assert payload["request"]["execution_id"].startswith("job_")
    else:
        assert payload["execution_id"] == executor.captured_requests[0].execution_id


@pytest.mark.asyncio
async def test_background_run_fails_closed_without_admission_and_job_store() -> None:
    from open_workflow_agent.sandbox.executor import SandboxWorkflowExecutor

    with pytest.raises(SandboxPolicyError, match="background execution admission is unavailable"):
        await SandboxWorkflowExecutor(None)._execute_background(
            SandboxExecutionRequest("run", "shell", command="approved-command"), _state()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("prior", [False, True])
async def test_background_acceptance_reuses_prior_deadline_and_returns_input(prior: bool) -> None:
    from open_workflow_agent.sandbox.executor import _RECONCILE_EXPIRED_RETRY
    from open_workflow_agent.sandbox.jobs import SandboxJobStore

    data = {"result": [1, None]}
    handle = ExecutionHandle(
        invocation_id="invocation",
        engine="portable",
        engine_execution_reference="native",
        user_id=None,
        session_id="session",
        workflow_name="workflow",
        workflow_version="1.0.0",
        workflow_fingerprint="fingerprint",
    )
    handle._execution_lease = object()  # type: ignore[assignment]
    identity_state = _state()
    identity_state.data = data
    identity_state.metadata["_invocation_handle"] = handle

    class FakeStore(SandboxJobStore):
        def __init__(self) -> None:
            pass

        def get(self, execution_id: str) -> Any:
            if not prior:
                return None
            return SimpleNamespace(admission_json='{"deadline_at":123.0}')

        def profile_provider(self) -> Any:
            return SimpleNamespace(timeout_seconds=25)

        def admit_action(
            self,
            identity: Any,
            selected_handle: Any,
            request: Any,
            value: Any,
            **kwargs: Any,
        ) -> Any:
            assert selected_handle is handle
            assert value == data
            assert kwargs["deadline_at"] == (123.0 if prior else 125.0)
            receipt = SimpleNamespace(output=value)
            return SimpleNamespace(receipt=receipt), receipt

    class Sandbox:
        def validate_job_request(self, request: SandboxExecutionRequest) -> None:
            assert request.kind == "shell"

    services = SimpleNamespace(sandbox_jobs=FakeStore(), sandbox=Sandbox())
    executor = SandboxWorkflowExecutor(None, services=services)
    executor._absolute_clock = lambda: 100.0  # type: ignore[method-assign]
    request = SandboxExecutionRequest("caller-id", "shell", command="approved-command")
    expired_retry = _RECONCILE_EXPIRED_RETRY.set(prior)
    try:
        result = await executor._execute_background(request, identity_state)
    finally:
        _RECONCILE_EXPIRED_RETRY.reset(expired_retry)
    assert result is data


def test_supervisor_mutation_fence_rejects_missing_authority_without_bootstrapping(
    tmp_path: Path,
) -> None:
    profile = SandboxJobProfile("internal", "deployment", "revision", "a" * 64)
    launch = SandboxJobLaunch(
        lease=SandboxJobLease("job_" + "b" * 64, "c" * 64, "d" * 32, 1, 2_000_000_000),
        profile=profile,
        request=SandboxExecutionRequest("job", "shell", command="approved-command"),
        request_digest="e" * 64,
        deadline_at=2_000_000_100,
        submit_id="submit",
    )
    database = tmp_path / "empty-authority.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sentinel (value TEXT)")

    with pytest.raises(WorkflowExecutionError, match="authority is unavailable or inconsistent"):
        with backend_mutation_fence(
            database,
            launch,
            expected_profile=profile,
            purpose="submit",
        ):
            pytest.fail("missing original authority must not reach physical mutation")

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert tables == {"sentinel"}


def test_supervisor_launch_verification_fails_closed_on_missing_ledger_tables() -> None:
    profile = SandboxJobProfile("internal", "deployment", "revision", "a" * 64)
    launch = SandboxJobLaunch(
        lease=SandboxJobLease("job_" + "b" * 64, "c" * 64, "d" * 32, 1, 2_000_000_000),
        profile=profile,
        request=SandboxExecutionRequest("job", "shell", command="approved-command"),
        request_digest="e" * 64,
        deadline_at=2_000_000_100,
        submit_id="submit",
    )
    raw = sqlite3.connect(":memory:")
    try:
        with pytest.raises(
            WorkflowExecutionError, match="authority is unavailable or inconsistent"
        ):
            verify_backend_launch(
                StorageConnection(raw, "sqlite"), launch, expected_profile=profile
            )
        tables = raw.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        assert tables == []
    finally:
        raw.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("known", "expected_error", "expected"),
    [
        (None, "retry budget expired before a new action", None),
        (SimpleNamespace(status="pending"), "unresolved execution intent", None),
        (
            SimpleNamespace(status="completed", request_digest="wrong"),
            "request differs during expired retry recovery",
            None,
        ),
        (
            SimpleNamespace(
                status="completed",
                request_digest=None,
                error=None,
                output={"retained": True},
            ),
            None,
            {"retained": True},
        ),
    ],
)
async def test_expired_action_retry_reconciles_only_matching_completed_receipts(
    known: Any, expected_error: str | None, expected: Any
) -> None:
    from open_workflow_agent.effects import effect_request_digest

    payload = {"value": 1}
    digest = effect_request_digest("catalog", payload)
    if known is not None and known.status == "completed" and known.request_digest is None:
        known.request_digest = digest
    calls: list[Any] = []

    class Journal:
        def get(self, identity: Any) -> Any:
            calls.append(identity)
            return known

    executor = WorkflowExecutor(None, services=SimpleNamespace(effect_journal=Journal()))
    state = _state()

    async def dispatch() -> dict[str, bool]:
        raise AssertionError("expired recovery must not dispatch new business work")

    expired_retry = _RECONCILE_EXPIRED_RETRY.set(True)
    try:
        if expected_error is None:
            result = await executor._run_effect_scoped("catalog", payload, state, dispatch)
            assert result == expected
        else:
            with pytest.raises(WorkflowExecutionError, match=expected_error):
                await executor._run_effect_scoped("catalog", payload, state, dispatch)
    finally:
        _RECONCILE_EXPIRED_RETRY.reset(expired_retry)
    assert len(calls) == 1


class _Backend:
    def __init__(self, capabilities: dict[str, Any] | None = None) -> None:
        self._capabilities = capabilities or {"enabled": False, "backend": "fixture"}
        self.executed: list[SandboxExecutionRequest] = []
        self.cancelled: list[str] = []
        self.stopped = False

    async def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        self.executed.append(request)
        return SandboxExecutionResult(request.execution_id, 7, "out", "err", 0.25)

    async def cancel(self, execution_id: str) -> None:
        self.cancelled.append(execution_id)

    async def shutdown(self) -> None:
        self.stopped = True

    def capabilities(self) -> dict[str, Any]:
        return dict(self._capabilities)


@pytest.mark.asyncio
async def test_manager_forwards_execution_lifecycle_and_returns_isolated_capabilities() -> None:
    backend = _Backend({"enabled": True, "backend": "fixture"})
    manager = SandboxManager(backend)
    request = SandboxExecutionRequest("run-1", "script", script_language="python")

    result = await manager.execute(request)
    await manager.cancel("run-1")
    capabilities = manager.capabilities()
    capabilities["enabled"] = False
    await manager.shutdown()

    assert result.as_output() == {"code": 7, "stdout": "out", "stderr": "err"}
    assert backend.executed == [request]
    assert backend.cancelled == ["run-1"]
    assert backend.stopped
    assert backend.capabilities()["enabled"] is True
    assert manager.capabilities()["awaitedReturn"]["default"] == "stdout"
    assert manager.capabilities()["backgroundExecution"] is False


@pytest.mark.asyncio
async def test_manager_rejects_stdio_on_non_internal_backend() -> None:
    manager = SandboxManager(_Backend())
    request = object()
    with pytest.raises(SandboxPolicyError, match="does not support MCP stdio"):
        async with manager.open_stdio(request):  # type: ignore[arg-type]
            pytest.fail("unsupported backend must not open a stdio session")


def _stdio_config(**overrides: Any) -> SandboxConfig:
    values: dict[str, Any] = {
        "enabled": True,
        "file_size_bytes": None,
        "mcp_stdio": McpStdioConfig(
            enabled=True,
            servers=[
                {
                    "id": "fixture",
                    "command": str(Path("fixture-server.exe").resolve()),
                    "arguments": ["--stable"],
                    "environment": {"MODE": "test"},
                }
            ],
        ),
    }
    values.update(overrides)
    return SandboxConfig(**values)


def test_stdio_admission_returns_only_matching_deployment_binding() -> None:
    config = _stdio_config()
    selected = validate_stdio_transport(
        {
            "stdio": {
                "command": config.mcp_stdio.servers[0].command,
                "arguments": ["--stable"],
            }
        },
        config,
    )
    assert selected.command == config.mcp_stdio.servers[0].command

    with pytest.raises(SandboxPolicyError, match="environment does not match"):
        validate_stdio_transport(
            {
                "stdio": {
                    "command": config.mcp_stdio.servers[0].command,
                    "arguments": ["--stable"],
                    "environment": {"MODE": "other"},
                }
            },
            config,
        )


@pytest.mark.parametrize(
    "transport",
    [
        None,
        {"http": {"url": "https://example.invalid"}},
        {
            "stdio": {
                "command": str(Path("fixture-server.exe").resolve()),
                "arguments": ["${INJECT}"],
            }
        },
        {
            "stdio": {
                "command": str(Path("fixture-server.exe").resolve()),
                "arguments": ["--stable"],
                "extra": True,
            }
        },
    ],
)
def test_stdio_admission_rejects_non_fixed_or_unknown_transport_fields(transport: Any) -> None:
    with pytest.raises(SandboxPolicyError):
        validate_stdio_transport(transport, _stdio_config())


def test_stdio_admission_rejects_disabled_or_unbound_deployments() -> None:
    config = _stdio_config()
    with pytest.raises(SandboxPolicyError, match="enabled internal"):
        validate_stdio_transport(
            {"stdio": {"command": config.mcp_stdio.servers[0].command}},
            _stdio_config(enabled=False),
        )
    with pytest.raises(SandboxPolicyError, match="not deployment-approved"):
        validate_stdio_transport(
            {"stdio": {"command": str(Path("other-server.exe").resolve()), "arguments": []}}, config
        )
