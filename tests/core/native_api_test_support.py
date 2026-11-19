"""Real common storage/authority with a nonexecuting engine for API unit tests."""

from __future__ import annotations

from contextlib import contextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.engine import EngineCapabilities, WorkflowEngine
from open_workflow_agent.errors import InvocationNotFound, OwaError
from open_workflow_agent.execution_authority import ExecutionAuthority, authority_scope
from open_workflow_agent.native_extensions import LANGGRAPH_STREAMS
from open_workflow_agent.security import static_principal
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


class BoundaryEngine(WorkflowEngine):
    """Never invokes native work; only supplies the common ownership context."""

    extensions = (LANGGRAPH_STREAMS,)

    def capabilities(self):
        return EngineCapabilities(engine="boundary-test", native_extensions=self.extensions)


@contextmanager
def runtime(tmp_path):
    config = RuntimeConfig.model_validate(
        {
            "server": {"api_security_profile": "alice"},
            "security": {
                "profiles": {
                    name: {
                        "type": "bearer",
                        "principal": name,
                        "roles": ["operator"],
                        "token": {"from_env": "OWA_BOUNDARY_UNUSED"},
                    }
                    for name in ("alice", "bob")
                }
            },
            "execution_authorization": {
                "enabled": True,
                "id": "boundary",
                "version": "1",
                "policy": {
                    "rules": [
                        {
                            "actions": [
                                "invocations.stream",
                                "memory.inspect",
                                "memory.resolve",
                                "catalog.call",
                                "memory.add",
                                "state.inspect",
                                "state.update",
                                "state.resolve",
                            ],
                            "resources": ["*"],
                            "roles": ["operator"],
                        }
                    ]
                },
            },
            "memory": {"native": [{"id": "notes", "collection": "notes"}]},
            "observability": {"native_streams": {"max_subscribers": 1}},
            "native_state": {"timeout_seconds": 0.1},
            "native_state_updates": {},
        }
    )
    services = RuntimeServices(
        config,
        model=FakeModel(),
        database_root=tmp_path,
    )
    engine = BoundaryEngine()
    engine.services = services
    services.recovery_definitions.profile_provider = lambda name: {"engine": name, "test": True}
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "boundary",
                "version": "1.0.0",
            },
            "do": [{"noop": {"set": {"ok": True}}}],
        }
    )
    handle = services.invocations.create(
        engine="boundary-test",
        session_id=None,
        user_id=None,
        owner_principal="alice",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    authorities = {
        name: ExecutionAuthority.capture(
            static_principal(config.security.profile(name)), security_profile=name
        )
        for name in ("alice", "bob")
    }
    lease = services.execution_ownership.acquire(handle, seconds=300)
    handle._execution_lease = lease
    with authority_scope(authorities["alice"]):
        services.recovery_definitions.admit(plan, handle, {}, execution_lease=lease)
    services.execution_ownership.release(lease)
    handle._execution_lease = None
    app = FastAPI()

    @app.exception_handler(OwaError)
    async def common_error(_request, error):
        return JSONResponse(status_code=error.status_code, content={"error": error.as_dict()})

    def invocation(invocation_id: str, _request: Request):
        current = services.invocations.get(invocation_id)
        if current is None:
            raise InvocationNotFound("invocation not found")
        return current

    calls = []

    def authorize(action, resource):
        from open_workflow_agent.execution_authority import current_authority

        calls.append((action, resource))
        services.execution_authorizations.authorize_request(current_authority(), action, resource)

    try:
        yield services, engine, handle, authorities, app, authorize, invocation, calls
    finally:
        services.close()
