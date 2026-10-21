from __future__ import annotations

import asyncio
import json

import pytest
from open_workflow_agent.catalog import CatalogContext, FakeModel, ManagedFunctionCapabilities
from open_workflow_agent.errors import ConfigurationError, ToolError, WorkflowExecutionError
from open_workflow_agent.knowledge import (
    DeterministicEmbeddingProvider,
    KnowledgeService,
    SentenceTransformerEmbeddingProvider,
)
from open_workflow_agent.memory import MemoryService
from open_workflow_agent.persistence import InvocationStore
from open_workflow_agent.workflow import compile_workflow


def test_runtime_services_start_without_knowledge_extra(tmp_path, monkeypatch):
    import open_workflow_agent.knowledge as knowledge_module
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.services import RuntimeServices

    monkeypatch.setattr(knowledge_module, "NUMPY_AVAILABLE", False)
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    services.close()


def test_memory_add_search_delete(tmp_path):
    memory = MemoryService(str(tmp_path / "memory.sqlite3"))
    identifier = memory.add("license renewal policy", {"source": "policy"})
    assert memory.search("renewal")[0]["id"] == identifier
    assert memory.delete(identifier) is True
    assert memory.search("renewal") == []
    memory.close()


def test_memory_without_database_has_stable_ids_and_delete_result():
    memory = MemoryService()
    first = memory.add("first")
    second = memory.add("second")
    assert [item["id"] for item in memory.search("")] == [first, second]
    assert memory.delete(first) is True
    assert memory.delete(first) is False
    assert [item["id"] for item in memory.search("")] == [second]
    memory.close()


def test_knowledge_manifest_skips_unchanged_documents(tmp_path):
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "policy.md").write_text("License renewal requires an application.", encoding="utf-8")
    knowledge = KnowledgeService(
        root, tmp_path / "knowledge.sqlite3", embedding=DeterministicEmbeddingProvider()
    )
    assert knowledge.reload()["added"] == 1
    assert knowledge.reload()["unchanged"] == 1
    assert knowledge.search("renewal")[0]["path"].endswith("policy.md")
    (root / "policy.md").unlink()
    assert knowledge.reload()["deleted"] == 1
    knowledge.close()


def test_knowledge_manifest_records_parser_chunking_and_timestamp(tmp_path):
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "policy.md").write_text("License renewal policy.", encoding="utf-8")
    (root / "data.json").write_text('{"key": "value"}', encoding="utf-8")
    knowledge = KnowledgeService(
        root,
        tmp_path / "knowledge.sqlite3",
        chunk_size=64,
        chunk_overlap=8,
        embedding=DeterministicEmbeddingProvider(),
    )
    assert knowledge.reload()["added"] == 2
    rows = {
        row[0]: row
        for row in knowledge.connection.execute(
            "SELECT path, parser, chunking, indexed_at FROM manifest"
        )
    }
    markdown_row = rows[str(root / "policy.md")]
    json_row = rows[str(root / "data.json")]
    assert markdown_row[1] == "text"
    assert json_row[1] == "stdlib-json"
    for row in (markdown_row, json_row):
        # Chunking identity records the real algorithm and parameters, not just
        # the chunk size, so upgrades remain auditable.
        assert row[2] == "whitespace-window:64+8"
        assert row[3] and row[3].endswith("Z")
    knowledge.close()


def test_knowledge_manifest_migration_adds_indexed_at(tmp_path):
    import sqlite3

    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "policy.md").write_text("License renewal policy.", encoding="utf-8")
    database = tmp_path / "knowledge.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE manifest (path TEXT PRIMARY KEY, hash TEXT, parser TEXT, "
        "chunking TEXT, embedding TEXT)"
    )
    connection.execute(
        "INSERT INTO manifest VALUES "
        "('/stale/policy.md', 'digest', '.md', '400', 'deterministic:8')"
    )
    connection.commit()
    connection.close()

    knowledge = KnowledgeService(
        database=database, root=root, embedding=DeterministicEmbeddingProvider()
    )
    counts = knowledge.reload()
    assert counts["added"] == 1
    assert counts["deleted"] == 1
    stale = knowledge.connection.execute(
        "SELECT indexed_at FROM manifest WHERE path = '/stale/policy.md'"
    ).fetchone()
    assert stale is None
    fresh = knowledge.connection.execute(
        "SELECT indexed_at FROM manifest WHERE path LIKE '%policy.md' "
        "AND path != '/stale/policy.md'"
    ).fetchone()
    assert fresh is not None and fresh[0]
    knowledge.close()


