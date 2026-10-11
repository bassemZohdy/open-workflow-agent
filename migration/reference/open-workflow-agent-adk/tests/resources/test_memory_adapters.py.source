from openworkflow_adk import MemoryConfig, PostgresMemoryService, RedisMemoryService
from openworkflow_adk.internal import create_memory_service


def test_remote_memory_adapters_construct_without_connecting() -> None:
    redis_service = create_memory_service(
        MemoryConfig(type="redis", connection="redis://127.0.0.1:6379/0")
    )
    postgres_service = create_memory_service(
        MemoryConfig(type="postgres", connection="postgresql+asyncpg://user:pass@localhost/db")
    )

    assert isinstance(redis_service, RedisMemoryService)
    assert isinstance(postgres_service, PostgresMemoryService)


def test_postgres_memory_namespace_is_an_identifier() -> None:
    try:
        PostgresMemoryService(
            "postgresql+asyncpg://user:pass@localhost/db", "memory; DROP TABLE users"
        )
    except ValueError as error:
        assert "SQL identifier" in str(error)
    else:
        raise AssertionError("unsafe PostgreSQL namespace was accepted")
