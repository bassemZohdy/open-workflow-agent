"""Actual restricted duplex process policy, framing, limits and cleanup."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from open_workflow_agent.config import RuntimeConfig, SandboxConfig
from open_workflow_agent.errors import (
    SandboxOutputLimitError,
    SandboxPolicyError,
    SandboxProcessError,
    SandboxTimeoutError,
)
from open_workflow_agent.sandbox import (
    InternalSandboxBackend,
    SandboxManager,
    SandboxStdioRequest,
    validate_stdio_transport,
)

SERVER = r"""
import json, os, pathlib, subprocess, sys, time
for line in sys.stdin.buffer:
    request = json.loads(line)
    mode = request.get("mode", "probe")
    if mode == "stall":
        time.sleep(60)
    elif mode == "invalid":
        sys.stdout.buffer.write(b"\xff\n"); sys.stdout.buffer.flush()
    elif mode == "partial":
        sys.stdout.buffer.write(b"{}"); sys.stdout.buffer.flush(); time.sleep(60)
    elif mode == "stderr":
        sys.stderr.buffer.write(b"x" * 131072); sys.stderr.buffer.flush()
    elif mode == "oversized":
        sys.stdout.buffer.write(b"x" * 8192 + b"\n"); sys.stdout.buffer.flush()
    elif mode == "exit":
        sys.exit(0)
    elif mode == "response_exit":
        print(json.dumps({"known": None}), flush=True)
        sys.exit(0)
    elif mode == "response_stderr_exit":
        print(json.dumps({"known": None}), flush=True)
        sys.stderr.buffer.write(b"x" * 4096); sys.stderr.buffer.flush()
        sys.exit(0)
    elif mode == "frames":
        for index in range(10000):
            print(json.dumps({"index": index}), flush=True)
    else:
        child = None
        inherited_fd = False
        if "fd" in request:
            try:
                entry = os.fstat(request["fd"])
                inherited_fd = [entry.st_dev, entry.st_ino] == request["fd_identity"]
            except OSError:
                pass
        if mode == "tree":
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        print(json.dumps({"pid": os.getpid(), "child": child.pid if child else None,
            "fd": inherited_fd,
            "cwd": os.getcwd(), "environment": dict(os.environ)}), flush=True)
