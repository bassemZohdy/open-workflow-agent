"""HTTP API independent of the selected execution engine."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ._version import __version__
from .a2a import (
    A2ASendOutcome,
    ResumeTarget,
    a2a_capabilities,
    extract_output_text,
    mount_a2a,
)
from .a2a_tasks import has_pending_event_listener, project_a2a_task
from .approvals import APPROVAL_STATES, ApprovalEventBus
from .config import RuntimeConfig
from .durable_events import DEFAULT_EVENT_SCOPE, DurableEventBus
from .engine import InvocationResult, PortableWorkflowEngine, WorkflowEngine
from .errors import (
    ApprovalAuthorizationError,
    ApprovalNotFound,
    ApprovalValidationError,
    EventValidationError,
    InvocationNotFound,
    OwaError,
    ScheduleNotFound,
    ScheduleValidationError,
)
from .event_capabilities import event_capabilities
from .execution_authority import ExecutionAuthority, authority_scope, current_authority
from .grpc_capabilities import grpc_capabilities
from .lifecycle import TERMINAL_INVOCATION_STATES
from .mcp_capabilities import mcp_capabilities
from .metrics import MetricsMiddleware
from .persistence import ExecutionHandle
from .recovery_diagnostics import recovery_diagnostics
from .sandbox import compile_sandbox_workflow, resolve_and_compile_sandbox_workflow
from .schedule_profile import parse_schedule
from .scheduling import ScheduleRecord, WorkflowScheduler
from .security import (
    ApiKeySecurityProfile,
    BearerSecurityProfile,
    Principal,
    authenticate_static,
    authorize,
    static_principal,
)
from .security_headers import SecurityHeadersMiddleware
from .services import RuntimeServices
from .streaming import StreamLimits, lifecycle_sse_stream, streaming_capabilities
from .traffic_policy import TrafficPolicyMiddleware, traffic_policy_capabilities
from .workflow import WorkflowPlan


def _trusted_event_scope(request: Request) -> str:
    principal = request.scope.get("owa.principal")
    return principal if isinstance(principal, str) and principal else DEFAULT_EVENT_SCOPE


class RequestSizeLimitMiddleware:
    """Buffer only mutation requests and reject oversized bodies early."""

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
        (
            path.startswith("/v1/invocations/")
            or path.startswith("/v1/schedules/")
            or path.startswith("/v1/approvals/")
        )
        and path.endswith(("/resume", "/cancel", "/decision", "/reassign", "/escalate"))
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


class ApiAuthenticationMiddleware:
    """Optional API authentication middleware for /v1/* endpoints."""

    def __init__(self, app: Any, *, security: Any, profile_name: str) -> None:
        self.app = app
        self.security = security
        self.profile_name = profile_name
        self.profile = security.profile(profile_name)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Only protect /v1/* endpoints (not health checks or docs)
        if not path.startswith("/v1/"):
            await self.app(scope, receive, send)
            return

        raw_headers = list(scope.get("headers", []))
        selected_header = (
            self.profile.header.lower().encode("ascii")
            if isinstance(self.profile, ApiKeySecurityProfile)
            else b"authorization"
        )
        present = any(
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[0], bytes)
            and item[0].lower() == selected_header
            for item in raw_headers
        )
        try:
            principal = authenticate_static(raw_headers, self.profile)
            authority = ExecutionAuthority.capture(principal, security_profile=self.profile_name)
        except (OwaError, ValueError, TypeError, UnicodeError):
            response = JSONResponse(
                status_code=403 if present else 401,
                content={
                    "error": {
                        "code": "authentication_failed" if present else "authentication_required",
                        "message": "API authentication failed"
                        if present
                        else "API authentication is required",
                        "details": {},
                    }
                },
            )
            await response(scope, receive, send)
            return
        scope["owa.principal"] = principal.identity
        scope["owa.execution_authority"] = authority
        with authority_scope(authority):
            await self.app(scope, receive, send)


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


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    value: Any = None
    revision: int | None = Field(default=None, ge=1, le=129)


class ApprovalAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignee: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1, le=128)


def create_app(
    *,
    config: RuntimeConfig | None = None,
    engine: WorkflowEngine | None = None,
    services: RuntimeServices | None = None,
) -> FastAPI:
    runtime_config = config or RuntimeConfig()
    if runtime_config.a2a.enabled:
        from ._a2a_types import verify_sdk

        verify_sdk()
    runtime_services = services or RuntimeServices(runtime_config)
    runtime_engine = engine or PortableWorkflowEngine()
    scheduler = WorkflowScheduler(
        runtime_services,
        runtime_engine,
        poll_seconds=runtime_config.execution_timing.poll_interval_seconds,
        max_concurrency=runtime_config.execution_timing.max_concurrency,
        batch_size=runtime_config.execution_timing.schedule_batch_size,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        app.state.ready = False
        try:
            await runtime_engine.initialize(runtime_services)
            workflow_source: Any = runtime_config.workflow.definition
            if workflow_source is None and runtime_config.workflow.path:
                workflow_source = runtime_config.workflow.path
            app.state.plan = await resolve_and_compile_sandbox_workflow(
                workflow_source,
                sandbox=runtime_config.sandbox,
                trusted_catalogs=runtime_config.workflow.external_catalogs,
                resolver=runtime_services.external_catalogs,
                catalog=runtime_services.catalog,
                grpc_enabled=runtime_services.grpc_enabled,
                enabled_function_calls=runtime_services.role_function_calls,
            )
            runtime_services.verify_grpc_plan(app.state.plan)
            runtime_services.workflow_catalog.register(app.state.plan)
            for child_workflow in runtime_config.workflow.catalog:
                child_plan = await resolve_and_compile_sandbox_workflow(
                    child_workflow,
                    sandbox=runtime_config.sandbox,
                    trusted_catalogs=runtime_config.workflow.external_catalogs,
                    resolver=runtime_services.external_catalogs,
                    catalog=runtime_services.catalog,
                    grpc_enabled=runtime_services.grpc_enabled,
                    enabled_function_calls=runtime_services.role_function_calls,
                )
                runtime_services.verify_grpc_plan(child_plan)
                runtime_services.workflow_catalog.register(child_plan)
            if runtime_config.a2a.enabled:
                skill_plans: dict[str, WorkflowPlan] = {}
                skill_cards: list[dict[str, Any]] = []
                for skill in runtime_config.a2a.skills:
                    plan = runtime_services.workflow_catalog.resolve_by_name(skill.workflow)
                    skill_plans[skill.id] = plan
                    skill_cards.append(
                        {
                            "id": skill.id,
                            "name": skill.name or plan.name,
                            "description": skill.description,
                            "tags": list(skill.tags),
                        }
                    )
                app.state.a2a_skill_plans = skill_plans
                app.state.a2a_skill_cards = skill_cards
            if runtime_config.knowledge.reload.mode == "startup":
                runtime_services.knowledge.reload()
            elif runtime_config.knowledge.reload.mode == "watch":
                await runtime_services.knowledge.start_watch(
                    runtime_config.knowledge.reload.interval_seconds
                )
            await scheduler.start()
            if runtime_config.a2a.enabled:
                await runtime_engine.start_initial_deliveries(_a2a_delivery_authorized)
            app.state.ready = True
            yield
        finally:
            await scheduler.stop()
            await runtime_services.knowledge.stop_watch()
            await runtime_engine.shutdown()
            if services is None:
                runtime_services.close()

    app = FastAPI(title="Open Workflow Agent", version=__version__, lifespan=lifespan)
    app.add_middleware(
        RequestSizeLimitMiddleware, max_bytes=runtime_config.server.max_request_bytes
    )
    if runtime_config.traffic_policy.enabled:
        app.add_middleware(
            TrafficPolicyMiddleware,
            policy=runtime_config.traffic_policy,
            metrics=runtime_services.metrics,
        )
    if runtime_config.server.api_security_profile:
        app.add_middleware(
            ApiAuthenticationMiddleware,
            security=runtime_config.security,
            profile_name=runtime_config.server.api_security_profile,
        )
    if runtime_config.server.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=runtime_config.server.cors_origins,
            allow_credentials=runtime_config.server.cors_allow_credentials,
            allow_methods=runtime_config.server.cors_methods,
            allow_headers=runtime_config.server.cors_headers,
        )
    if runtime_config.server.security_headers.enabled:
        app.add_middleware(
            SecurityHeadersMiddleware,
            config=runtime_config.server.security_headers,
        )
    app.add_middleware(
        MetricsMiddleware,
        metrics=runtime_services.metrics,
        a2a_path=runtime_config.a2a.path,
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

        # Check downstream dependencies
        checks: dict[str, str] = {}
        overall_status = "ok"

        # Check database connectivity
        try:
            # Simple check - verify the invocations store is accessible
            if runtime_services.invocations is not None:
                checks["database"] = "ok"
            else:
                checks["database"] = "not_configured"
        except Exception as exc:
            checks["database"] = f"error: {exc}"
            overall_status = "degraded"

        # Check knowledge service (if configured)
        try:
            if runtime_config.knowledge.path:
                status = runtime_services.knowledge.reload_status()
                if status["last_reload_succeeded"] is False:
                    checks["knowledge"] = "degraded:reload_failed"
                    overall_status = "degraded"
                else:
                    checks["knowledge"] = "ok"
        except Exception as exc:
            checks["knowledge"] = f"error: {exc}"
            overall_status = "degraded"

        # Check model availability
        try:
            # Verify model is configured
            if runtime_config.model.name:
                checks["model"] = "ok"
        except Exception as exc:
            checks["model"] = f"error: {exc}"
            overall_status = "degraded"

        if overall_status == "degraded":
            return JSONResponse(
                status_code=200,
                content={
                    "status": "degraded",
                    "checks": checks,
                },
            )

        return {"status": "ok", "checks": checks}

    @app.get("/metrics", response_class=Response)
    async def metrics() -> Response:
        schedule_counts = runtime_services.schedules.status_counts()
        for status in ("active", "completed", "cancelled", "faulted"):
            runtime_services.metrics.set_gauge(
                "owa_scheduler_jobs",
                float(schedule_counts.get(status, 0)),
                labels={"status": status},
            )
        approval_counts = runtime_services.approvals.store.status_counts()
        runtime_services.metrics.set_gauge(
            "owa_approval_queue_depth",
            float(approval_counts.get("pending", 0)),
        )
        return Response(
            content=runtime_services.metrics.render(),
            media_type="text/plain; version=0.0.4",
        )

    @app.get("/v1/capabilities")
    async def capabilities() -> dict[str, Any]:
        value = runtime_engine.capabilities().as_dict()
        value["features"]["scheduling"]["on"] = runtime_services.event_store is not None
        value.setdefault("features", {})["events"] = event_capabilities(runtime_services.event_bus)
        value.setdefault("features", {})["approvals"] = runtime_services.approvals.capabilities()
        value.setdefault("features", {})["catalogs"] = (
            runtime_services.external_catalogs.capabilities()
        )
        value.setdefault("features", {})["sandbox"] = runtime_services.sandbox.capabilities()
        value.setdefault("features", {})["mcp"] = mcp_capabilities(runtime_services.sandbox)
        grpc = grpc_capabilities(enabled=runtime_services.grpc_enabled)
        value.setdefault("features", {})["grpc"] = grpc
        if grpc["enabled"] and "grpc" not in value["protocols"]:
            value["protocols"].append("grpc")
        value.setdefault("features", {})["lifecycleStreaming"] = streaming_capabilities(
            durable_history=runtime_services.lifecycle_history is not None
        )
        value.setdefault("features", {})["tracing"] = runtime_services.tracing.capabilities()
        value.setdefault("features", {})["a2a"] = a2a_capabilities(runtime_config.a2a)
        value.setdefault("features", {})["trafficPolicy"] = traffic_policy_capabilities(
            runtime_config.traffic_policy
        )
        return value

    @app.post("/v1/events")
    async def publish_event(request: PublishEventRequest, http_request: Request) -> dict[str, Any]:
        try:
            bus = runtime_services.event_bus
            if isinstance(bus, (DurableEventBus, ApprovalEventBus)):
                envelope = await bus.publish_scoped(
                    request.event,
                    default_source="urn:open-workflow-agent:api",
                    scope=_trusted_event_scope(http_request),
                )
            else:
                envelope = await bus.publish(
                    request.event, default_source="urn:open-workflow-agent:api"
                )
        except ValueError as exc:
            raise EventValidationError(str(exc)) from exc
        return envelope.as_dict()

    @app.get("/v1/events/lifecycle")
    async def lifecycle_events(
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> JSONResponse:
        _authorize_request("events.read", "events:lifecycle")
        return JSONResponse(
            content=[
                event.as_dict() for event in runtime_services.lifecycle_events.snapshot(limit)
            ],
            media_type="application/cloudevents-batch+json",
        )

    @app.get("/v1/events/lifecycle/stream")
    async def lifecycle_event_stream(
        max_events: int = Query(default=100, ge=1, le=1000),
        max_bytes: int = Query(default=1_048_576, ge=256, le=16_777_216),
        timeout_seconds: float = Query(default=30.0, gt=0, le=300),
        queue_size: int = Query(default=64, ge=1, le=1000),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> Any:
        _authorize_request("events.read", "events:lifecycle")
        limits = StreamLimits(
            max_events=max_events,
            max_bytes=max_bytes,
            timeout_seconds=timeout_seconds,
            queue_size=queue_size,
        )
        try:
            stream = lifecycle_sse_stream(
                runtime_services.lifecycle_events,
                last_event_id=last_event_id,
                limits=limits,
            )
        except LookupError as exc:
            return JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "code": "stream_replay_unavailable",
                        "message": str(exc),
                        "details": {},
                    }
                },
            )
        except RuntimeError as exc:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "stream_capacity_exceeded",
                        "message": str(exc),
                        "details": {},
                    }
                },
            )
        return StreamingResponse(
            stream,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/v1/approvals")
    async def list_approvals(
        http_request: Request,
        status: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        authorization: str | None = Header(default=None, alias="Authorization"),
        operator_id: str | None = Header(default=None, alias="X-Operator-Id"),
        actor_authorization: str | None = Header(default=None, alias="X-Approval-Authorization"),
    ) -> list[dict[str, Any]]:
        _validate_approval_header(http_request)
        _authorize_request("approvals.list", "approvals")
        if actor_authorization is not None:
            if status is not None and status not in APPROVAL_STATES:
                raise ApprovalValidationError("unsupported approval status")
            actor = runtime_services.approvals.authorize_actor(actor_authorization, operator_id)
            protected = runtime_services.approvals.protected_store
            assert protected is not None
            where = " AND a.status=?" if status is not None else ""
            parameters: tuple[Any, ...] = (
                (actor.identity, status, limit) if status is not None else (actor.identity, limit)
            )
            rows = protected.connection.execute(
                f"SELECT r.approval_id FROM {protected.requests} r "
                f"JOIN {runtime_services.approvals.store._table} a ON a.approval_id=r.approval_id "
                f"WHERE r.assignee=?{where} ORDER BY a.requested_at DESC LIMIT ?",
                parameters,
            ).fetchall()
            return [
                runtime_services.approvals.visible_record(row[0], actor).as_dict() for row in rows
            ]
        runtime_services.approvals.authorize(authorization, operator_id)
        return [
            record.as_dict()
            for record in runtime_services.approvals.store.list(status=status, limit=limit)
            if not isinstance(record.request_event.get("data"), dict)
            or "approvalPolicy" not in record.request_event["data"]
        ]

    @app.get("/v1/approvals/{approval_id}")
    async def get_approval(
        approval_id: str,
        http_request: Request,
        authorization: str | None = Header(default=None, alias="Authorization"),
        operator_id: str | None = Header(default=None, alias="X-Operator-Id"),
        actor_authorization: str | None = Header(default=None, alias="X-Approval-Authorization"),
    ) -> dict[str, Any]:
        _validate_approval_header(http_request)
        _authorize_request("approvals.get", "approval:" + approval_id)
        if actor_authorization is not None:
            actor = runtime_services.approvals.authorize_actor(actor_authorization, operator_id)
            return runtime_services.approvals.visible_record(approval_id, actor).as_dict()
        runtime_services.approvals.authorize(authorization, operator_id)
        record = runtime_services.approvals.store.get(approval_id)
        if record is None:
            raise ApprovalNotFound("approval not found", details={"approval_id": approval_id})
        if record.assignment_revision is not None:
            raise ApprovalAuthorizationError(
                "protected approval requires trusted actor credentials"
            )
        return record.as_dict()

    @app.post("/v1/approvals/{approval_id}/decision")
    async def decide_approval(
        approval_id: str,
        request: ApprovalDecisionRequest,
        http_request: Request,
        authorization: str | None = Header(default=None, alias="Authorization"),
        operator_id: str | None = Header(default=None, alias="X-Operator-Id"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        actor_authorization: str | None = Header(default=None, alias="X-Approval-Authorization"),
    ) -> dict[str, Any]:
        _validate_approval_header(http_request)
        _authorize_request("approvals.decide", "approval:" + approval_id)
        actor = (
            runtime_services.approvals.authorize_actor(actor_authorization, operator_id)
            if actor_authorization is not None
            else None
        )
        authorized_operator = (
            actor.identity
            if actor is not None
            else runtime_services.approvals.authorize(authorization, operator_id)
        )
        record = await runtime_services.approvals.decide(
            approval_id,
            decision=request.decision,
            operator_id=authorized_operator,
            value=request.value,
            operation_key=idempotency_key,
            revision=request.revision,
            actor=actor,
        )
        return record.as_dict()

    @app.post("/v1/approvals/{approval_id}/reassign")
    @app.post("/v1/approvals/{approval_id}/escalate")
    async def change_approval_assignment(
        approval_id: str,
        request: ApprovalAssignmentRequest,
        http_request: Request,
        actor_authorization: str | None = Header(default=None, alias="X-Approval-Authorization"),
        operator_id: str | None = Header(default=None, alias="X-Operator-Id"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        _validate_approval_header(http_request)
        action = "escalate" if http_request.url.path.endswith("/escalate") else "reassign"
        _authorize_request("approvals." + action, "approval:" + approval_id)
        actor = runtime_services.approvals.authorize_actor(actor_authorization, operator_id)
        record = await runtime_services.approvals.mutate_protected(
            approval_id,
            action=action,
            actor=actor,
            revision=request.revision,
            assignee=request.assignee,
            operation_key=idempotency_key,
        )
        return record.as_dict()

    def _validate_approval_header(request: Request) -> None:
        for selected in (b"authorization", b"x-approval-authorization"):
            if sum(name.lower() == selected for name, _ in request.scope.get("headers", [])) > 1:
                raise ApprovalAuthorizationError("approval authentication failed")

    def _authorize_request(action: str, resource: str) -> None:
        runtime_services.execution_authorizations.authorize_request(
            current_authority(), action, resource
        )

    @app.post("/v1/schedules")
    async def create_schedule(
        request: CreateScheduleRequest,
        http_request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        plan = getattr(
            app.state,
            "plan",
            compile_sandbox_workflow(
                sandbox=runtime_config.sandbox,
                grpc_enabled=runtime_services.grpc_enabled,
                enabled_function_calls=runtime_services.role_function_calls,
            ),
        )
        if (
            parse_schedule(plan.source.get("schedule")).kind == "on"
            and runtime_services.event_store is None
        ):
            raise ScheduleValidationError("event schedules require the embedded event store")
        _authorize_request(
            "schedules.create", f"workflow:{plan.namespace}/{plan.name}@{plan.version}"
        )
        record = runtime_services.schedules.create(
            plan,
            request.input,
            operation_key=idempotency_key,
            owner_principal=_trusted_event_scope(http_request),
            event_scope=_trusted_event_scope(http_request),
            timezone=runtime_config.execution_timing.timezone,
        )
        return record.as_dict()

    def authorized_schedule(schedule_id: str, request: Request) -> ScheduleRecord:
        record = runtime_services.schedules.get(schedule_id)
        principal = _trusted_event_scope(request)
        ownerless_anonymous = (
            not runtime_config.server.api_security_profile
            and principal == DEFAULT_EVENT_SCOPE
            and record is not None
            and record.owner_principal is None
        )
        if record is None or (record.owner_principal != principal and not ownerless_anonymous):
            # Do not echo an identifier from a lookup that may refer to a
            # foreign object. A fixed response keeps foreign and missing IDs
            # indistinguishable at the API boundary.
            raise ScheduleNotFound("schedule not found")
        return record

    @app.get("/v1/schedules/{schedule_id}")
    async def get_schedule(schedule_id: str, http_request: Request) -> dict[str, Any]:
        record = authorized_schedule(schedule_id, http_request)
        _authorize_request("schedules.get", "schedule:" + schedule_id)
        return record.as_dict()

    @app.post("/v1/schedules/{schedule_id}/cancel")
    async def cancel_schedule(
        schedule_id: str,
        http_request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        authorized_schedule(schedule_id, http_request)
        _authorize_request("schedules.cancel", "schedule:" + schedule_id)
        try:
            record = await scheduler.cancel(schedule_id, operation_id=idempotency_key)
        except KeyError as exc:
            raise ScheduleNotFound("schedule not found") from exc
        return record.as_dict()

    @app.post("/v1/invoke")
    async def invoke(request: InvokeRequest, http_request: Request) -> Any:
        plan = getattr(
            app.state,
            "plan",
            compile_sandbox_workflow(
                sandbox=runtime_config.sandbox,
                grpc_enabled=runtime_services.grpc_enabled,
                enabled_function_calls=runtime_services.role_function_calls,
            ),
        )
        _authorize_request(
            "invocations.create", f"workflow:{plan.namespace}/{plan.name}@{plan.version}"
        )
        handle = runtime_services.invocations.create(
            engine=runtime_engine.engine_name,
            session_id=request.session_id,
            user_id=request.user_id,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
            owner_principal=_trusted_event_scope(http_request),
        )
        runtime_services.execution_authorizations.reserve_invocation(
            handle, current_authority(), input_data=request.input
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
        http_request: Request,
    ) -> Any:
        handle = authorized_invocation(invocation_id, http_request)
        _authorize_request("invocations.resume", "invocation:" + invocation_id)
        if handle.status in TERMINAL_INVOCATION_STATES:
            return runtime_engine._stored_result(handle).as_dict()
        try:
            plan = runtime_services.resolve_original_definition(handle)
            result = await runtime_engine.resume(handle, request.input, plan)
        except OwaError as exc:
            return JSONResponse(status_code=exc.status_code, content={"error": exc.as_dict()})
        return result.as_dict()

    @app.post("/v1/invocations/{invocation_id}/cancel")
    async def cancel(
        invocation_id: str,
        http_request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> Any:
        handle = authorized_invocation(invocation_id, http_request)
        _authorize_request("invocations.cancel", "invocation:" + invocation_id)
        result = await runtime_engine.cancel(handle, operation_id=idempotency_key)
        return result.as_dict()

    def authorized_invocation(invocation_id: str, request: Request) -> ExecutionHandle:
        handle = runtime_services.invocations.get(invocation_id)
        principal = _trusted_event_scope(request)
        ownerless_anonymous = (
            not runtime_config.server.api_security_profile
            and principal == DEFAULT_EVENT_SCOPE
            and handle is not None
            and handle.owner_principal is None
        )
        if handle is None or (handle.owner_principal != principal and not ownerless_anonymous):
            raise InvocationNotFound(
                "invocation not found", details={"invocation_id": invocation_id}
            )
        return handle

    @app.get("/v1/invocations/{invocation_id}/recovery")
    async def inspect_recovery(invocation_id: str, http_request: Request) -> dict[str, Any]:
        handle = authorized_invocation(invocation_id, http_request)
        _authorize_request("invocations.inspect", "invocation:" + invocation_id)
        return recovery_diagnostics(runtime_services, handle)

    @app.post("/v1/admin/knowledge/reload")
    async def reload_knowledge() -> dict[str, Any]:
        _authorize_request("knowledge.reload", "knowledge")
        return runtime_services.knowledge.reload()

    def _a2a_plan_for_skill(skill_id: str | None) -> WorkflowPlan | None:
        skill_plans = getattr(app.state, "a2a_skill_plans", {}) or {}
        if skill_id is not None:
            return skill_plans.get(skill_id)
        fallback: WorkflowPlan | None = getattr(app.state, "plan", None)
        if fallback is None:
            return compile_sandbox_workflow(
                sandbox=runtime_config.sandbox,
                grpc_enabled=runtime_services.grpc_enabled,
                enabled_function_calls=runtime_services.role_function_calls,
            )
        return fallback

    def _a2a_delivery_authorized(
        handle: ExecutionHandle, skill_id: str | None, original: WorkflowPlan
    ) -> bool:
        # Current exposure/identity policy authorizes continuation separately
        # from the archived executable version selected by the dispatcher.
        if not runtime_config.a2a.enabled:
            return False
        current = _a2a_plan_for_skill(skill_id)
        if current is None or (current.namespace, current.name) != (
            original.namespace,
            original.name,
        ):
            return False
        if runtime_config.a2a.skills and skill_id not in {
            skill.id for skill in runtime_config.a2a.skills
        }:
            return False
        principal = Principal(identity="anonymous")
        if runtime_config.a2a.security_profile:
            profile = runtime_config.security.profile(runtime_config.a2a.security_profile)
            if not isinstance(profile, BearerSecurityProfile):
                return False
            principal = static_principal(profile)
        if handle.owner_principal != principal.identity:
            return False
        policy = runtime_config.a2a.authorization
        return (
            policy is None
            or not policy.rules
            or authorize(
                principal,
                policy,
                action="message.send",
                resource=f"skill:{skill_id}" if skill_id is not None else "skill:workflow",
            )
        )

    def _a2a_task_snapshot(invocation_id: str) -> dict[str, Any] | None:
        handle = runtime_services.invocations.get(invocation_id)
        if handle is None:
            return None
        return project_a2a_task(handle, services=runtime_services)

    def _a2a_outcome(
        invocation_id: str, result: InvocationResult, return_immediately: bool
    ) -> A2ASendOutcome:
        if return_immediately:
            task = _a2a_task_snapshot(invocation_id)
            if task is not None:
                return A2ASendOutcome(task=task)
        if result.status in {"waiting", "faulted", "cancelled"}:
            task = _a2a_task_snapshot(invocation_id)
            if task is not None:
                return A2ASendOutcome(task=task)
        text_container = (
            isinstance(result.output, dict)
            and len(result.output) == 1
            and next(iter(result.output)) in {"text", "response", "message", "output"}
            and isinstance(next(iter(result.output.values())), str)
        )
        if not isinstance(result.output, str) and not text_container:
            task = _a2a_task_snapshot(invocation_id)
            if task is not None:
                return A2ASendOutcome(task=task)
        handle = runtime_services.invocations.get(invocation_id)
        return A2ASendOutcome(
            reply_text=extract_output_text(result.output),
            context_id=handle.session_id if handle is not None else None,
        )

    async def _a2a_start_task(
        text: str,
        skill_id: str | None,
        return_immediately: bool,
        context_id: str | None = None,
        owner_principal: str = "anonymous",
    ) -> A2ASendOutcome:
        plan = _a2a_plan_for_skill(skill_id)
        if plan is None:
            return A2ASendOutcome(error_code="skill_not_found")
        _authorize_request("message.send", f"workflow:{plan.namespace}/{plan.name}@{plan.version}")
        handle = runtime_services.invocations.create(
            engine=runtime_engine.engine_name,
            session_id=context_id or str(uuid4()),
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
            owner_principal=owner_principal,
        )
        runtime_services.execution_authorizations.reserve_invocation(
            handle, current_authority(), input_data={"question": text}
        )
        if return_immediately:
            try:
                await runtime_engine.prepare_initial_delivery(
                    plan, handle, {"question": text}, skill_id=skill_id
                )
            except OwaError as error:
                return A2ASendOutcome(error_code=error.code)
            task = _a2a_task_snapshot(handle.invocation_id)
            if task is not None:
                return A2ASendOutcome(task=task)
            return A2ASendOutcome(error_code="task_not_found")
        return _a2a_outcome(
            handle.invocation_id,
            await runtime_engine.invoke(plan, handle, {"question": text}),
            return_immediately,
        )

    async def _a2a_resolve_resume_target(task_id: str) -> ResumeTarget | None:
        handle = runtime_services.invocations.get(task_id)
        if handle is None:
            return None
        if handle.status in TERMINAL_INVOCATION_STATES:
            return ResumeTarget(status=handle.status, skill_id=None, deployable=True)
        if handle.status == "waiting" and has_pending_event_listener(handle, runtime_services):
            # Durable event waits must be settled by their event source. A
            # SendMessage resume must not turn one into an unbounded engine run.
            return ResumeTarget(status=handle.status, skill_id=None, deployable=False)
        skill_plans = getattr(app.state, "a2a_skill_plans", {}) or {}
        skill_id: str | None = None
        try:
            original = runtime_services.resolve_original_definition(handle)
            # Current advertised skill authority remains separate from the
            # pinned executable version. Match the workflow's declared identity
            # across a version deployment, never silently grant an unrelated
            # archived invocation the implicit default skill's authority.
            candidates = [
                candidate
                for candidate, plan in skill_plans.items()
                if (plan.namespace, plan.name) == (original.namespace, original.name)
            ]
            if len(candidates) == 1:
                skill_id = candidates[0]
            current = getattr(app.state, "plan", None)
            implicit = current is not None and (current.namespace, current.name) == (
                original.namespace,
                original.name,
            )
            deployable = len(candidates) <= 1 and (skill_id is not None or implicit)
        except OwaError:
            deployable = False
        return ResumeTarget(status=handle.status, skill_id=skill_id, deployable=deployable)

    async def _a2a_resume_task(text: str, task_id: str, return_immediately: bool) -> A2ASendOutcome:
        handle = runtime_services.invocations.get(task_id)
        if handle is None:
            return A2ASendOutcome(error_code="task_not_found")
        if handle.status != "waiting":
            return A2ASendOutcome(error_code="task_not_accepting_input")
        try:
            plan = runtime_services.resolve_original_definition(handle)
        except OwaError:
            return A2ASendOutcome(error_code="task_not_resumeable")
        if return_immediately:
            return A2ASendOutcome(error_code="unsupported_async_resume")
        try:
            result = await runtime_engine.resume(handle, {"question": text}, plan)
        except OwaError as exc:
            payload = exc.as_dict()
            code = payload.get("code") if isinstance(payload, dict) else None
            return A2ASendOutcome(error_code=str(code or "workflow_execution_error"))
        return _a2a_outcome(task_id, result, return_immediately)

    if runtime_config.a2a.enabled:
        mount_a2a(
            app,
            runtime_config.a2a,
            runtime_config.security,
            start_task=_a2a_start_task,
            resolve_resume_target=_a2a_resolve_resume_target,
            resume_task=_a2a_resume_task,
        )

    return app


def build_app_from_environment() -> FastAPI:
    return create_app(config=RuntimeConfig.from_file())
