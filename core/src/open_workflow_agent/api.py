"""HTTP API independent of the selected execution engine."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .config import RuntimeConfig
from .engine import PortableWorkflowEngine, WorkflowEngine
from .errors import OwaError
from .services import RuntimeServices
from .workflow import compile_workflow


class InvokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str | None = None
    session_id: str | None = None
    input: Any = Field(default_factory=dict)


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: Any = Field(default_factory=dict)


def create_app(
    *,
    config: RuntimeConfig | None = None,
    engine: WorkflowEngine | None = None,
    services: RuntimeServices | None = None,
) -> FastAPI:
    runtime_config = config or RuntimeConfig()
    runtime_services = services or RuntimeServices(runtime_config)
    runtime_engine = engine or PortableWorkflowEngine()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await runtime_engine.initialize(runtime_services)
        workflow_source: Any = runtime_config.workflow.definition
        if workflow_source is None and runtime_config.workflow.path:
            workflow_source = runtime_config.workflow.path
        app.state.plan = compile_workflow(workflow_source)
        if runtime_config.knowledge.reload.mode == "startup":
            runtime_services.knowledge.reload()
        elif runtime_config.knowledge.reload.mode == "watch":
            await runtime_services.knowledge.start_watch(
                runtime_config.knowledge.reload.interval_seconds
            )
        try:
            yield
        finally:
            await runtime_services.knowledge.stop_watch()
            await runtime_engine.shutdown()
            if services is None:
                runtime_services.close()

    app = FastAPI(title="Open Workflow Agent", version="0.1.0", lifespan=lifespan)
    app.state.config = runtime_config
    app.state.services = runtime_services
    app.state.engine = runtime_engine

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/capabilities")
    async def capabilities() -> dict[str, Any]:
        return runtime_engine.capabilities().as_dict()

    @app.post("/v1/invoke")
    async def invoke(request: InvokeRequest) -> dict[str, Any]:
        plan = getattr(app.state, "plan", compile_workflow())
        handle = runtime_services.invocations.create(
            engine=runtime_engine.engine_name,
            session_id=request.session_id,
            user_id=request.user_id,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        result = await runtime_engine.invoke(plan, handle, request.input)
        if result.status == "faulted":
            raise HTTPException(status_code=500, detail=result.error)
        return result.as_dict()

    @app.post("/v1/invocations/{invocation_id}/resume")
    async def resume(invocation_id: str, request: ResumeRequest) -> dict[str, Any]:
        handle = runtime_services.invocations.get(invocation_id)
        if handle is None:
            raise HTTPException(status_code=404, detail="invocation not found")
        plan = getattr(app.state, "plan", compile_workflow())
        try:
            result = await runtime_engine.resume(handle, request.input, plan)
        except OwaError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from exc
        return result.as_dict()

    @app.post("/v1/admin/knowledge/reload")
    async def reload_knowledge() -> dict[str, Any]:
        return runtime_services.knowledge.reload()

    return app


def build_app_from_environment() -> FastAPI:
    return create_app(config=RuntimeConfig.from_file())
