"""Development runtime helpers for assembled ADK workflows."""

from __future__ import annotations

import asyncio
import inspect
import os
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from google.adk.memory import BaseMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from openworkflow_adk.config import resolve_memory_config
from openworkflow_adk.expressions import evaluate
from openworkflow_adk.models import OpenWorkflowDocument
from openworkflow_adk.ops import replay as _replay
from openworkflow_adk.ops.schedule import trigger_events
from openworkflow_adk.resources.broker import Broker, InMemoryBroker
from openworkflow_adk.resources.catalog import resolve_catalog_functions
from openworkflow_adk.resources.memory import create_memory_service
from openworkflow_adk.run_config import RunConfig
from openworkflow_adk.security.security import redact, resolve_secret
from openworkflow_adk.suspension import WorkflowSuspended
from openworkflow_adk.translator import build_workflow

replay_event_log = _replay.replay_event_log


def _has_agent(items: list[Any]) -> bool:
    """Recursively check whether any task uses the agent extension."""
    from openworkflow_adk.models import TaskItem

    for item in items:
        if isinstance(item, dict):
            item = TaskItem.model_validate(item)
        if item.task.effective_agent() is not None:
            return True
        if item.task.do and _has_agent(item.task.do):
            return True
        if item.task.try_ and _has_agent(item.task.try_):
            return True
        fork = item.task.fork or {}
        if isinstance(fork, dict) and _has_agent(fork.get("branches", [])):
            return True
        for case in item.task.switch or []:
            if isinstance(case, dict):
                configuration = next(iter(case.values())) if case else {}
                if isinstance(configuration, dict) and _has_agent(configuration.get("do", [])):
                    return True
    return False


replay_from_task = _replay.replay_from_task
verify_replay_determinism = _replay.verify_replay_determinism


_HISTORY_METHODS = frozenset({"start", "get", "finish", "checkpoint", "record_event", "suspend"})


async def _call_history_method(history: Any, method: str, *args: Any, **kwargs: Any) -> Any:
    """Invoke a sync or async history method and await the result if needed.

    Only a fixed set of lifecycle methods may be dispatched, and synchronous
    (blocking) implementations such as :class:`SQLiteRunHistory` are executed in
    a worker thread so the event loop is never blocked.
    """
    if method not in _HISTORY_METHODS:
        raise ValueError(f"history method {method!r} is not allowed")
    callable_method = getattr(history, method, None)
    if callable_method is None:
        raise AttributeError(f"history backend has no method {method!r}")
    if inspect.iscoroutinefunction(callable_method):
        return await callable_method(*args, **kwargs)
    return await asyncio.to_thread(callable_method, *args, **kwargs)


