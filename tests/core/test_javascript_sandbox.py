"""Physical JavaScript execution uses the same controlled process boundary."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from javascript_fixture_policy import javascript_path
from open_workflow_agent.config import SandboxConfig
from open_workflow_agent.errors import (
    SandboxOutputLimitError,
    SandboxPolicyError,
    SandboxProcessError,
    SandboxTimeoutError,
    UnsupportedWorkflowFeature,
)
from open_workflow_agent.sandbox import (
    InternalSandboxBackend,
    SandboxExecutionRequest,
    SandboxSecretReference,
    compile_sandbox_workflow,
)
from sandbox_fixture_policy import supported_process_limits


@pytest.fixture
def backend(tmp_path):
    return InternalSandboxBackend(
        SandboxConfig(
            **supported_process_limits(),
            enabled=True,
            script_runtimes=["javascript"],
            executable_search_path=javascript_path(),
            inherited_environment=["SYSTEMROOT"] if os.name == "nt" else [],
            workspace_root=str(tmp_path / "workspace"),
            timeout_seconds=3,
            memory_bytes=None,
        )
    )


def request(code, **values):
    return SandboxExecutionRequest(
        "javascript-physical", "script", script_language="javascript", script_code=code, **values
    )


@pytest.mark.asyncio
async def test_javascript_preserves_utf8_arguments_stdin_and_private_environment(
    backend, monkeypatch
):
    monkeypatch.setenv("OWA_JS_AMBIENT_SECRET", "ambient-canary")
    monkeypatch.setenv("NODE_OPTIONS", "--require=/missing-ambient-canary.cjs")
    code = """
const fs = require('node:fs');
process.stdout.write(JSON.stringify({
  input: fs.readFileSync(0, 'utf8'), args: process.argv.slice(2),
  secret: process.env.OWA_JS_AMBIENT_SECRET ?? null,
  options: process.env.NODE_OPTIONS ?? null, cwd: process.cwd(),
  home: process.env.HOME, temp: process.env.TEMP, tmp: process.env.TMP,
  value: process.env.OWA_JS_VALUE
}));
process.stderr.write('diagnostic');
"""
    result = await backend.execute(
        request(
            code,
            arguments=("é/雪", "--eval=throw 1"),
            stdin="snow: 雪\n",
            environment=(("OWA_JS_VALUE", "literal"),),
        )
    )
    value = json.loads(result.stdout)
    assert value["input"] == "snow: 雪\n"
    assert value["args"] == ["é/雪", "--eval=throw 1"]
    assert value["secret"] is value["options"] is None
    assert value["cwd"] == value["home"] == value["temp"] == value["tmp"]
    assert value["value"] == "literal"
    assert not Path(value["cwd"]).exists()
    assert result.stderr == "diagnostic" and result.exit_code == 0
    assert list(Path(backend.config.workspace_root).iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "output", "exit"])
async def test_javascript_enforces_common_failure_controls_and_cleanup(backend, failure):
    codes = {
        "timeout": ("setInterval(() => {}, 1000);", SandboxTimeoutError),
        "output": ("process.stdout.write('x'.repeat(4096));", SandboxOutputLimitError),
        "exit": ("process.exit(7);", SandboxProcessError),
    }
    backend.config.timeout_seconds = 0.5 if failure == "timeout" else 3
    backend.config.max_output_bytes = 64
    code, error = codes[failure]
    with pytest.raises(error):
        await backend.execute(request(code))
    assert not backend._active
    assert list(Path(backend.config.workspace_root).iterdir()) == []


@pytest.mark.asyncio
async def test_javascript_cancellation_closes_owned_process_and_workspace(backend):
    task = asyncio.create_task(backend.execute(request("setInterval(() => {}, 1000);")))
    async with asyncio.timeout(2):
        while not backend._active:
            if task.done():
                await task
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await backend.shutdown()
    assert not backend._active
    assert list(Path(backend.config.workspace_root).iterdir()) == []


@pytest.mark.asyncio
async def test_javascript_selected_secret_echo_is_redacted_from_both_streams(
    backend, monkeypatch, caplog
):
    secret = "selected-javascript-credential-must-not-leak"
    monkeypatch.setenv("OWA_JS_SELECTED_SECRET", secret)
    backend.config.secret_environment = ["OWA_JS_SELECTED_SECRET"]
    result = await backend.execute(
        request(
            "process.stdout.write(process.env.TOKEN); process.stderr.write(process.env.TOKEN);",
            environment=(("TOKEN", SandboxSecretReference("OWA_JS_SELECTED_SECRET")),),
        )
    )
    assert result.stdout == result.stderr == "[REDACTED]"
    assert secret not in caplog.text


def test_javascript_interpreter_identity_tracks_physical_bytes(backend, tmp_path):
    executable = tmp_path / ("node.exe" if os.name == "nt" else "node")
    executable.write_bytes(b"first-interpreter")
    executable.chmod(0o755)
    backend.config.executable_search_path = str(tmp_path)
    before = backend.script_runtime_snapshot()["javascript"]
    assert before["sha256"] == hashlib.sha256(b"first-interpreter").hexdigest()
    executable.write_bytes(b"other-interpreter")
    after = backend.script_runtime_snapshot()["javascript"]
    assert after["sha256"] != before["sha256"]


def test_missing_javascript_interpreter_is_not_advertised_or_prepared(backend, tmp_path):
    backend.config.executable_search_path = str(tmp_path)
    assert backend.capabilities()["script"] == {
        "enabled": False,
        "runtimes": [],
        "externalSource": False,
    }
    with pytest.raises(SandboxPolicyError, match="unavailable"):
        backend._prepare_command(request("throw 1"), tmp_path)
    with pytest.raises(SandboxPolicyError, match="runtime is not enabled"):
        backend._prepare_command(replace(request("pass"), script_language="python"), tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(os.name != "nt", reason="Windows event-loop process support")
def test_windows_selector_loop_does_not_advertise_awaited_processes(backend, monkeypatch):
    loop = asyncio.SelectorEventLoop()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(asyncio, "get_running_loop", lambda: loop)
            assert backend.capabilities()["script"]["enabled"] is False
            with pytest.raises(SandboxPolicyError, match="Proactor"):
                backend._validate_awaited_controls()
    finally:
        loop.close()


def test_javascript_background_and_unconfigured_runtime_reject_before_admission(
    backend, monkeypatch
):
    from open_workflow_agent.sandbox import background_qualification

    monkeypatch.setattr(background_qualification, "BACKGROUND_EXECUTION_PROFILE_QUALIFIED", True)
    workflow = {
        "document": {"dsl": "1.0.3", "namespace": "test", "name": "javascript", "version": "1.0.0"},
        "do": [
            {
                "execute": {
                    "run": {"await": False, "script": {"language": "javascript", "code": "throw 1"}}
                }
            }
        ],
    }
    backend.config.background.enabled = True
    with pytest.raises(UnsupportedWorkflowFeature, match="Python job profile"):
        compile_sandbox_workflow(workflow, sandbox=backend.config)
    with pytest.raises(SandboxPolicyError, match="Python jobs"):
        backend.validate_background_request(request("throw 1"))