def test_knowledge_reindexes_when_embedding_identity_changes(tmp_path):
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "policy.md").write_text("License renewal policy.", encoding="utf-8")
    database = tmp_path / "knowledge.sqlite3"
    first = KnowledgeService(root, database, embedding=DeterministicEmbeddingProvider(8))
    assert first.reload()["added"] == 1
    first.close()
    second = KnowledgeService(root, database, embedding=DeterministicEmbeddingProvider(16))
    assert second.reload()["updated"] == 1
    second.close()


def test_knowledge_watch_can_start_and_stop(tmp_path):
    async def run():
        knowledge = KnowledgeService(
            tmp_path / "knowledge",
            tmp_path / "knowledge.sqlite3",
            embedding=DeterministicEmbeddingProvider(),
        )
        await knowledge.start_watch(1)
        await knowledge.stop_watch()
        knowledge.close()

    asyncio.run(run())


def test_pinned_embedding_provider_is_injectable_and_records_identity():
    class FakeFastEmbed:
        def embed(self, values):
            assert values == ["hello"]
            yield [1.0, 0.0, 0.0]

    provider = SentenceTransformerEmbeddingProvider(model=FakeFastEmbed())
    assert provider.identity.endswith("@ea78891063587eb050ed4166b20062eaf978037c")
    assert provider.embed("hello").tolist() == [1.0, 0.0, 0.0]


def test_invocation_fingerprint_is_persisted_and_checked(tmp_path):
    store = InvocationStore(tmp_path / "runtime.sqlite3")
    handle = store.create(
        engine="test",
        session_id="s1",
        user_id="u1",
        workflow_name="w",
        workflow_version="1",
        workflow_fingerprint="abc",
    )
    assert store.get(handle.invocation_id).session_id == "s1"
    store.verify_fingerprint(handle, "abc")
    store.close()


def test_tools_are_external_configuration_not_workflow_state(tmp_path):
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.services import RuntimeServices

    config = RuntimeConfig.model_validate(
        {"tools": [{"type": "mcp", "name": "catalog", "endpoint": "http://localhost:9000"}]}
    )
    services = RuntimeServices(config, database_root=tmp_path)
    assert services.tools.names() == ("catalog",)
    assert services.agent_tools == (
        "search_knowledge",
        "add_memory",
        "search_memory",
        "delete_memory",
        "catalog",
    )
    services.close()


def test_managed_catalog_context_scopes_runtime_services(tmp_path):
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.services import RuntimeServices

    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    try:
        context = CatalogContext(model=services.model, services=services)
        assert isinstance(context.services, ManagedFunctionCapabilities)
        assert context.services.agent_tools == services.agent_tools
        assert not hasattr(context.services, "config")
        assert not hasattr(context.services, "sandbox")
        assert not hasattr(context.services, "memory")
        assert (
            asyncio.run(context.services.invoke_agent_tool("search_knowledge", {"query": ""})) == []
        )
        with pytest.raises(ToolError, match="not approved"):
            asyncio.run(context.services.invoke_agent_tool("arbitrary-subprocess", {}))
    finally:
        services.close()


