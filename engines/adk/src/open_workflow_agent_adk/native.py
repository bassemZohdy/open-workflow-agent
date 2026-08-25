"""Native ADK 2.x dynamic-node invocation for the portable plan."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunsplit

from open_workflow_agent.storage import is_postgresql_datasource

try:
    from google.adk.agents.context import Context
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.adk.sessions.sqlite_session_service import SqliteSessionService
    from google.adk.workflow import FunctionNode
    from google.genai import types

    ADK_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised in the core-only environment
    Context = None  # type: ignore[assignment]
    Runner = None  # type: ignore[assignment]
    InMemorySessionService = None  # type: ignore[assignment]
    SqliteSessionService = None  # type: ignore[assignment]
    FunctionNode = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]
    ADK_AVAILABLE = False

try:
    from google.adk.sessions import DatabaseSessionService as _DatabaseSessionService
except ImportError:  # pragma: no cover - optional ADK db extra
    _DatabaseSessionService = None  # type: ignore[assignment]


class NativeAdkRunner:
    """Execute a plan through an ADK dynamic FunctionNode child."""

    def __init__(self) -> None:
        self.available = ADK_AVAILABLE

    async def invoke(
        self,
        runner: Callable[[Any], Awaitable[Any]],
        input_data: Any,
        *,
        session_id: str,
        user_id: str | None,
        database_path: str | None = None,
        invocation_id: str | None = None,
    ) -> Any:
        if (
            not ADK_AVAILABLE
            or Runner is None
            or InMemorySessionService is None
            or FunctionNode is None
            or Context is None
            or types is None
        ):
            raise RuntimeError("google-adk 2.x is not installed")
        app_name = "open-workflow-agent"
        user = user_id or "owa-user"
        if database_path and is_postgresql_datasource(database_path):
            if _DatabaseSessionService is None:
                raise RuntimeError("PostgreSQL ADK persistence requires google-adk[db] and asyncpg")
            parsed = urlparse(database_path)
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            query.pop("options", None)
            db_url = urlunsplit(
                (
                    "postgresql+asyncpg",
                    parsed.netloc,
                    parsed.path,
                    urlencode(query),
                    parsed.fragment,
                )
            )
            sessions = _DatabaseSessionService(
                db_url,
                connect_args={"server_settings": {"search_path": "owa_adk,public"}},
            )
        elif database_path and SqliteSessionService is not None:
            sessions = SqliteSessionService(database_path)
        else:
            sessions = InMemorySessionService()
        existing = await sessions.get_session(
            app_name=app_name,
            user_id=user,
            session_id=session_id,
        )
        if existing is None:
            await sessions.create_session(
                app_name=app_name,
                user_id=user,
                session_id=session_id,
                state={"owa_input": input_data},
            )
        else:
            existing.state["owa_input"] = input_data

        result_value: Any = None

        async def root_node(ctx: Context) -> Any:
            node_input = ctx.state.get("owa_input", {})

            async def dynamic_node(node_input: Any) -> Any:
                nonlocal result_value
                result_value = await runner(node_input)
                return result_value

            return await ctx.run_node(
                dynamic_node,
                node_input,
                use_as_output=True,
                run_id="owa-plan",
            )

        root = FunctionNode(func=root_node, name="owa_root", rerun_on_resume=True)
        framework_runner = Runner(
            app_name=app_name,
            node=root,
            session_service=sessions,
        )
        message = types.Content(
            role="user",
            parts=[types.Part(text=json.dumps(input_data, ensure_ascii=False, default=str))],
        )
        output = None
        async for event in framework_runner.run_async(
            user_id=user,
            session_id=session_id,
            invocation_id=invocation_id,
            new_message=message,
            yield_user_message=False,
        ):
            if getattr(event, "output", None) is not None:
                output = event.output
        return output if output is not None else result_value

    async def resume(
        self,
        runner: Callable[[Any], Awaitable[Any]],
        resume_input: Any,
        *,
        session_id: str,
        user_id: str | None,
        invocation_id: str,
        database_path: str | None = None,
    ) -> Any:
        """Resume an ADK invocation using its persisted native invocation id."""
        return await self.invoke(
            runner,
            resume_input,
            session_id=session_id,
            user_id=user_id,
            invocation_id=invocation_id,
            database_path=database_path,
        )
