"""HTTP server for running OpenWorkflow documents as ADK agents."""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from openworkflow_adk.interop.openapi import generate_openapi
from openworkflow_adk.loader import load
from openworkflow_adk.models import OpenWorkflowDocument
from openworkflow_adk.ops.history import InMemoryRunHistory, SQLiteRunHistory
from openworkflow_adk.ops.postgres_history import PostgresRunHistory, PostgresRunHistoryConfig
from openworkflow_adk.run_config import RunConfig
from openworkflow_adk.runtime import run_workflow
from openworkflow_adk.security.access import AccessPolicy, Principal

try:
    from fastapi import HTTPException, Request
except ImportError:  # pragma: no cover - server extras may be missing at import time
    HTTPException = type("HTTPException", (Exception,), {})  # type: ignore[misc,assignment]
    Request = Any  # type: ignore[misc,assignment]

logger = logging.getLogger("openworkflow_adk.server")


class RunRequest(BaseModel):
    input: dict[str, Any] = {}
    session_id: str | None = None


@dataclass
class ServerAuthConfig:
    """Authentication configuration for the HTTP server.

    ``api_keys`` are static bearer credentials accepted from the
    ``Authorization: Bearer <key>`` or ``X-API-Key: <key>`` headers.
    ``token_verifier`` is an optional callable that maps an OIDC/opaque bearer
    token to a :class:`Principal`; the server never verifies tokens itself.
    ``access_policy`` restricts which permissions the derived principal may use.
    """

    api_keys: set[str] = field(default_factory=set)
    access_policy: AccessPolicy | None = None
    token_verifier: Callable[[str], Principal] | None = None

    @classmethod
    def from_env(cls) -> ServerAuthConfig:
        keys = {
            item.strip()
            for item in os.environ.get("WORKFLOW_SERVER_API_KEY", "").split(",")
            if item.strip()
        }
        return cls(api_keys=keys)


def _is_loopback(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1", ""}


def _check_deps() -> None:
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "server extras are required; install with 'pip install open-workflow-agent-adk[server]'"
        ) from exc


