#!/usr/bin/env bash
set -euo pipefail

engine="${1:?engine is required}"
image="${2:?image is required}"
port="${3:?port is required}"
workdir="$(mktemp -d)"
name="owa-${engine}-sandbox"
log_file="${RUNNER_TEMP:-$workdir}/owa-${engine}-sandbox-image.log"
secret="sandbox-image-secret-${engine}"

cleanup() {
  docker logs "$name" > "$log_file" 2>&1 || true
  docker rm --force "$name" >/dev/null 2>&1 || true
  rm -rf "$workdir"
}
trap cleanup EXIT

mkdir -p "$workdir/config" "$workdir/knowledge" "$workdir/data"
chmod 0777 "$workdir/data"
cat > "$workdir/config/agent.yaml" <<'YAML'
model:
  provider: fake
  name: fake/default
workflow:
  path: /config/sandbox.yaml
knowledge:
  path: /knowledge
  database: /data/knowledge.sqlite3
  reload:
    mode: startup
sandbox:
  enabled: true
  backend: internal
  allow_shell: true
  script_runtimes:
    - python
  timeout_seconds: 2
  max_input_bytes: 65536
  max_output_bytes: 65536
  max_workspace_bytes: 1048576
  workspace_root: /tmp/owa-sandbox
YAML
cat > "$workdir/config/sandbox.yaml" <<'YAML'
document:
  dsl: '1.0.3'
  namespace: ci
  name: sandbox-image
  version: '1.0.0'
do:
  - script:
      run:
        script:
          language: python
          stdin: ${ .delay }
          code: |
            import sys
            import time
            from pathlib import Path
            Path("created.txt").write_text("ok")
            delay = float(sys.stdin.read() or "0")
            if delay:
                time.sleep(delay)
            print("script-ok")
  - shell:
      run:
        shell:
          command: printf
          arguments:
            - shell-ok
YAML

docker run --detach --name "$name" --publish "$port":8080 \
  --read-only --tmpfs /tmp:rw,nosuid,nodev,size=128m --user 12345:0 \
  --env SANDBOX_CI_SECRET="$secret" \
  --volume "$workdir/config:/config:ro" \
  --volume "$workdir/knowledge:/knowledge:ro" \
  --volume "$workdir/data:/data:rw" \
  "$image"

for attempt in $(seq 1 60); do
  if curl --fail --silent "http://127.0.0.1:${port}/health/ready" >/dev/null; then
    break
  fi
  sleep 1
done
curl --fail --silent "http://127.0.0.1:${port}/health/ready" >/dev/null

capabilities="$(curl --fail --silent "http://127.0.0.1:${port}/v1/capabilities")"
python - "$capabilities" <<'PY'
import json
import sys

sandbox = json.loads(sys.argv[1])["features"]["sandbox"]
assert sandbox["enabled"] is True
assert sandbox["backend"] == "internal"
assert sandbox["script"] == {
    "enabled": True,
    "runtimes": ["python"],
    "externalSource": False,
}
assert sandbox["shell"]["enabled"] is True
assert sandbox["container"]["enabled"] is False
assert sandbox["cancellation"] is True
assert sandbox["filesystemIsolation"] == "workspace_cwd_only"
assert sandbox["networkIsolation"] == "none"
assert sandbox["hardIsolation"] is False
PY

invocation="$(curl --fail --silent --request POST --header 'content-type: application/json' \
  --data '{"input":{"delay":"0"}}' "http://127.0.0.1:${port}/v1/invoke")"
python - "$invocation" <<'PY'
import json
import sys

result = json.loads(sys.argv[1])
assert result["status"] == "completed"
assert result["output"] == {"exitCode": 0, "stdout": "shell-ok", "stderr": ""}
PY

docker exec "$name" sh -eu -c 'test -d /tmp/owa-sandbox; test -z "$(find /tmp/owa-sandbox -mindepth 1 -maxdepth 1 -print -quit)"'

curl --fail --silent --request POST --header 'content-type: application/json' \
  --data '{"input":{"delay":"30"}}' "http://127.0.0.1:${port}/v1/invoke" \
  > "$workdir/cancelled-invoke.json" &
invoke_pid=$!
cancel_id=""
for attempt in $(seq 1 100); do
  cancel_id="$(curl --fail --silent "http://127.0.0.1:${port}/v1/events/lifecycle?limit=50" | python -c '
import json, sys
values=[event["data"] for event in json.load(sys.stdin)]
started=[value for value in values if value.get("event_type")=="SandboxExecutionStarted"]
print(started[-1].get("invocation_id", "") if len(started) >= 3 else "")
')"
  if [[ -n "$cancel_id" ]]; then
    break
  fi
  sleep 0.05
done
test -n "$cancel_id"
cancelled="$(curl --fail --silent --request POST \
  "http://127.0.0.1:${port}/v1/invocations/${cancel_id}/cancel")"
python - "$cancelled" <<'PY'
import json
import sys

assert json.loads(sys.argv[1])["status"] == "cancelled"
PY
wait "$invoke_pid" || true
python - "$workdir/cancelled-invoke.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if path.exists() and path.stat().st_size:
    assert json.loads(path.read_text())["status"] == "cancelled"
PY

docker exec "$name" sh -eu -c 'test -z "$(find /tmp/owa-sandbox -mindepth 1 -maxdepth 1 -print -quit)"'

timeout_status="$(curl --silent --output "$workdir/timed-out.json" --write-out '%{http_code}' \
  --request POST --header 'content-type: application/json' \
  --data '{"input":{"delay":"10"}}' "http://127.0.0.1:${port}/v1/invoke")"
test "$timeout_status" = "500"
python - "$workdir/timed-out.json" <<'PY'
import json
import sys
from pathlib import Path

result = json.loads(Path(sys.argv[1]).read_text())
assert result["error"]["code"] == "sandbox_timeout"
PY

docker exec "$name" sh -eu -c 'test -z "$(find /tmp/owa-sandbox -mindepth 1 -maxdepth 1 -print -quit)"'

lifecycle="$(curl --fail --silent "http://127.0.0.1:${port}/v1/events/lifecycle?limit=50")"
python - "$lifecycle" <<'PY'
import json
import sys

values = [event["data"] for event in json.loads(sys.argv[1])]
sandbox_events = [value for value in values if value.get("event_type", "").startswith("SandboxExecution")]
assert sum(value["event_type"] == "SandboxExecutionStarted" for value in sandbox_events) == 4
assert sum(value["event_type"] == "SandboxExecutionCompleted" for value in sandbox_events) == 2
assert sum(value["event_type"] == "SandboxExecutionCancelled" for value in sandbox_events) == 1
assert sum(value["event_type"] == "SandboxExecutionFailed" for value in sandbox_events) == 1
assert all(value.get("execution_id") for value in sandbox_events)
assert all(value.get("task_reference") for value in sandbox_events)
failed = [value for value in sandbox_events if value["event_type"] == "SandboxExecutionFailed"]
assert failed[0]["error"] == {"code": "sandbox_timeout"}
PY

docker stop --time 5 "$name" >/dev/null
docker logs "$name" > "$log_file" 2>&1 || true
if grep -Fq "$secret" "$log_file"; then
  echo "sandbox image acceptance leaked an ambient credential" >&2
  exit 1
fi
docker rm "$name" >/dev/null
trap - EXIT
rm -rf "$workdir"
