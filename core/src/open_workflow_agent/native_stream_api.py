"""Original-owner, data-bearing native observation SSE. Reads never execute work."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Callable
from time import monotonic
from typing import Any, Literal

from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.types import Receive, Scope, Send

from .engine import WorkflowEngine
from .errors import InvocationNotFound, UnsupportedWorkflowFeature
from .execution_authority import current_authority
from .native_extensions import LANGGRAPH_STREAMS
from .native_stream_registry import NativeStreamRegistry
from .native_streams import NativeStreamFrame, NativeStreamSubscription
from .persistence import ExecutionHandle
from .services import RuntimeServices
from .streaming import StreamLimits, encode_terminal


def native_stream_capabilities() -> dict[str, Any]:
    return {
        "profile": LANGGRAPH_STREAMS,
        "endpoint": "/v1/invocations/{invocation_id}/stream",
        "transport": "sse",
        "modes": ["values", "updates"],
        "data": "task_data_and_public_ows_references",
        "ordering": "native_sdk_emission_order",
        "durability": "process_local_observation_only",
        "replay": "bounded_same_worker_incarnation",
        "disconnectCancelsInvocation": False,
        "action": "invocations.stream",
    }


class _NativeStreamResponse(StreamingResponse):
    def __init__(
        self,
        content: AsyncGenerator[bytes, None],
        registry: NativeStreamRegistry,
        subscription: NativeStreamSubscription,
        timeout: float,
    ) -> None:
        super().__init__(
            content,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
        self._registry = registry
        self._subscription = subscription
        self._timeout = timeout
        self._content = content

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            async with asyncio.timeout(self._timeout):
                await super().__call__(scope, receive, send)
        except TimeoutError:
            pass  # A blocked network consumer is disconnected at its deadline.
        finally:
            try:
                await self._content.aclose()
            finally:
                self._registry.release(self._subscription)


def _stream(
    backlog: tuple[NativeStreamFrame, ...],
    subscription: NativeStreamSubscription,
    limits: StreamLimits,
    mode: str,
) -> AsyncGenerator[bytes, None]:
    async def events() -> AsyncGenerator[bytes, None]:
        started = monotonic()
        count = 0
        size = 0
        reason = "timeout"
        pending = iter(backlog)
        try:
            while count < limits.max_events:
                remaining = limits.timeout_seconds - (monotonic() - started)
                if remaining <= 0:
                    break
                frame = next(pending, None)
                if frame is None:
                    try:
                        frame = await subscription.receive(timeout=remaining)
                    except TimeoutError:
                        break
                    except BufferError:
                        reason = "observation_unavailable"
                        break
                    except EOFError as exc:
                        reason = str(exc)
                        break
                if mode != "all" and json.loads(frame.payload)["mode"] != mode:
                    continue
                encoded = (
                    b"id: "
                    + frame.event_id.encode("ascii")
                    + b"\nevent: native.observation\ndata: "
                    + frame.payload
                    + b"\n\n"
                )
                if size + len(encoded) > limits.max_bytes:
                    reason = "byte_limit"
                    break
                yield encoded
                size += len(encoded)
                count += 1
            else:
                reason = "event_limit"
            terminal = encode_terminal(reason=reason)
            if size + len(terminal) <= limits.max_bytes:
                yield terminal
        finally:
            subscription.close()

    return events()


def mount_native_streams(
    app: FastAPI,
    services: RuntimeServices,
    engine: WorkflowEngine,
    authorize: Callable[[str, str], None],
    invocation: Callable[[str, Request], ExecutionHandle],
) -> None:
    if services.config.observability.native_streams is None:
        return

    @app.get("/v1/invocations/{invocation_id}/stream")
    async def observe(
        invocation_id: str,
        http_request: Request,
        mode: Literal["all", "values", "updates"] = "all",
        max_events: int = Query(default=100, ge=1, le=1000),
        max_bytes: int = Query(default=1_048_576, ge=256, le=16_777_216),
        timeout_seconds: float = Query(default=30, gt=0, le=300),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> Any:
        handle = invocation(invocation_id, http_request)
        caller = current_authority()
        if caller is None or caller.principal.identity != handle.owner_principal:
            raise InvocationNotFound("invocation not found")
        resource = "invocation:" + invocation_id
        authorize("invocations.stream", resource)
        original = services.execution_authorizations.verify_invocation(handle)
        services.execution_authorizations.authorize_request(
            original, "invocations.stream", resource
        )
        if LANGGRAPH_STREAMS not in engine.capabilities().native_extensions:
            raise UnsupportedWorkflowFeature("native stream observations are unavailable")
        registry = services.native_streams
        if registry is None:
            raise UnsupportedWorkflowFeature("native stream observations are unavailable")
        registry.verify(services.config.observability.native_streams)
        try:
            backlog, subscription = registry.subscribe(handle, last_event_id=last_event_id)
        except (LookupError, BufferError):
            return JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "code": "native_stream_replay_unavailable",
                        "message": "native observations or the requested replay "
                        "are unavailable in this worker",
                        "details": {},
                    }
                },
            )
        except RuntimeError:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "native_stream_capacity_exceeded",
                        "message": "native observation subscriber capacity is exhausted",
                        "details": {},
                    }
                },
            )
        limits = StreamLimits(
            max_events=max_events,
            max_bytes=max_bytes,
            timeout_seconds=timeout_seconds,
            queue_size=registry.configuration.queue_size,
        )
        return _NativeStreamResponse(
            _stream(backlog, subscription, limits, mode), registry, subscription, timeout_seconds
        )
