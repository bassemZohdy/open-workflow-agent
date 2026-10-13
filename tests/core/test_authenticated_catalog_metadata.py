"""Actual TLS selected-credential metadata rejection before immutable capture."""

from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
from _credential_catalog_peer import credential_catalog_peer
from open_workflow_agent.catalog import FakeModel, FunctionCatalog
from open_workflow_agent.config import ExternalCatalogConfig, RuntimeConfig
from open_workflow_agent.credential_guard import active_projection_guard
from open_workflow_agent.errors import UnsupportedWorkflowFeature
from open_workflow_agent.external_catalog import ExternalCatalogResolver
from open_workflow_agent.observability import InMemoryEventSink
from open_workflow_agent.protocols import HttpClient
from open_workflow_agent.security import ProfileAuthentication, SecurityConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import resolve_and_compile_workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["body", "validator_header"])
async def test_actual_authenticated_metadata_echo_never_enters_cache_or_captured_definition(
    tmp_path, monkeypatch, where
):
    secret = "controlled-private-catalog-token-684"
    monkeypatch.setenv("OWA_METADATA_GUARD_TOKEN", secret)
    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "metadata": {
                    "type": "bearer",
                    "principal": "catalog-reader",
                    "token": {"from_env": "OWA_METADATA_GUARD_TOKEN"},
                }
            }
        }
    )
    with credential_catalog_peer(tmp_path, secret) as (peer, origin, ca):
        plain = (
            b"call: http\nwith:\n  endpoint: https://business.test/echo\n"
            b"  method: post\n  body: {ok: true}\n"
        )
        peer["body"] = (
            plain.replace(b"{ok: true}", json.dumps({"echo": secret}).encode())
            if where == "body"
            else plain
        )
        peer["etag"] = secret if where == "validator_header" else "controlled-function"
        policy = ExternalCatalogConfig(
            allowed_hosts=["localhost", "business.test"],
            cache_ttl_seconds=0,
            integrity_pins={"echo:1.0.0@trusted": hashlib.sha256(peer["body"]).hexdigest()},
        )
        events = InMemoryEventSink()
        resolver = ExternalCatalogResolver(
            {"trusted": policy}, security=security, event_sink=events
        )
        # Explicit local test destination/private CA: proves real authenticated
        # wire I/O and literal rejection, not permission to bypass production SSRF.
        auth = ProfileAuthentication(security, "metadata")

        async def local_destination(endpoint):
            assert endpoint.startswith(origin + "/")
            return ("127.0.0.1",)

        def client(*args, **kwargs):
            return HttpClient(
                authentication=auth,
                ca_bundle=ca,
                allowed_hosts={"localhost"},
                destination_resolver=local_destination,
                timeout=5,
                max_response_bytes=65536,
            )

        monkeypatch.setattr(resolver, "_client", client)
        source = {
            "document": {
                "dsl": "1.0.3",
                "namespace": "metadata",
                "name": "guard",
                "version": "1.0.0",
            },
            "use": {"catalogs": {"trusted": {"endpoint": {"uri": origin + "/root"}}}},
            "do": [{"remote": {"call": "echo:1.0.0@trusted"}}],
        }
        services = RuntimeServices(
            RuntimeConfig(model={"provider": "fake"}),
            model=FakeModel(),
            database_root=tmp_path / "common",
        )
        catalog = services.catalog
        tables = (
            "recovery_definitions",
            "recovery_invocation_definitions",
            "invocations",
            "workflow_effects",
        )

        def snapshot():
            return {
                table: services.invocations.connection.execute("SELECT * FROM " + table).fetchall()
                for table in tables
            }

        before = snapshot()
        try:
            async with asyncio.timeout(10):
                with pytest.raises(UnsupportedWorkflowFeature) as error:
                    await resolve_and_compile_workflow(
                        source,
                        trusted_catalogs=resolver.policies,
                        resolver=resolver,
                        catalog=catalog,
                    )
            assert snapshot() == before and services.model.calls == []
        finally:
            services.close()
        assert len(peer["requests"]) == 1 and peer["requests"][0][1] == "Bearer " + secret
        assert (
            resolver._cache == {}
            and resolver._registered_bindings == {}
            and not catalog.has("echo:1.0.0@trusted")
        )
        assert secret not in json.dumps(error.value.as_dict()) and secret not in repr(events.events)
        assert active_projection_guard() is None


@pytest.mark.asyncio
async def test_authenticated_safe_metadata_pin_retains_original_wire_bytes(tmp_path, monkeypatch):
    secret = "controlled-private-catalog-token-963"
    monkeypatch.setenv("OWA_METADATA_GUARD_TOKEN", secret)
    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "metadata": {"type": "bearer", "token": {"from_env": "OWA_METADATA_GUARD_TOKEN"}}
            }
        }
    )
    with credential_catalog_peer(tmp_path, secret) as (peer, origin, ca):
        raw = (
            b"# exact original bytes\ncall: http\nwith:\n  method: post\n"
            b"  endpoint: https://business.test/echo\n  body: {ok: true}\n"
        )
        peer["body"] = raw
        digest = hashlib.sha256(raw).hexdigest()
        policy = ExternalCatalogConfig(
            allowed_hosts=["localhost", "business.test"],
            integrity_pins={"echo:1.0.0@trusted": digest},
        )
        resolver = ExternalCatalogResolver({"trusted": policy}, security=security)
        auth = ProfileAuthentication(security, "metadata")

        async def local_destination(endpoint):
            assert endpoint.startswith(origin + "/")
            return ("127.0.0.1",)

        monkeypatch.setattr(
            resolver,
            "_client",
            lambda *args, **kwargs: HttpClient(
                authentication=auth,
                ca_bundle=ca,
                allowed_hosts={"localhost"},
                destination_resolver=local_destination,
                timeout=5,
            ),
        )
        source = {
            "document": {
                "dsl": "1.0.3",
                "namespace": "metadata",
                "name": "safe",
                "version": "1.0.0",
            },
            "use": {"catalogs": {"trusted": {"endpoint": {"uri": origin + "/root"}}}},
            "do": [{"remote": {"call": "echo:1.0.0@trusted"}}],
        }
        async with asyncio.timeout(10):
            bindings = await resolver.resolve_functions(source, FunctionCatalog())
        assert len(bindings) == 1 and bindings[0].digest == digest and len(peer["requests"]) == 1
        assert bindings[0].definition["with"]["body"] == {"ok": True}
        assert secret not in bindings[0].definition_json and active_projection_guard() is None