def create_app(
    document: OpenWorkflowDocument | str,
    *,
    model_factory: Callable[[str], Any] | None = None,
    function_registry: dict[str, Callable[..., Any]] | None = None,
    workflow_registry: Any | None = None,
    event_sink: Callable[[Any], None | Awaitable[None]] | None = None,
    history: InMemoryRunHistory | SQLiteRunHistory | PostgresRunHistory | None = None,
    history_config: PostgresRunHistoryConfig | None = None,
    auth: ServerAuthConfig | None = None,
) -> Any:
    """Return a FastAPI app that runs a loaded OpenWorkflow document.

    When ``auth`` is ``None`` the app reads ``WORKFLOW_SERVER_API_KEY`` from the
    environment; if neither provides credentials the endpoints remain open
    (acceptable for the default loopback binding — see :func:`serve`).
    """
    _check_deps()
    from contextlib import asynccontextmanager

    from fastapi import Body, Depends, FastAPI, HTTPException
    from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

    if isinstance(document, str):
        document = load(document)

    effective_auth = auth if auth is not None else ServerAuthConfig.from_env()

    @asynccontextmanager
    async def lifespan(app: Any) -> Any:
        if history_config is not None:
            pg_history = PostgresRunHistory(history_config)
            await pg_history.connect()
            app.state.history = pg_history
        else:
            app.state.history = history
        try:
            yield
        finally:
            if isinstance(app.state.history, PostgresRunHistory):
                await app.state.history.close()

    app = FastAPI(
        title=f"owf-adk: {document.document.name}",
        lifespan=lifespan,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.state.history = history

    def _extract_credential(request: Request) -> str | None:
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        return request.headers.get("x-api-key")

    def _require_auth(request: Request) -> Principal:
        if not effective_auth.api_keys and effective_auth.token_verifier is None:
            return Principal(subject="anonymous")
        credential = _extract_credential(request)
        if not credential:
            raise HTTPException(
                status_code=401,
                detail="missing credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if credential in effective_auth.api_keys:
            principal = Principal(subject="api-key")
        elif effective_auth.token_verifier is not None:
            try:
                principal = effective_auth.token_verifier(credential)
            except Exception as exc:
                logger.warning("token verification failed: %s", exc)
                raise HTTPException(status_code=401, detail="invalid credentials") from exc
        else:
            raise HTTPException(status_code=401, detail="invalid credentials")
        if effective_auth.access_policy is not None:
            try:
                effective_auth.access_policy.require(principal, "workflow:run")
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
        return principal

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "workflow": document.document.name}

    @app.get("/metrics")
    async def metrics(_: Principal = Depends(_require_auth)) -> PlainTextResponse:
        body = await _prometheus_metrics(app.state.history)
        return PlainTextResponse(content=body, media_type="text/plain; version=0.0.4")

    @app.get("/openapi.json")
    async def openapi(request: Request, _: Principal = Depends(_require_auth)) -> JSONResponse:
        spec = generate_openapi(document, base_url=str(request.base_url))
        return JSONResponse(content=spec)

    @app.post("/run")
    async def run(
        payload: RunRequest = Body(...), principal: Principal = Depends(_require_auth)
    ) -> JSONResponse:
        run_config = RunConfig(
            user_id=principal.subject,
            session_id=payload.session_id or "workflow-session",
            model_factory=model_factory,
            function_registry=function_registry,
            workflow_registry=workflow_registry,
            event_sink=event_sink,
            history=app.state.history,
        )
        try:
            events = await run_workflow(document, payload.input, config=run_config)
        except Exception as exc:
            raise _internal_error(exc) from exc
        return JSONResponse(
            content={
                "workflow": document.document.name,
                "events": [_event_to_json(event) for event in events],
            }
        )

    @app.post("/run/stream")
    async def run_stream(
        payload: RunRequest = Body(...), principal: Principal = Depends(_require_auth)
    ) -> StreamingResponse:
        async def generator():
            collected: list[Any] = []

            async def sink(event: Any) -> None:
                collected.append(event)

            run_config = RunConfig(
                user_id=principal.subject,
                session_id=payload.session_id or "workflow-session",
                model_factory=model_factory,
                function_registry=function_registry,
                workflow_registry=workflow_registry,
                event_sink=sink,
                history=app.state.history,
            )
            try:
                await run_workflow(document, payload.input, config=run_config)
            except Exception as exc:
                correlation_id = _log_error(exc)
                yield f'event: error\ndata: {{"correlation_id": "{correlation_id}"}}\n\n'
                return
            for event in collected:
                yield f"data: {_event_to_json(event)}\n\n"

        return StreamingResponse(generator(), media_type="text/event-stream")

    return app


def _log_error(exc: Exception) -> str:
    """Log an internal error server-side and return a correlation ID."""
    correlation_id = str(uuid.uuid4())
    logger.exception("workflow run failed (correlation_id=%s): %s", correlation_id, exc)
    return correlation_id


def _internal_error(exc: Exception) -> HTTPException:
    """Convert an internal exception into a generic 500 with a correlation ID."""
    correlation_id = _log_error(exc)
    return HTTPException(
        status_code=500,
        detail={
            "code": "internal_error",
            "correlation_id": correlation_id,
            "message": "the workflow run failed; see server logs with the correlation id",
        },
    )


async def _prometheus_metrics(
    history: InMemoryRunHistory | SQLiteRunHistory | PostgresRunHistory | None,
) -> str:
    lines: list[str] = []
    if isinstance(history, PostgresRunHistory):
        summary = await history.stats_summary()
        lines.append("# HELP owf_adk_runs_total Total workflow runs by status")
        lines.append("# TYPE owf_adk_runs_total gauge")
        for status, count in summary["by_status"].items():
            lines.append(f'owf_adk_runs_total{{status="{status}"}} {count}')
        durations = summary["duration_seconds"]
        lines.append("# HELP owf_adk_run_duration_seconds Workflow run duration percentiles")
        lines.append("# TYPE owf_adk_run_duration_seconds summary")
        for quantile, value in durations.items():
            if value is not None:
                lines.append(f'owf_adk_run_duration_seconds{{quantile="{quantile}"}} {value}')
        failures = await history.failure_summary(limit=1000)
        lines.append("# HELP owf_adk_run_failures_total Total failed workflow runs")
        lines.append("# TYPE owf_adk_run_failures_total gauge")
        lines.append(f"owf_adk_run_failures_total {len(failures)}")
    else:
        lines.append(
            "# HELP owf_adk_runs_total Total workflow runs (history backend not configured)"
        )
        lines.append("# TYPE owf_adk_runs_total gauge")
        lines.append('owf_adk_runs_total{status="unknown"} 0')
    return "\n".join(lines) + "\n"


def _event_to_json(event: Any) -> dict[str, Any]:
    return {
        "author": getattr(event, "author", None),
        "output": getattr(event, "output", None),
        "error": getattr(event, "error_message", None),
    }


def serve(
    document: OpenWorkflowDocument | str,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    model_factory: Callable[[str], Any] | None = None,
    function_registry: dict[str, Callable[..., Any]] | None = None,
    workflow_registry: Any | None = None,
    history: InMemoryRunHistory | SQLiteRunHistory | PostgresRunHistory | None = None,
    history_config: PostgresRunHistoryConfig | None = None,
    auth: ServerAuthConfig | None = None,
) -> None:
    """Start a Uvicorn server for the given workflow document.

    Binding to any host other than loopback requires authentication: either an
    explicit ``auth`` config or ``WORKFLOW_SERVER_API_KEY`` in the environment.
    """
    _check_deps()
    import uvicorn

    effective_auth = auth if auth is not None else ServerAuthConfig.from_env()
    if (
        not _is_loopback(host)
        and not effective_auth.api_keys
        and effective_auth.token_verifier is None
    ):
        raise ValueError(
            "binding to a non-loopback host requires authentication; "
            "set WORKFLOW_SERVER_API_KEY or pass an explicit auth config"
        )
    app = create_app(
        document,
        model_factory=model_factory,
        function_registry=function_registry,
        workflow_registry=workflow_registry,
        history=history,
        history_config=history_config,
        auth=effective_auth,
    )
    uvicorn.run(app, host=host, port=port)
