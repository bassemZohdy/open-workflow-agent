from google.adk.memory.base_memory_service import MemoryEntry
from google.genai import types

from openworkflow_adk import FileMemoryService, InMemoryMemoryService, MemoryConfig
from openworkflow_adk.internal import create_memory_service


async def test_in_memory_memory_round_trip() -> None:
    service = InMemoryMemoryService()
    await service.add_memory(
        app_name="demo",
        user_id="u1",
        memories=[MemoryEntry(content=types.Content(parts=[types.Part(text="likes tea")]))],
    )

    result = await service.search_memory(app_name="demo", user_id="u1", query="tea")

    assert len(result.memories) == 1


async def test_file_memory_round_trip(tmp_path) -> None:
    path = tmp_path / "memory.json"
    service = FileMemoryService(str(path))
    await service.add_memory(
        app_name="demo",
        user_id="u1",
        memories=[MemoryEntry(content=types.Content(parts=[types.Part(text="likes coffee")]))],
    )
    reopened = create_memory_service(MemoryConfig(type="file", connection=str(path)))

    result = await reopened.search_memory(app_name="demo", user_id="u1", query="coffee")

    assert len(result.memories) == 1


async def test_file_memory_recall_survives_new_service_instance(tmp_path) -> None:
    path = tmp_path / "cross-run-memory.json"
    first_run = create_memory_service(MemoryConfig(type="file", connection=str(path)))
    await first_run.add_memory(
        app_name="workflow",
        user_id="user",
        memories=[
            MemoryEntry(
                content=types.Content(parts=[types.Part(text="customer prefers monthly invoices")])
            )
        ],
    )

    second_run = create_memory_service(MemoryConfig(type="file", connection=str(path)))
    result = await second_run.search_memory(
        app_name="workflow", user_id="user", query="monthly invoices"
    )

    assert len(result.memories) == 1
