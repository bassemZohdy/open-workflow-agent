from __future__ import annotations

import httpx
import pytest
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices


def _config(**a2a: object) -> RuntimeConfig:
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
    base["a2a"] = {"enabled": True, **a2a}
    return RuntimeConfig.model_validate(base)


def _make_app(tmp_path, config: RuntimeConfig):
    services = RuntimeServices(config, model=FakeModel({"response": "ok"}), database_root=tmp_path)
    return create_app(config=config, services=services)


HEADERS = {"a2a-version": "1.0"}


def _send(client: httpx.AsyncClient, skill_id: str | None) -> httpx.Response:
    message = {"role": "ROLE_USER", "parts": [{"text": "run"}]}
    if skill_id is not None:
        message["metadata"] = {"skillId": skill_id}
    return client.post(
        "/a2a",
        headers=HEADERS,
        json={"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {"message": message}},
    )


@pytest.mark.asyncio
async def test_declared_skills_are_advertised_and_routed(tmp_path) -> None:
    config = _config(
        skills=[
            {"id": "a", "workflow": "skill-a"},
            {"id": "b", "name": "Skill B", "workflow": "skill-b"},
        ]
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

            first = await _send(client, "a")
            assert first.json()["result"]["message"]["parts"][0]["text"] == '{"skill": "a"}'

            second = await _send(client, "b")
            assert second.json()["result"]["message"]["parts"][0]["text"] == '{"skill": "b"}'


@pytest.mark.asyncio
async def test_unknown_skill_id_fails_closed(tmp_path) -> None:
    config = _config(skills=[{"id": "a", "workflow": "skill-a"}])
    app = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await _send(client, "not-a-skill")
            body = response.json()
            assert body["error"]["code"] == -32602
            assert "not-a-skill" in body["error"]["message"]

            missing = await _send(client, None)
            assert "skillId" in missing.json()["error"]["message"]


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
                            "role": "ROLE_USER",
                            "parts": [{"text": "hello"}],
                        }
                    },
                },
            )
            assert response.json()["result"]["message"]["parts"][0]["text"] == "implicit-ok"
