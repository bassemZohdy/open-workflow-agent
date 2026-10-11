import sys
from pathlib import Path

import grpc
import respx
from grpc_tools import protoc

from openworkflow_adk import load, run_workflow

PROTO = """
syntax = \"proto3\";
service Echo {
  rpc Ping (PingRequest) returns (PingReply);
}
message PingRequest { string name = 1; }
message PingReply { string message = 1; }
"""

SERVER_PROTO = PROTO.replace("service Echo", "package server;\nservice Echo")


async def test_grpc_call_compiles_proto_and_invokes_reflected_method(tmp_path, monkeypatch) -> None:
    # The egress guard blocks loopback by default; the in-process test server
    # is explicitly allowlisted for this test.
    monkeypatch.setenv("WORKFLOW_EGRESS_ALLOWLIST", "127.0.0.1")
    proto_path = tmp_path / "echo.proto"
    proto_path.write_text(SERVER_PROTO)
    assert (
        protoc.main(
            [
                "grpc_tools.protoc",
                f"-I{tmp_path}",
                f"--python_out={tmp_path}",
                f"--grpc_python_out={tmp_path}",
                str(proto_path),
            ]
        )
        == 0
    )
    sys.path.insert(0, str(tmp_path))
    try:
        import echo_pb2

        async def ping(request, context):
            return echo_pb2.PingReply(message=f"Hello {request.name}")

        server = grpc.aio.server()
        handler = grpc.method_handlers_generic_handler(
            "Echo",
            {
                "Ping": grpc.unary_unary_rpc_method_handler(
                    ping,
                    request_deserializer=echo_pb2.PingRequest.FromString,
                    response_serializer=echo_pb2.PingReply.SerializeToString,
                )
            },
        )
        server.add_generic_rpc_handlers((handler,))
        port = server.add_insecure_port("127.0.0.1:0")
        await server.start()
        try:
            with respx.mock:
                respx.get("https://proto.test/echo.proto").respond(
                    content=PROTO.encode(), headers={"content-type": "text/plain"}
                )
                document = load(
                    {
                        "document": {
                            "dsl": "1.0.3",
                            "namespace": "demo",
                            "name": "grpc",
                            "version": "1.0.0",
                        },
                        "do": [
                            {
                                "ping": {
                                    "call": "grpc",
                                    "with": {
                                        "proto": {
                                            "endpoint": {"uri": "https://proto.test/echo.proto"}
                                        },
                                        "service": {
                                            "name": "Echo",
                                            "host": "127.0.0.1",
                                            "port": port,
                                        },
                                        "method": "Ping",
                                        "arguments": {"name": "Ada"},
                                    },
                                }
                            }
                        ],
                    }
                )
                fixture = load(Path(__file__).parents[2] / "tests" / "fixtures" / "grpc.yaml")
                fixture.do[0].task.with_.update(document.do[0].task.with_)
                document = fixture
                events = await run_workflow(document)
            assert any(event.output == {"message": "Hello Ada"} for event in events)
        finally:
            await server.stop(0)
    finally:
        sys.path.remove(str(tmp_path))
