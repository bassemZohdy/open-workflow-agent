from openworkflow_adk import MemoryConfig
from openworkflow_adk.internal import create_memory_service


def test_vertex_memory_uses_adk_native_service() -> None:
    config = MemoryConfig(
        type="vertex",
        extra={
            "project": "p",
            "location": "us-central1",
            "agent_engine_id": "123",
        },
    )
    try:
        service = create_memory_service(config)
    except ImportError as error:
        assert "google-cloud-aiplatform" in str(error)
    else:
        assert service.__class__.__name__ == "VertexAiMemoryBankService"
