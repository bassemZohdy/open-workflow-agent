"""HTTP API independent of the selected execution engine."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .config import RuntimeConfig
from .engine import PortableWorkflowEngine, WorkflowEngine
from .errors import EventValidationError, InvocationNotFound, OwaError, ScheduleNotFound
from .scheduling import WorkflowScheduler
from .services import RuntimeServices
from .workflow import compile_workflow


class RequestSizeLimitMiddleware:
    """Buffer only invocation/resume requests and reject oversized bodies early."""

    def __init__(self, app: Any, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not _is_limited_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        declared = int(headers.get(b"content-length", b"0") or b"0")
        if declared > self.max_bytes:
            await _send_limit_error(scope, send, self.max_bytes)
            return
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            body = message.get("body", b"")
            total += len(body)
            if total > self.max_bytes:
                await _send_limit_error(scope, send, self.max_bytes)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        sent = False

        async def replay() -> dict[str, Any]:
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, send)


def _is_limited_path(path: str) -> bool:
    return path in {"/v1/invoke", "/v1/events", "/v1/schedules"} or (
        (path.startswith("/v1/invocations/") or path.startswith("/v1/schedules/"))
        and (path.endswith("/resume") or path.endswith("/cancel"))
    )


async def _send_limit_error(scope: dict[str, Any], send: Any, max_bytes: int) -> None:
    response = JSONResponse(
        status_code=413,
        content={
            "error": {
                "code": "request_too_large",
                "message": f"request body exceeds {max_bytes} bytes",
                "details": {"max_request_bytes": max_bytes},
            }
        },
    )

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    await response(scope, receive, send)


class InvokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str | None = None
    session_id: str | None = None
    input: Any = Field(default_factory=dict)


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: Any = Field(default_factory=dict)


class PublishEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: dict[str, Any]


class CreateScheduleRequest(BaseModel):
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
    scheduler = WorkflowScheduler(runtime_services, runtime_engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        app.state.ready = False
        try:
            await runtime_engine.initialize(runtime_services)
            workflow_source: Any = runtime_config.workflow.definition
            if workflow_source is None and runtime_config.workflow.path:
                workflow_source = runtime_config.workflow.path
            app.state.plan = compile_workflow(workflow_source)
            runtime_services.workflow_catalog.register(app.state.plan)
            for child_workflow in runtime_config.workflow.catalog:
                runtime_services.workflow_catalog.register(child_workflow)
            if runtime_config.knowledge.reload.mode == "startup":
                runtime_services.knowledge.reload()
            elif runtime_config.knowledge.reload.mode == "watch":
                await runtime_services.knowledge.start_watch(
                    runtime_config.knowledge.reload.interval_seconds
                )
            await scheduler.start()
            app.state.ready = True
            yield
        finally:
            await scheduler.stop()
            await runtime_services.knowledge.stop_watch()
            await runtime_engine.shutdown()
            if services is None:
                runtime_services.close()

    app = FastAPI(title="Open Workflow Agent", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        RequestSizeLimitMiddleware, max_bytes=runtime_config.server.max_request_bytes
    )

    @app.exception_handler(OwaError)
    async def owa_error(_request: Request, exc: OwaError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.as_dict()})

    @app.exception_handler(RequestValidationError)
    async def request_validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "request_validation_error",
                    "message": "request validation failed",
                    "details": {"errors": exc.errors()},
                }
            },
        )

    app.state.config = runtime_config
    app.state.services = runtime_services
    app.state.engine = runtime_engine
    app.state.scheduler = scheduler
    app.state.ready = False

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready() -> Any:
        if not app.state.ready:
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return {"status": "ok"}

    @app.get("/v1/capabilities")
    async def capabilities() -> dict[str, Any]:
        return runtime_engine.capabilities().as_dict()

    @app.post("/v1/events")
    async def publish_event(request: PublishEventRequest) -> dict[str, Any]:
        try:
            envelope = await runtime_services.event_bus.publish(
                request.event,
                default_source="urn:open-workflow-agent:api",
            )
        except ValueError as exc:
            raise EventValidationError(str(exc)) from exc
        return envelope.as_dict()

    @app.get("/v1/events/lifecycle")
    async def lifecycle_events(
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> JSONResponse:
        return JSONResponse(
            content=[
                event.as_dict() for event in runtime_services.lifecycle_events.snapshot(limit)
            ],
            media_type="application/cloudevents-batch+json",
        )

    @app.post("/v1/schedules")
    async def create_schedule(
        request: CreateScheduleRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        plan = getattr(app.state, "plan", compile_workflow())
        record = runtime_services.schedules.create(
            plan,
            request.input,
            operation_key=idempotency_key,
        )
        return record.as_dict()

    @app.get("/v1/schedules/{schedule_id}")
    async def get_schedule(schedule_id: str) -> dict[str, Any]:
        record = runtime_services.schedules.get(schedule_id)
        if record is None:
            raise ScheduleNotFound("schedule not found", details={"schedule_id": schedule_id})
        return record.as_dict()

    @app.post("/v1/schedules/{schedule_id}/cancel")
    async def cancel_schedule(
        schedule_id: str,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        try:
            record = await scheduler.cancel(schedule_id, operation_id=idempotency_key)
        except KeyError as exc:
            raise ScheduleNotFound(
                "schedule not found", details={"schedule_id": schedule_id}
            ) from exc
        return record.as_dict()

    @app.post("/v1/invoke")
    async def invoke(request: InvokeRequest) -> Any:
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
            error = result.error or {
                "code": "workflow_execution_error",
                "message": "invocation faulted",
                "details": {},
            }
            return JSONResponse(status_code=500, content={"error": error})
        return result.as_dict()

    @app.post("/v1/invocations/{invocation_id}/resume")
    async def resume(
        invocation_id: str,
        request: ResumeRequest,
    ) -> Any:
        handle = runtime_services.invocations.get(invocation_id)
        if handle is None:
            raise InvocationNotFound(
                "invocation not found", details={"invocation_id": invocation_id}
            )
        plan = getattr(app.state, "plan", compile_workflow())
        try:
            result = await runtime_engine.resume(handle, request.input, plan)
        except OwaError as exc:
            return JSONResponse(status_code=exc.status_code, content={"error": exc.as_dict()})
        return result.as_dict()

    @app.post("/v1/invocations/{invocation_id}/cancel")
    async def cancel(
        invocation_id: str,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> Any:
        handle = runtime_services.invocations.get(invocation_id)
        if handle is None:
            raise InvocationNotFound(
                "invocation not found", details={"invocation_id": invocation_id}
            )
        result = await runtime_engine.cancel(handle, operation_id=idempotency_key)
        return result.as_dict()

    @app.post("/v1/admin/knowledge/reload")
    async def reload_knowledge() -> dict[str, Any]:
        return runtime_services.knowledge.reload()

    return app


def build_app_from_environment() -> FastAPI:
    return create_app(config=RuntimeConfig.from_file())
