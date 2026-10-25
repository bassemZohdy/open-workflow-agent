#!/usr/bin/env bash
set -euo pipefail

engine="${1:?engine is required}"
image="${2:?image is required}"

docker run --rm --interactive --read-only --network none --user 12345:0 \
  --tmpfs /tmp:rw,nosuid,nodev,size=128m --memory 1g --pids-limit 128 --cpus 2 \
  --env OWA_JS_AMBIENT_SECRET=must-not-be-inherited \
  --env NODE_OPTIONS=--require=/missing-ambient-module.cjs \
  --entrypoint python "$image" - "$engine" <<'PY'
import asyncio
import importlib
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.sandbox import compile_sandbox_workflow
from open_workflow_agent.services import RuntimeServices

# The image contains the interpreter, without npm or a startup installation.
version = subprocess.run(
    ["/usr/local/bin/node", "--version"], env={}, capture_output=True,
    text=True, timeout=5, check=True,
).stdout.strip()
assert version.startswith("v24.")


async def main(directory):
    engine_name = sys.argv[1]
    module = importlib.import_module("open_workflow_agent_" + engine_name)
    engine = getattr(module, "AdkWorkflowEngine" if engine_name == "adk" else "LangGraphWorkflowEngine")()
    config = RuntimeConfig.model_validate({"model": {"provider": "fake"}, "sandbox": {
        "enabled": True, "script_runtimes": ["javascript"],
        "workspace_root": str(directory / "workspace"),
        "memory_bytes": None, "process_count": 32, "timeout_seconds": 15,
    }})
    assert config.sandbox.process_count == 32
    services = RuntimeServices(config, model=FakeModel(), database_root=directory / "runtime")
    await engine.initialize(services)
    try:
        assert engine.native.available if engine_name == "adk" else engine.native.task_nodes_available
        await services.recovery_definitions.prepare_profile(engine_name)
        public = {"document": {"dsl": "1.0.3", "namespace": "ci", "name": "javascript-image", "version": "1.0.0"}, "do": [{"execute": {"run": {
            "return": "all", "script": {"language": "javascript", "stdin": "snow: 雪", "arguments": ["--throw=arg"], "code": """
const fs = require('node:fs');
process.stdout.write(JSON.stringify({
  input: fs.readFileSync(0, 'utf8'), args: process.argv.slice(2),
  cwd: process.cwd(), home: process.env.HOME, temp: process.env.TEMP,
  secret: process.env.OWA_JS_AMBIENT_SECRET ?? null,
  options: process.env.NODE_OPTIONS ?? null
}));
process.stderr.write('diagnostic');
"""}}}}]}
        plan = compile_sandbox_workflow(public, sandbox=config.sandbox)
        handle = services.invocations.create(
            engine=engine_name, session_id=None, user_id=None,
            workflow_name=plan.name, workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        result = await engine.invoke(plan, handle, {"original": None})
        assert result.status == "completed", result.error
        assert result.output["code"] == 0 and result.output["stderr"] == "diagnostic"
        value = json.loads(result.output["stdout"])
        assert value["input"] == "snow: 雪" and value["args"] == ["--throw=arg"]
        assert value["secret"] is value["options"] is None
        assert value["cwd"] == value["home"] == value["temp"]
        assert not Path(value["cwd"]).exists()
        assert not list((directory / "workspace").iterdir())
        assert services.model.calls == []
        assert services.sandbox.capabilities()["script"]["runtimes"] == ["javascript"]
        assert services.sandbox.capabilities()["resourceLimits"]["processCount"] is True
    finally:
        await engine.shutdown()
        services.close()


with TemporaryDirectory(prefix="owa-javascript-image-") as folder:
    asyncio.run(main(Path(folder)))
print("Packaged JavaScript sandbox acceptance passed")
PY