"""


def configured(tmp_path: Path, *, timeout: float = 3, output: int = 4096):
    config = SandboxConfig.model_validate(
        {
            "enabled": True,
            "workspace_root": str(tmp_path / "workspace"),
            "timeout_seconds": timeout,
            "max_output_bytes": output,
            "file_size_bytes": None if os.name == "nt" else 33_554_432,
            "mcp_stdio": {
                "enabled": True,
                "max_frame_bytes": 4096,
                "max_pending_frames": 2,
                "servers": [
                    {
                        "id": "fixture",
                        "command": sys.executable,
                        "arguments": ["-u", "-c", SERVER],
                        "environment": {"APPROVED": "present"},
                    }
                ],
            },
        }
    )
    backend = InternalSandboxBackend(config)
    return config, backend, SandboxManager(backend)


def request(config, mode="probe", *, timeout=3):
    server = config.mcp_stdio.servers[0]
    return SandboxStdioRequest(
        mode, server.command, tuple(server.arguments), timeout_seconds=timeout
    )


@pytest.mark.asyncio
async def test_complete_response_remains_available_after_process_exit(tmp_path):
    config, backend, manager = configured(tmp_path)
    async with manager.open_stdio(request(config, "response_exit")) as session:
        await session.send_frame(b'{"mode":"response_exit"}')
        # Wait for actual process/pipe EOF, so this tests the queued-frame race
        # independently of whether the parent happened to receive first.
        await asyncio.wait_for(session.process.wait(), timeout=3)
        await asyncio.wait_for(asyncio.shield(session.failure), timeout=3)
        assert json.loads(await session.receive_frame()) == {"known": None}
        with pytest.raises(SandboxProcessError):
            await session.receive_frame()
    assert not backend._stdio_sessions
    assert list(Path(config.workspace_root).iterdir()) == []


@pytest.mark.asyncio
async def test_complete_frame_eof_cannot_hide_stderr_output_violation(tmp_path):
    config, backend, manager = configured(tmp_path, output=512)
    async with manager.open_stdio(request(config, "response_stderr_exit")) as session:
        await session.send_frame(b'{"mode":"response_stderr_exit"}')
        await asyncio.wait_for(session.process.wait(), timeout=3)
        await asyncio.wait_for(asyncio.gather(*session.tasks[:2]), timeout=3)
        with pytest.raises(SandboxOutputLimitError):
            await session.receive_frame()
    assert not backend._stdio_sessions


def alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            assert kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    status = Path(f"/proc/{pid}/stat")
    if status.exists() and status.read_text().split()[2] == "Z":
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.asyncio
async def test_duplex_process_uses_only_approved_environment_and_dedicated_workspace(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MODEL_API_KEY", "ambient-must-not-reach-child")
    config, backend, manager = configured(tmp_path)
    descriptor = os.open(tmp_path / "not-inherited", os.O_CREAT | os.O_RDWR, 0o600)
    os.set_inheritable(descriptor, True)
    entry = os.fstat(descriptor)
    try:
        async with manager.open_stdio(request(config)) as session:
            await session.send_frame(
                json.dumps(
                    {"mode": "probe", "fd": descriptor, "fd_identity": [entry.st_dev, entry.st_ino]}
                ).encode()
            )
            first = json.loads(await session.receive_frame())
            await session.send_frame(b'{"mode":"probe"}')
            second = json.loads(await session.receive_frame())
            assert first["pid"] == second["pid"] and first["fd"] is False
            assert first["environment"]["APPROVED"] == "present"
            assert "MODEL_API_KEY" not in first["environment"]
            assert first["cwd"] != str(Path.cwd())
            assert Path(first["cwd"]).parent == Path(config.workspace_root)
            assert first["environment"]["HOME"] == first["cwd"]
    finally:
        os.close(descriptor)
    assert not alive(first["pid"])
    assert not Path(first["cwd"]).exists() and not backend._stdio_sessions


@pytest.mark.parametrize(
    "damage", ["disabled", "external", "command", "arguments", "environment", "dynamic"]
)
def test_stdio_policy_rejects_unapproved_values_before_launch(tmp_path, damage):
    config, _, _ = configured(tmp_path)
    server = config.mcp_stdio.servers[0]
    transport = {"stdio": {"command": server.command, "arguments": list(server.arguments)}}
    if damage == "disabled":
        config.mcp_stdio.enabled = False
    elif damage == "external":
        config.docker.allowed_images = ["fixture@sha256:" + "0" * 64]
        config.backend = "docker"
    elif damage == "command":
        transport["stdio"]["command"] = "/unapproved/server"
    elif damage == "arguments":
        transport["stdio"]["arguments"] = ["-c", "unapproved"]
    elif damage == "environment":
        transport["stdio"]["environment"] = {"MODEL_API_KEY": "forged"}
    else:
        transport["stdio"]["command"] = "${ .command }"
    with pytest.raises(SandboxPolicyError):
        validate_stdio_transport(transport, config)
    assert not Path(config.workspace_root).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,error",
    [
        ("stall", SandboxTimeoutError),
        ("partial", SandboxTimeoutError),
        ("invalid", SandboxProcessError),
        ("oversized", SandboxOutputLimitError),
        ("stderr", SandboxOutputLimitError),
        ("exit", SandboxProcessError),
    ],
)
async def test_stdio_bad_streams_are_bounded_and_cleaned(tmp_path, mode, error):
    config, backend, manager = configured(tmp_path)
    timeout = 0.5 if mode in {"stall", "partial"} else 5.0
    with pytest.raises(error):
        async with manager.open_stdio(request(config, mode, timeout=timeout)) as session:
            await session.send_frame(json.dumps({"mode": mode}).encode())
            await session.receive_frame()
    assert not backend._stdio_sessions and not backend._stdio_starting
    assert list(Path(config.workspace_root).iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["cancel", "shutdown", "task_cancel"])
async def test_stdio_descendants_are_owned_until_cancel_or_shutdown(tmp_path, control):
    config, backend, manager = configured(tmp_path)
    witness = asyncio.get_running_loop().create_future()

    async def run():
        async with manager.open_stdio(request(config, "tree")) as session:
            await session.send_frame(b'{"mode":"tree"}')
            result = json.loads(await session.receive_frame())
            witness.set_result(result)
            await session.receive_frame()

    task = asyncio.create_task(run())
    try:
        result = await asyncio.wait_for(asyncio.shield(witness), timeout=3)
        assert alive(result["pid"]) and alive(result["child"])
        if control == "task_cancel":
            task.cancel()
        elif control == "cancel":
            await backend.cancel("tree")
        else:
            await backend.shutdown()
        with pytest.raises((asyncio.CancelledError, SandboxProcessError)):
            await asyncio.wait_for(task, timeout=3)
        assert not alive(result["pid"]) and not alive(result["child"])
        assert not Path(result["cwd"]).exists()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await backend.shutdown()


@pytest.mark.asyncio
async def test_stdio_frame_queue_and_cumulative_output_remain_bounded(tmp_path):
    config, backend, manager = configured(tmp_path, output=512)
    with pytest.raises(SandboxOutputLimitError):
        async with manager.open_stdio(request(config, "frames")) as session:
            await session.send_frame(b'{"mode":"frames"}')
            await asyncio.sleep(0.05)
            assert session.frames.qsize() <= 2
            while True:
                await session.receive_frame()
    assert not backend._stdio_sessions


def test_outbound_profiles_allow_oauth_mtls_but_operator_profiles_remain_header_only():
    profile = {
        "type": "mtls",
        "certificate": {"from_env": "CERT"},
        "private_key": {"from_env": "KEY"},
    }
    value = {
        "security": {"profiles": {"selected": profile}},
        "protocols": {"security_profile": "selected"},
        "tools": [{"type": "mcp", "security_profile": "selected"}],
    }
    assert RuntimeConfig.model_validate(value).tools[0].security_profile == "selected"
    value["server"] = {"api_security_profile": "selected"}
    with pytest.raises(ValueError):
        RuntimeConfig.model_validate(value)


@pytest.mark.asyncio
async def test_stdio_send_backpressure_cannot_extend_whole_deadline(tmp_path):
    config, backend, manager = configured(tmp_path)
    with pytest.raises(SandboxTimeoutError):
        async with manager.open_stdio(request(config, "send-stall", timeout=0.5)) as session:
            await session.send_frame(b'{"mode":"stall"}')
            for _ in range(200):
                await session.send_frame(b"x" * 4096)
    assert not backend._stdio_sessions and not backend._stdio_starting
    assert list(Path(config.workspace_root).iterdir()) == []


def test_stdio_platform_limit_rejection_precedes_process_creation(tmp_path, monkeypatch):
    config, backend, _ = configured(tmp_path)
    if os.name == "nt":
        config.file_size_bytes = 1024
    else:
        import resource

        monkeypatch.setattr(resource, "RLIMIT_FSIZE", None)
    server = config.mcp_stdio.servers[0]
    with pytest.raises(SandboxPolicyError, match="resource limits"):
        validate_stdio_transport(
            {"stdio": {"command": server.command, "arguments": list(server.arguments)}}, config
        )
    assert not Path(config.workspace_root).exists()
    assert backend.capabilities()["mcpStdio"]["enabled"] is False