def test_configured_sqlite_datasource_is_shared_by_durable_services(tmp_path):
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.services import RuntimeServices

    database = tmp_path / "runtime.sqlite3"
    config = RuntimeConfig.model_validate(
        {"persistence": {"datasource": f"sqlite:///{database.as_posix()}"}}
    )
    services = RuntimeServices(config, model=FakeModel())
    identifier = services.memory.add("durable datasource memory")
    plan = compile_workflow()
    handle = services.invocations.create(
        engine="test",
        session_id="session",
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    assert services.memory.search("durable")[0]["id"] == identifier
    assert services.invocations.get(handle.invocation_id) is not None
    services.close()

    reopened = RuntimeServices(config, model=FakeModel())
    assert reopened.memory.search("durable")[0]["id"] == identifier
    assert reopened.invocations.get(handle.invocation_id) is not None
    assert reopened.datasource == str(database)
    reopened.close()


def test_unsupported_datasource_is_not_silently_treated_as_sqlite():
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.services import RuntimeServices

    config = RuntimeConfig.model_validate(
        {"persistence": {"datasource": "postgresql://example.invalid/owa"}}
    )
    try:
        RuntimeServices(config)
    except ConfigurationError as exc:
        assert exc.details["scheme"] == "postgresql"
    else:
        raise AssertionError("unsupported datasource was accepted")


def test_sqlite_absolute_url_normalizes_posix_root():
    import os

    if os.name == "nt":
        return
    from open_workflow_agent.storage import resolve_datasource

    assert resolve_datasource("sqlite:////tmp/owa-runtime.sqlite3") == "/tmp/owa-runtime.sqlite3"


def test_windows_path_is_treated_as_local_sqlite_datasource():
    from open_workflow_agent.storage import datasource_kind, resolve_datasource

    datasource = r"C:\owa\runtime.sqlite3"
    assert datasource_kind(datasource) == "sqlite"
    assert resolve_datasource(datasource) == datasource


def test_runtime_services_role_memory_preflight_accepts_bounded_payloads(tmp_path):
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.services import RuntimeServices

    services = RuntimeServices(
        RuntimeConfig.model_validate(
            {
                "memory": {
                    "bounds": {
                        "enabled": True,
                        "max_text_bytes": 32,
                        "max_query_bytes": 32,
                        "max_query_terms": 2,
                        "max_results": 5,
                    }
                }
            }
        ),
        model=FakeModel(),
        database_root=tmp_path,
    )
    accepted = {
        "add_memory": {"text": "approved note", "metadata": {"source": "role"}},
        "search_memory": {"query": "approved note", "limit": 5},
        "delete_memory": {"id": 7},
    }
    try:
        for name, payload in accepted.items():
            services.validate_agent_tool_payload(name, payload)
        assert (
            services.memory._connection.execute(
                "SELECT COUNT(*) FROM bounded_memory_entries"
            ).fetchone()[0]
            == 0
        )
        assert (
            services.memory._connection.execute(
                "SELECT COUNT(*) FROM bounded_memory_namespaces"
            ).fetchone()[0]
            == 0
        )
    finally:
        services.close()


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("add_memory", {"text": "private", "namespace": "another-principal"}),
        ("add_memory", {"text": True}),
        ("add_memory", {"text": "x" * 33}),
        ("add_memory", {"text": "private", "metadata": ["not", "an", "object"]}),
        ("search_memory", {"query": "one two three"}),
        ("search_memory", {"query": "x" * 33}),
        ("search_memory", {"query": "one", "limit": True}),
        ("search_memory", {"query": "one", "limit": 0}),
        ("search_memory", {"query": "one", "limit": 6}),
        ("search_memory", {"query": "one", "principal": "another-principal"}),
        ("delete_memory", {"id": True}),
        ("delete_memory", {"id": 0}),
        ("delete_memory", {"id": 2**63}),
        ("delete_memory", {"id": 7, "namespace": "another-principal"}),
    ],
)
def test_runtime_services_rejects_invalid_role_memory_payloads_without_mutation(
    tmp_path, name, payload
):
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.services import RuntimeServices

    services = RuntimeServices(
        RuntimeConfig.model_validate(
            {
                "memory": {
                    "bounds": {
                        "enabled": True,
                        "max_text_bytes": 32,
                        "max_query_bytes": 32,
                        "max_query_terms": 2,
                        "max_results": 5,
                    }
                }
            }
        ),
        model=FakeModel(),
        database_root=tmp_path,
    )
    connection = services.memory._connection
    before = (
        connection.execute(
            "SELECT * FROM bounded_memory_entries ORDER BY namespace,memory_id"
        ).fetchall(),
        connection.execute("SELECT * FROM bounded_memory_namespaces ORDER BY namespace").fetchall(),
    )
    try:
        with pytest.raises(WorkflowExecutionError):
            services.validate_agent_tool_payload(name, payload)
        after = (
            connection.execute(
                "SELECT * FROM bounded_memory_entries ORDER BY namespace,memory_id"
            ).fetchall(),
            connection.execute(
                "SELECT * FROM bounded_memory_namespaces ORDER BY namespace"
            ).fetchall(),
        )
        assert after == before
    finally:
        services.close()