async def run_workflow(
    document: OpenWorkflowDocument,
    input: dict[str, Any] | None = None,
    *,
    config: RunConfig | None = None,
    **kwargs: Any,
) -> list[Any]:
    """Run a translated workflow using the selected ADK session backend.

    Pass a frozen :class:`RunConfig` for repeated runs, or keep using the
    keyword arguments (backward compatible); the keywords are consolidated into
    a ``RunConfig`` when ``config`` is omitted.
    """
    cfg = config if config is not None else RunConfig(**kwargs)
    if cfg.mode not in {"auto", "extended"}:
        raise ValueError("mode must be auto or extended")
    if cfg.resume and cfg.history is None:
        raise ValueError("resume requires a persistent run history")
    resumed_from_history = False
    if cfg.resume and cfg.history is not None:
        prior = await _load_prior_run(cfg.history, cfg.session_id)
        if prior is not None and prior.checkpoint_task:
            if cfg.region is not None and prior.region is not None and prior.region != cfg.region:
                raise RuntimeError(
                    f"run region {prior.region!r} does not match requested region {cfg.region!r}"
                )
            if prior.status == "suspended" and prior.resume_at:
                resume_at = datetime.fromisoformat(prior.resume_at)
                if resume_at > datetime.now(timezone.utc):
                    raise RuntimeError(
                        f"workflow timer has not elapsed; resume_at={prior.resume_at}"
                    )
            start_at_checkpoint = prior.suspension_reason not in {"human_input", "broker_listen"}
            resumed_do = _resume_task_list(document.do, prior.checkpoint_task)
            if resumed_do is None:
                raise KeyError(f"checkpoint task {prior.checkpoint_task!r} is not in workflow")
            if start_at_checkpoint:
                document = document.model_copy(update={"do": resumed_do})
            input = prior.state
            resumed_from_history = True
    document = await resolve_catalog_functions(
        document,
        cfg.catalog_registry,
        base_dir=cfg.catalog_base_dir,
        environ=dict(os.environ),
    )
    if (
        not resumed_from_history
        and isinstance(document.input, dict)
        and document.input.get("from") is not None
        and input is not None
    ):
        # Document-level `input.from` filters the raw run input before the
        # workflow starts; resumed state is already filtered and is kept as-is.
        filtered = evaluate(document.input["from"], input)
        if filtered is not None:
            input = filtered
    workflow = build_workflow(
        document,
        config=cfg,
        broker=cfg.broker or InMemoryBroker(),
        suspend_long_waits=cfg.history is not None
        if cfg.suspend_long_waits is None
        else cfg.suspend_long_waits,
        suspend_after=cfg.suspend_after,
        resume_input=cfg.resume_input,
        suspend_listens=cfg.history is not None and not cfg.resume,
        memoization=cfg.memoization,
        self_healer=cfg.self_healer,
    )
    if cfg.history is not None and not cfg.resume:
        await _call_history_method(
            cfg.history,
            "start",
            cfg.session_id,
            document.document.name,
            input or {},
            region=cfg.region,
        )
    secret_values = [
        value for name in document.use.secrets if (value := resolve_secret(name)) is not None
    ]
    if cfg.run_logger:
        cfg.run_logger(
            {
                "event": "run.started",
                "run_id": cfg.session_id,
                "workflow": document.document.name,
            }
        )
        for task in document.do:
            cfg.run_logger({"event": "task.enter", "run_id": cfg.session_id, "task": task.name})
    sessions = _session_service_for(cfg)
    existing = await sessions.get_session(
        app_name=document.document.name,
        user_id=cfg.user_id,
        session_id=cfg.session_id,
    )
    if existing is None:
        await sessions.create_session(
            app_name=document.document.name,
            user_id=cfg.user_id,
            session_id=cfg.session_id,
            state=input or {},
        )
    active_memory_service = cfg.memory_service or memory_service_for_document(document, os.environ)
    runner = Runner(
        node=workflow,
        app_name=document.document.name,
        session_service=sessions,
        memory_service=active_memory_service,
    )
    message = cfg.message or types.Content(
        role="user",
        parts=[types.Part(text="Run workflow")],
    )
    try:
        started = time.monotonic()
        events: list[Any] = []
        state = dict(input or {})
        interval = cfg.checkpoint_interval
        if interval < 0:
            raise ValueError("checkpoint_interval must be non-negative")
        if cfg.token_budget is not None and cfg.token_budget < 0:
            raise ValueError("token_budget must be non-negative")
        consumed_tokens = 0
        async for event in runner.run_async(
            user_id=cfg.user_id,
            session_id=cfg.session_id,
            new_message=message,
        ):
            events.append(event)
            consumed_tokens += _event_token_count(event)
            if cfg.token_budget is not None and consumed_tokens > cfg.token_budget:
                raise RuntimeError(
                    f"workflow token budget exceeded: {consumed_tokens}>{cfg.token_budget}"
                )
            if cfg.event_sink is not None:
                delivered = cfg.event_sink(event)
                if hasattr(delivered, "__await__"):
                    await delivered
            if event.actions:
                state.update(event.actions.state_delta or {})
            if cfg.history is not None:
                await _call_history_method(
                    cfg.history,
                    "record_event",
                    cfg.session_id,
                    _event_log_entry(event, secret_values),
                )
            if (
                cfg.history is not None
                and interval
                and len(events) % interval == 0
                and not event.error_code
                and not event.error_message
            ):
                await _call_history_method(
                    cfg.history,
                    "checkpoint",
                    cfg.session_id,
                    state=redact(state, secret_values),
                    index=len(events),
                    task=_event_task_name(event),
                )
        if active_memory_service is not None:
            session = await sessions.get_session(
                app_name=document.document.name,
                user_id=cfg.user_id,
                session_id=cfg.session_id,
            )
            if session is not None:
                await active_memory_service.add_session_to_memory(session)
        if isinstance(document.output, dict) and document.output.get("as") is not None and events:
            # Document-level `output.as` shapes the workflow's final output;
            # the expression sees the accumulated context and the raw output.
            events[-1].output = evaluate(
                document.output["as"], {**state, "output": events[-1].output}
            )
    except WorkflowSuspended as suspension:
        if cfg.history is None:
            raise
        await _call_history_method(
            cfg.history,
            "suspend",
            cfg.session_id,
            state=redact(state, secret_values),
            index=len(events),
            task=suspension.task,
            resume_at=suspension.resume_at.isoformat(),
            reason=suspension.reason,
        )
        if cfg.run_logger:
            cfg.run_logger(
                {
                    "event": "run.suspended",
                    "run_id": cfg.session_id,
                    "task": suspension.task,
                    "resume_at": suspension.resume_at.isoformat(),
                }
            )
        return events
    except Exception as error:
        safe_error = RuntimeError(str(redact(str(error), secret_values)))
        if cfg.history is not None:
            await _call_history_method(
                cfg.history,
                "finish",
                cfg.session_id,
                state=redact(state, secret_values),
                error=safe_error,
            )
        if cfg.run_logger:
            cfg.run_logger(
                {
                    "event": "run.failed",
                    "run_id": cfg.session_id,
                    "error": str(safe_error),
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                }
            )
        raise
    if cfg.history is not None:
        await _call_history_method(
            cfg.history,
            "finish",
            cfg.session_id,
            state=redact(state, secret_values),
            output=redact(events[-1].output if events else None, secret_values),
        )
    if cfg.run_logger:
        for event in events:
            cfg.run_logger(
                {
                    "event": "task.exit",
                    "run_id": cfg.session_id,
                    "task": event.author,
                    "output": redact(event.output, secret_values),
                    "state_delta": redact(
                        event.actions.state_delta if event.actions else {}, secret_values
                    ),
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                    "route": getattr(event.actions, "escalate", None) if event.actions else None,
                }
            )
        cfg.run_logger(
            {
                "event": "run.completed",
                "run_id": cfg.session_id,
                "workflow": document.document.name,
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
            }
        )
    if cfg.telemetry:
        cfg.telemetry.record_run(document.document.name, cfg.session_id, events)
    return events


