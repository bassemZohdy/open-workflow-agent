import sys

import pytest

from openworkflow_adk.tasks.call import _compile_grpc_proto

PROTO = """
syntax = "proto3";
service Echo {
  rpc Ping (PingRequest) returns (PingReply);
}
message PingRequest { string name = 1; }
message PingReply { string message = 1; }
"""


def test_compile_grpc_proto_uses_unique_hash_suffixed_modules(tmp_path) -> None:
    """Regression: each distinct proto compiles into a uniquely-named module pair."""
    proto_a = PROTO.encode()
    proto_b = (
        PROTO.replace("Echo", "EchoTwo")
        .replace('syntax = "proto3";', 'syntax = "proto3";\npackage beta;')
        .encode()
    )

    sys.path.insert(0, str(tmp_path))
    try:
        mod_a, grpc_a = _compile_grpc_proto(proto_a, str(tmp_path))
        mod_b, grpc_b = _compile_grpc_proto(proto_b, str(tmp_path))

        assert mod_a.__name__ != mod_b.__name__
        assert grpc_a.__name__ != grpc_b.__name__
        assert mod_a.__name__.startswith("owf_grpc_")
        assert grpc_a.__name__.startswith("owf_grpc_")
        assert grpc_a.__name__.endswith("_pb2_grpc")
        # Same bytes produce the same module name (deterministic import).
        mod_a2, grpc_a2 = _compile_grpc_proto(proto_a, str(tmp_path))
        assert mod_a2.__name__ == mod_a.__name__
        assert grpc_a2.__name__ == grpc_a.__name__
    finally:
        sys.path.remove(str(tmp_path))


def test_compile_grpc_proto_surfaces_protoc_errors(tmp_path) -> None:
    """Invalid proto bytes must raise a descriptive ValueError."""
    with pytest.raises(ValueError, match="protoc exit"):
        _compile_grpc_proto(b"not a valid proto", str(tmp_path))