def test_runtime_services_recovery_profile_composes_enabled_policies_without_secrets(
    tmp_path, monkeypatch
):
    import open_workflow_agent.services as services_module
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.services import RuntimeServices

    secret = "must-never-appear-in-recovery-profile"
    monkeypatch.setenv("OWA_TEST_RUNTIME_MODEL_SECRET", secret)
    monkeypatch.setattr(
        services_module,
        "recovery_profile",
        lambda engine, **_kwargs: {
            "profile": "test-only/runtime-services-composition",
            "engine": engine,
            "continuation_supported": True,
            "policy": {"physical_marker": "kept"},
        },
    )
    config = RuntimeConfig.model_validate(
        {
            "model": {
                "credentials": {"api_key": {"from_env": "OWA_TEST_RUNTIME_MODEL_SECRET"}},
                "bounds": {"id": "completion-test", "version": "2"},
            },
            "agent": {
                "roles": {
                    "drafter": {
                        "id": "drafter-test",
                        "model": {"name": "fake/drafter"},
                        "tools": ["search_memory"],
                    }
                }
            },
            "memory": {"bounds": {"enabled": True, "id": "memory-test", "version": "3"}},
        }
    )
    services = RuntimeServices(
        config,
        model=FakeModel(),
        database_root=tmp_path,
        embedded_event_transport=True,
    )
    try:
        profile = services._recovery_profile("reference")
        policy = profile["policy"]
        serialized = json.dumps(profile, sort_keys=True)
        assert policy["physical_marker"] == "kept"
        assert policy["memory_bounds"]["id"] == "memory-test"
        assert policy["model"]["bounds"]["id"] == "completion-test"
        assert policy["model"]["credentials"] == {
            "api_key": {"from_env": "OWA_TEST_RUNTIME_MODEL_SECRET"}
        }
        assert policy["agent_roles"] == services.role_bindings.snapshot()
        assert policy["event_transport"]["profile"] == "owa/embedded-event-transport/v1"
        assert "approvals" in policy
        assert secret not in serialized
    finally:
        services.close()


@pytest.mark.parametrize(
    "declaration",
    [
        None,
        {},
        {"id": "bad id", "version": "1"},
        {"id": "runtime", "version": ""},
        {"id": "runtime", "version": "1", "secret": "must-not-be-retained"},
    ],
    ids=["not-object", "missing-fields", "invalid-id", "invalid-version", "extra-field"],
)
def test_runtime_services_rejects_invalid_recovery_binding_declarations(
    tmp_path, monkeypatch, declaration
):
    import open_workflow_agent.services as services_module
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.services import RuntimeServices

    monkeypatch.setattr(
        services_module,
        "recovery_profile",
        lambda engine, **_kwargs: {
            "profile": "test-only/physical-recovery-evidence",
            "engine": engine,
            "continuation_supported": True,
            "policy": {},
        },
    )
    services = RuntimeServices(
        RuntimeConfig(),
        model=FakeModel(),
        database_root=tmp_path,
        recovery_profile_provider=lambda _engine: declaration,
    )
    try:
        with pytest.raises(ConfigurationError, match="declaration is invalid"):
            services._recovery_profile("test")
    finally:
        services.close()


def test_runtime_services_recovery_profile_records_deployed_approval_actor_identity(
    tmp_path, monkeypatch
):
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.services import RuntimeServices

    monkeypatch.setenv("OWA_TEST_APPROVAL_ACTOR_TOKEN", "never-retain-this-token")
    config = RuntimeConfig.model_validate(
        {
            "security": {
                "profiles": {
                    "reviewer": {
                        "type": "bearer",
                        "principal": "reviewer-identity",
                        "roles": ["approver", "auditor"],
                        "scopes": ["approve"],
                        "audience": ["workflow"],
                        "token": {"from_env": "OWA_TEST_APPROVAL_ACTOR_TOKEN"},
                    }
                }
            },
            "approvals": {
                "enabled": True,
                "protected": True,
                "actor_security_profiles": ["reviewer"],
            },
        }
    )
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    try:
        profile = services._compose_recovery_profile("test", {})
        serialized = json.dumps(profile, sort_keys=True)
        assert profile["policy"]["approvals"]["actorBindings"] == [
            {
                "profile": "reviewer",
                "principal": "reviewer-identity",
                "roles": ["approver", "auditor"],
                "scopes": ["approve"],
                "audience": ["workflow"],
            }
        ]
        assert "never-retain-this-token" not in serialized
    finally:
        services.close()


