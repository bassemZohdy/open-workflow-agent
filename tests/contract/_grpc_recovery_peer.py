"""Test-only independent pinned unary receiver with a durable business ledger."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from _process_recovery_worker import write_json
from google.protobuf import (
    descriptor_pb2,
    descriptor_pool,
    json_format,
    message_factory,
    struct_pb2,
)
from open_workflow_agent.grpc_client import (
    APPLICATION_STATUS_HEADER,
    APPLICATION_STATUS_PROFILE,
    verify_sdk,
)

PROTO = b"""syntax = "proto3"; package stopped;
import "google/protobuf/struct.proto";
message Payload { google.protobuf.Value value = 1; }
service Peer { rpc Call(Payload) returns (Payload); }
"""
URI = "https://metadata.test/stopped.proto"


def descriptor_bytes():
    file = descriptor_pb2.FileDescriptorProto(
        name="stopped.proto", package="stopped", syntax="proto3"
    )
    file.dependency.append("google/protobuf/struct.proto")
    file.message_type.add(name="Payload").field.add(
        name="value", number=1, type=11, label=1, type_name=".google.protobuf.Value"
    )
    file.service.add(name="Peer").method.add(
        name="Call", input_type=".stopped.Payload", output_type=".stopped.Payload"
    )
    files = descriptor_pb2.FileDescriptorSet()
    files.file.add().ParseFromString(struct_pb2.DESCRIPTOR.serialized_pb)
    files.file.add().CopyFrom(file)
    return files.SerializeToString(deterministic=True)


def binding(endpoint):
    raw = descriptor_bytes()
    return {
        "proto_uri": URI,
        "proto_filename": "stopped.proto",
        "proto_source": PROTO.decode("utf-8"),
        "proto_sha256": hashlib.sha256(PROTO).hexdigest(),
        "descriptor_set_base64": base64.b64encode(raw).decode("ascii"),
        "descriptor_sha256": hashlib.sha256(raw).hexdigest(),
        "endpoint": endpoint,
        "service": "stopped.Peer",
        "method": "Call",
        "application_status_profile": True,
        "allow_insecure_loopback": True,
    }


def commits(directory):
    with closing(sqlite3.connect(directory / "receiver.sqlite3", timeout=3)) as connection:
        return connection.execute(
            "SELECT operation_id,body,pid FROM commits ORDER BY rowid"
        ).fetchall()


async def serve(directory):
    verify_sdk()
    grpc = importlib.import_module("grpc")
    scenario = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
    pool = descriptor_pool.DescriptorPool()
    for file in descriptor_pb2.FileDescriptorSet.FromString(descriptor_bytes()).file:
        pool.Add(file)
    message = message_factory.GetMessageClass(pool.FindMessageTypeByName("stopped.Payload"))
    with closing(sqlite3.connect(directory / "receiver.sqlite3")) as connection:
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("CREATE TABLE commits(operation_id TEXT,body TEXT,pid INTEGER)")
        connection.commit()

    async def invoke(request, context):
        metadata = dict(context.invocation_metadata())
        operation_id = metadata["x-owa-operation-id"]
        assert operation_id == metadata["idempotency-key"]
        body = json_format.MessageToDict(request, descriptor_pool=pool)["value"]
        with closing(sqlite3.connect(directory / "receiver.sqlite3", timeout=3)) as connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                "INSERT INTO commits VALUES(?,?,?)",
                (operation_id, json.dumps(body, sort_keys=True), os.getpid()),
            )
            connection.commit()
        write_json(
            directory / "receiver-committed.json",
            {"pid": os.getpid(), "operation_id": operation_id},
        )
        if scenario["boundary"] == "pending":
            while not (directory / "receiver-release.json").exists():
                await asyncio.sleep(0.02)
        if scenario["outcome"] == "fault":
            proof = {
                "profile": APPLICATION_STATUS_PROFILE,
                "operation_key": operation_id,
                "status": "NOT_FOUND",
            }
            context.set_trailing_metadata(((APPLICATION_STATUS_HEADER, json.dumps(proof)),))
            await context.abort(grpc.StatusCode.NOT_FOUND, "private controlled receiver fault")
        return request

    server = grpc.aio.server(options=(("grpc.enable_retries", 0),))
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                "stopped.Peer",
                {
                    "Call": grpc.unary_unary_rpc_method_handler(
                        invoke,
                        request_deserializer=message.FromString,
                        response_serializer=lambda value: value.SerializeToString(
                            deterministic=True
                        ),
                    )
                },
            ),
        )
    )
    port = server.add_insecure_port("127.0.0.1:0")
    assert port > 0
    await asyncio.wait_for(server.start(), 3)
    write_json(
        directory / "receiver-ready.json",
        {"endpoint": f"http://127.0.0.1:{port}", "port": port, "pid": os.getpid()},
    )
    try:
        await asyncio.Event().wait()
    finally:
        await asyncio.wait_for(server.stop(0), 3)
        write_json(directory / "receiver-cleaned.json", {"pid": os.getpid()})


if __name__ == "__main__":
    directory = Path(sys.argv[1])
    try:
        asyncio.run(serve(directory))
    except BaseException as error:
        write_json(
            directory / "receiver-failure.json",
            {
                "stage": "receiver",
                "type": type(error).__name__,
                "code": getattr(error, "code", None),
            },
        )
        raise
