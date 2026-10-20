from __future__ import annotations

import httpx
import pytest
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices


def _config(transport: str = "jsonrpc", **a2a: object) -> RuntimeConfig:
    base = {
        "model": {"provider": "fake", "name": "fake/default"},
        "workflow": {
            "catalog": [
                {
                    "document": {
                        "dsl": "1.0.3",
                        "namespace": "skills",
                        "name": "skill-a",
                        "version": "1.0.0",
                    },
                    "do": [{"mark": {"set": {"skill": "a"}}}],
                },
                {
                    "document": {
                        "dsl": "1.0.3",
                        "namespace": "skills",
                        "name": "skill-b",
                        "version": "1.0.0",
                    },
                    "do": [{"mark": {"set": {"skill": "b"}}}],
                },
            ]
        },
    }
    base["a2a"] = {"enabled": True, "transport": transport, **a2a}
    return RuntimeConfig.model_validate(base)


def _make_app(tmp_path, config: RuntimeConfig):
    services = RuntimeServices(config, model=FakeModel({"response": "ok"}), database_root=tmp_path)
    return create_app(config=config, services=services)


HEADERS = {"a2a-version": "1.0"}


def _send(client: httpx.AsyncClient, skill_id: str | None, transport: str) -> httpx.Response:
    message = {"messageId": "fixture-message", "role": "ROLE_USER", "parts": [{"text": "run"}]}
    if skill_id is not None:
        message["metadata"] = {"skillId": skill_id}
    if transport == "jsonrpc":
        return client.post(
            "/a2a",
            headers=HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "SendMessage",
                "params": {"message": message},
            },
        )
    return client.post(
        "/a2a/message:send",
        headers=HEADERS,
        json={"message": message},
    )


def _send_body(response: httpx.Response, transport: str) -> dict[str, object]:
    body = response.json()
    return body["result"] if transport == "jsonrpc" else body


@pytest.mark.asyncio
@pytest.mark.parametrize("transport_name", ["jsonrpc", "http_json"])
async def test_declared_skills_are_advertised_and_routed(tmp_path, transport_name: str) -> None:
    config = _config(
        transport=transport_name,
        skills=[
            {"id": "a", "workflow": "skill-a"},
            {"id": "b", "name": "Skill B", "workflow": "skill-b"},
        ],
    )
    app = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            capabilities = (await client.get("/v1/capabilities")).json()
            assert capabilities["features"]["a2a"]["skills"] == ["a", "b"]

            card = (await client.get("/.well-known/agent-card.json", headers=HEADERS)).json()
            assert [skill["id"] for skill in card["skills"]] == ["a", "b"]
            assert card["skills"][1]["name"] == "Skill B"

            first = await _send(client, "a", transport_name)
            assert first.status_code == 200, first.text
            first_task = _send_body(first, transport_name)["task"]
            assert first_task["status"]["state"] == "TASK_STATE_COMPLETED"
            assert first_task["artifacts"][0]["parts"][0]["data"] == {"skill": "a"}

            second = await _send(client, "b", transport_name)
            assert second.status_code == 200, second.text
            second_task = _send_body(second, transport_name)["task"]
            assert second_task["status"]["state"] == "TASK_STATE_COMPLETED"
            assert second_task["artifacts"][0]["parts"][0]["data"] == {"skill": "b"}
            assert first_task["id"] != second_task["id"]
            assert app.state.services.model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("transport_name", ["jsonrpc", "http_json"])
async def test_unknown_skill_id_fails_closed(tmp_path, transport_name: str) -> None:
    config = _config(transport=transport_name, skills=[{"id": "a", "workflow": "skill-a"}])
    app = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            invocations_before = app.state.services.invocations.connection.execute(
                "SELECT COUNT(*) FROM invocations"
            ).fetchone()[0]
            response = await _send(client, "not-a-skill", transport_name)
            assert (
                app.state.services.invocations.connection.execute(
                    "SELECT COUNT(*) FROM invocations"
                ).fetchone()[0]
                == invocations_before
            )
            assert app.state.services.model.calls == []
            body = response.json()
            if transport_name == "jsonrpc":
                assert body["error"]["code"] == -32602
                assert "not-a-skill" in body["error"]["message"]
            else:
                assert response.status_code == 422
                assert body["error"]["code"] == 3
                assert body["error"]["details"][0]["reason"] == "INVALID_PARAMS"
                assert body["error"]["details"][0]["metadata"]["owaCode"] == "skill_not_found"
                assert "not-a-skill" in body["error"]["message"]

            missing = await _send(client, None, transport_name)
            assert (
                app.state.services.invocations.connection.execute(
                    "SELECT COUNT(*) FROM invocations"
                ).fetchone()[0]
                == invocations_before
            )
            assert app.state.services.model.calls == []
            missing_body = missing.json()
            if transport_name == "jsonrpc":
                assert "skillId" in missing_body["error"]["message"]
            else:
                assert missing.status_code == 422
                assert missing_body["error"]["code"] == 3
                assert missing_body["error"]["details"][0]["reason"] == "INVALID_PARAMS"
                assert (
                    missing_body["error"]["details"][0]["metadata"]["owaCode"] == "skill_not_found"
                )
                assert "skillId" in missing_body["error"]["message"]


@pytest.mark.asyncio
async def test_ambiguous_or_missing_skill_workflows_fail_at_startup(tmp_path) -> None:
    config = _config(skills=[{"id": "a", "workflow": "not-registered"}])
    app = _make_app(tmp_path, config)
    with pytest.raises(Exception, match="not uniquely registered"):
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.asyncio
async def test_without_declared_skills_the_main_workflow_stays_implicit(tmp_path) -> None:
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True}})
    services = RuntimeServices(
        config, model=FakeModel({"response": "implicit-ok"}), database_root=tmp_path
    )
    app = create_app(config=config, services=services)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            card = (await client.get("/.well-known/agent-card.json", headers=HEADERS)).json()
            assert [skill["id"] for skill in card["skills"]] == ["workflow"]
            response = await client.post(
                "/a2a",
                headers=HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "SendMessage",
                    "params": {
                        "message": {
                            "messageId": "fixture-message",
                            "role": "ROLE_USER",
                            "parts": [{"text": "hello"}],
                        }
                    },
                },
            )
            assert response.json()["result"]["message"]["parts"][0]["text"] == "implicit-ok"