async def _load_prior_run(history: Any, session_id: str) -> Any | None:
    """Fetch a prior run record, returning ``None`` when it does not exist."""
    try:
        return await _call_history_method(history, "get", session_id)
    except KeyError:
        return None


def _session_service_for(config: RunConfig) -> Any:
    """Build the ADK session service for the configured backend."""
    backend = config.session_backend or os.environ.get("WORKFLOW_SESSION_BACKEND", "inmemory")
    if backend == "inmemory":
        return InMemorySessionService()
    if backend in {"database", "sqlite"}:
        from google.adk.sessions.database_session_service import DatabaseSessionService

        database_url = os.environ.get(
            "WORKFLOW_SESSION_DATABASE_URL", "sqlite+aiosqlite:///workflow-sessions.db"
        )
        return DatabaseSessionService(db_url=database_url)
    if backend == "vertex":
        from google.adk.sessions.vertex_ai_session_service import VertexAiSessionService

        return VertexAiSessionService(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("GOOGLE_CLOUD_LOCATION"),
            agent_engine_id=os.environ.get("WORKFLOW_VERTEX_AGENT_ENGINE_ID"),
        )
    raise ValueError(f"unsupported session backend: {backend!r}")


def _event_task_name(event: Any) -> str | None:
    node_info = getattr(event, "node_info", None)
    path = getattr(node_info, "path", None) if node_info else None
    if not path:
        return None
    return str(path).rsplit("/", 1)[-1].split("@", 1)[0]


