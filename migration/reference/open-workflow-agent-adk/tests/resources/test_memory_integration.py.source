import os

import pytest
from google.adk.memory.base_memory_service import MemoryEntry
from google.genai import types

from openworkflow_adk import MemoryConfig
from openworkflow_adk.internal import create_memory_service

pytestmark = pytest.mark.skipif(
    os.environ.get("WORKFLOW_RUN_INTEGRATION_TESTS") != "1",
    reason="set WORKFLOW_RUN_INTEGRATION_TESTS=1 to run Docker-backed memory tests",
)


async def test_redis_memory_round_trip() -> None:
    from testcontainers.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as container:
        url = f"redis://{container.get_container_host_ip()}:{container.get_exposed_port(6379)}/0"
        service = create_memory_service(MemoryConfig(type="redis", connection=url))
        await service.add_memory(
            app_name="demo",
            user_id="user",
            memories=[MemoryEntry(content=types.Content(parts=[types.Part(text="likes redis")]))],
        )
        result = await service.search_memory(app_name="demo", user_id="user", query="redis")
        await service.close()

    assert len(result.memories) == 1


async def test_postgres_memory_round_trip() -> None:
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as container:
        url = container.get_connection_url(driver="asyncpg")
        service = create_memory_service(MemoryConfig(type="postgres", connection=url))
        await service.add_memory(
            app_name="demo",
            user_id="user",
            memories=[
                MemoryEntry(content=types.Content(parts=[types.Part(text="likes postgres")]))
            ],
        )
        result = await service.search_memory(app_name="demo", user_id="user", query="postgres")
        await service.close()

    assert len(result.memories) == 1