@pytest.mark.asyncio
async def test_runtime_services_async_recovery_profile_rejects_concurrent_binding_change(
    tmp_path, monkeypatch
):
    import threading

    import open_workflow_agent.services as services_module
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.services import RuntimeServices

    started = threading.Event()
    release = threading.Event()

    def slow_profile(_engine, **_kwargs):
        started.set()
        assert release.wait(5), "test did not release the bounded profile worker"
        return {"profile": "test-only/physical"}

    monkeypatch.setattr(services_module, "recovery_profile", slow_profile)
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    pending = asyncio.create_task(services._recovery_profile_async("test"))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        services.config = RuntimeConfig.model_validate({"agent": {"instruction": "changed"}})
        release.set()
        with pytest.raises(ConfigurationError, match="binding changed during verification"):
            await pending
        await asyncio.sleep(0)
        assert services._physical_profile_slots._value == 2
    finally:
        release.set()
        if not pending.done():
            await pending
        services.close()


def test_runtime_services_caches_event_trigger_store_per_engine(tmp_path):
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.services import RuntimeServices

    services = RuntimeServices(
        RuntimeConfig.model_validate({"events": {"backend": "embedded"}}),
        model=FakeModel(),
        database_root=tmp_path,
    )
    try:
        first = services.event_triggers("adk")
        assert first is not None
        assert services.event_triggers("adk") is first
        assert services.event_triggers("langgraph") is not first
        assert services.event_triggers("langgraph", clock=lambda: 42.0) is services.event_triggers(
            "langgraph"
        )
        assert services.event_triggers("adk").clock() != 42.0
    finally:
        services.close()


def test_runtime_services_rejects_protocol_authorization_hook_replacement(tmp_path):
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.services import RuntimeServices

    services = RuntimeServices(
        RuntimeConfig.model_validate({"execution_authorization": {"enabled": True}}),
        model=FakeModel(),
        database_root=tmp_path,
    )
    try:
        services.protocols.authorize_destination = lambda *_args: None
        with pytest.raises(ConfigurationError, match="not deployment-bound"):
            services.verify_protocol_authorization_binding()
    finally:
        services.close()


def test_runtime_services_tool_validation_rejects_unknown_and_unbounded_shapes(tmp_path):
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.services import RuntimeServices

    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    try:
        with pytest.raises(ConfigurationError, match="not deployment-bound"):
            services.validate_agent_tool_payload("unregistered", {})
        with pytest.raises(ConfigurationError, match="requires a bounded object"):
            services.validate_agent_tool_payload("add_memory", "private")
        with pytest.raises(ConfigurationError, match="cannot choose its namespace"):
            services.validate_agent_tool_payload(
                "search_knowledge", {"query": "policy", "namespace": "another-user"}
            )
        with pytest.raises(ConfigurationError, match="input is invalid"):
            services.validate_agent_tool_payload("add_memory", {"text": True})
        with pytest.raises(ConfigurationError, match="input is invalid"):
            services.validate_agent_tool_payload("delete_memory", {"id": True})
        with pytest.raises(ConfigurationError, match="search tool input is invalid"):
            services.validate_agent_tool_payload("search_memory", {"query": "policy", "limit": 0})
    finally:
        services.close()


@pytest.mark.asyncio
async def test_runtime_services_optional_dispatchers_and_empty_retained_proofs_are_noops(
    tmp_path,
):
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.services import RuntimeServices

    services = RuntimeServices(
        RuntimeConfig.model_validate({"events": {"backend": "memory"}}),
        model=FakeModel(),
        database_root=tmp_path,
    )
    plan = compile_workflow()
    handle = services.invocations.create(
        engine="test",
        session_id="services-noop-session",
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    try:
        assert services.event_triggers("test") is None
        services.verify_background_jobs(handle)
        services.verify_protected_approvals(handle)
        await services.verify_executable_source_history(handle)
        services.verify_protected_approval_plan(plan)
        await services.start_protected_approvals()
        await services.stop_protected_approvals()
        await services.start_background_jobs()
        await services.stop_background_jobs()
        assert services.sandbox_jobs is None
        assert services._background_dispatcher is None
        assert services._approval_expiry_dispatcher is None
    finally:
        services.close()