def _event_token_count(event: Any) -> int:
    """Read ADK usage metadata without requiring a specific event version."""
    usage = getattr(event, "usage_metadata", None)
    if usage is None:
        return 0
    return int(getattr(usage, "total_token_count", 0) or 0)


def _event_log_entry(event: Any, secrets: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    """Build a redacted event-log entry.

    Event logs are persisted (SQLite/PostgreSQL), so secrets that appear in
    outputs or state deltas are redacted before storage. Resumed runs read only
    checkpoint state, which is already redacted, so secrets stay out of
    resumable state by reference rather than by value.
    """
    return {
        "task": _event_task_name(event),
        "output": redact(event.output, secrets),
        "state_delta": redact(event.actions.state_delta if event.actions else {}, secrets),
        "error": redact(event.error_message or event.error_code, secrets),
    }


def _resume_task_list(items: list[Any], checkpoint: str) -> list[Any] | None:
    """Slice a task list so execution resumes after ``checkpoint``.

    The checkpoint may live inside a nested ``do``/``try``/``fork``/``switch``
    body; the containing list is sliced at the checkpoint and every ancestor is
    kept (with its body replaced) so the surrounding control flow is preserved.
    Returns ``None`` when the checkpoint is not found.
    """
    for index, item in enumerate(items):
        if item.name == checkpoint:
            return items[index + 1 :]
        task = item.task
        for key in ("do", "try_"):
            children = getattr(task, key, None)
            if children and (sliced := _resume_task_list(children, checkpoint)) is not None:
                replacement = item.model_copy(deep=True)
                setattr(replacement.task, key, sliced)
                return [replacement, *items[index + 1 :]]
        catch = task.catch
        if isinstance(catch, dict):
            children = catch.get("do", [])
            if children and (sliced := _resume_task_list(children, checkpoint)) is not None:
                replacement = item.model_copy(deep=True)
                replacement.task.catch = {**catch, "do": sliced}
                return [replacement, *items[index + 1 :]]
        fork = task.fork
        if isinstance(fork, dict):
            for branch_index, branch in enumerate(fork.get("branches", [])):
                if (sliced := _resume_raw_task_list([branch], checkpoint)) is not None:
                    replacement = item.model_copy(deep=True)
                    branches = list(fork.get("branches", []))
                    branches[branch_index] = sliced[0]
                    replacement.task.fork = {
                        **fork,
                        "branches": branches,
                    }
                    return [replacement, *items[index + 1 :]]
        for case in task.switch or []:
            if not isinstance(case, dict):
                continue
            case_name, configuration = next(iter(case.items()))
            children = configuration.get("do", []) if isinstance(configuration, dict) else []
            if children and (sliced := _resume_task_list(children, checkpoint)) is not None:
                replacement = item.model_copy(deep=True)
                updated_case = {**case, case_name: {**configuration, "do": sliced}}
                replacement.task.switch = [
                    updated_case if candidate is case else candidate for candidate in task.switch
                ]
                return [replacement, *items[index + 1 :]]
    return None


def _raw_task_mapping(item: Any) -> dict[str, Any] | None:
    """Return the raw task mapping from a named task item, when available."""
    if not isinstance(item, dict):
        return None
    if isinstance(item.get("task"), dict):
        return item["task"]
    if len(item) == 1:
        candidate = next(iter(item.values()))
        if isinstance(candidate, dict):
            return candidate
    return None


def _resume_raw_task_list(items: list[Any], checkpoint: str) -> list[Any] | None:
    """Resume a raw task list without normalizing unrelated branch entries."""
    from copy import deepcopy

    from openworkflow_adk.models import TaskItem

    for index, raw_item in enumerate(items):
        item = TaskItem.model_validate(raw_item)
        if item.name == checkpoint:
            return items[index + 1 :]
        raw_task = _raw_task_mapping(raw_item)
        if raw_task is None:
            continue
        for key in ("do", "try"):
            children = raw_task.get(key)
            if (
                isinstance(children, list)
                and (sliced := _resume_raw_task_list(children, checkpoint)) is not None
            ):
                replacement = deepcopy(raw_item)
                _raw_task_mapping(replacement)[key] = sliced
                return [replacement, *items[index + 1 :]]
        catch = raw_task.get("catch")
        if isinstance(catch, dict) and isinstance(catch.get("do"), list):
            sliced = _resume_raw_task_list(catch["do"], checkpoint)
            if sliced is not None:
                replacement = deepcopy(raw_item)
                replacement_task = _raw_task_mapping(replacement)
                replacement_task["catch"] = {**catch, "do": sliced}
                return [replacement, *items[index + 1 :]]
        fork = raw_task.get("fork")
        if isinstance(fork, dict) and isinstance(fork.get("branches"), list):
            for branch_index, branch in enumerate(fork["branches"]):
                sliced = _resume_raw_task_list([branch], checkpoint)
                if sliced is not None:
                    replacement = deepcopy(raw_item)
                    replacement_task = _raw_task_mapping(replacement)
                    branches = list(fork["branches"])
                    branches[branch_index] = sliced[0]
                    replacement_task["fork"] = {**fork, "branches": branches}
                    return [replacement, *items[index + 1 :]]
        for case in raw_task.get("switch") or []:
            if not isinstance(case, dict):
                continue
            case_name, configuration = next(iter(case.items()))
            children = configuration.get("do") if isinstance(configuration, dict) else None
            if isinstance(children, list):
                sliced = _resume_raw_task_list(children, checkpoint)
                if sliced is not None:
                    replacement = deepcopy(raw_item)
                    replacement_task = _raw_task_mapping(replacement)
                    replacement_task["switch"] = [
                        {case_name: {**configuration, "do": sliced}}
                        if candidate is case
                        else candidate
                        for candidate in raw_task.get("switch") or []
                    ]
                    return [replacement, *items[index + 1 :]]
    return None


async def run(document: OpenWorkflowDocument, input: dict[str, Any] | None = None) -> list[Any]:
    """Public library entrypoint for an in-memory workflow run."""
    return await run_workflow(document, input)


def _iter_tasks(items: list[Any]) -> Any:
    """Recursively yield TaskItem instances from a task list and nested bodies."""
    from openworkflow_adk.models import TaskItem

    for item in items:
        if isinstance(item, dict):
            item = TaskItem.model_validate(item)
        yield item
        task = item.task
        for child in [*(task.do or []), *(task.try_ or [])]:
            yield from _iter_tasks([child])
        catch = task.catch
        if isinstance(catch, dict):
            yield from _iter_tasks(catch.get("do", []))
        if isinstance(task.fork, dict):
            for branch in task.fork.get("branches", []):
                yield from _iter_tasks([branch])


def memory_service_for_document(
    document: OpenWorkflowDocument,
    environ: Mapping[str, str] | None = None,
) -> BaseMemoryService | None:
    """Build the first referenced memory backend for a workflow host.

    ADK's Runner exposes one memory service per run. Workflows that need
    multiple stores should use separate hosts until a routing memory service
    is introduced.
    """
    for item in _iter_tasks(document.do):
        agent_config = item.task.effective_agent()
        if agent_config and agent_config.memory:
            config = resolve_memory_config(
                agent_config.memory,
                document.effective_memories(),
                environ=environ,
            )
            return create_memory_service(config)
    return None


async def run_scheduled(
    document: OpenWorkflowDocument,
    input: dict[str, Any] | None = None,
    *,
    broker: Broker | None = None,
    max_runs: int | None = None,
) -> list[list[Any]]:
    """Run a scheduled workflow, optionally stopping after ``max_runs`` triggers."""
    results: list[list[Any]] = []
    async for _ in trigger_events(document, broker):
        results.append(await run_workflow(document, input, broker=broker))
        if max_runs is not None and len(results) >= max_runs:
            break
    return results
