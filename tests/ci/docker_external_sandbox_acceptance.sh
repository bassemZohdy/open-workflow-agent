#!/usr/bin/env bash
set -euo pipefail

engine="${1:?engine is required}"
runtime_image="${2:?runtime image is required}"
port="${3:?runtime port is required}"
controller_image="owa-sandbox-controller:${GITHUB_SHA:-local}"
worker_image="$(docker image inspect --format '{{.Id}}' "$runtime_image")"
docker_gid="$(stat -c '%g' /var/run/docker.sock)"
workdir="$(mktemp -d)"
socket_dir="$workdir/socket"
config_dir="$workdir/config"
knowledge_dir="$workdir/knowledge"
data_dir="$workdir/data"
controller_name="owa-sandbox-controller-${engine}"
runtime_name="owa-${engine}-external-sandbox"
secret="docker-sandbox-secret-${engine}-must-not-leak"

mkdir -p "$socket_dir" "$config_dir" "$knowledge_dir" "$data_dir"
chmod 0777 "$socket_dir" "$data_dir"
printf '%s\n' 'External sandbox acceptance document.' > "$knowledge_dir/policy.md"

cleanup() {
  set +e
  docker logs "$runtime_name" > "$workdir/runtime.log" 2>&1
  docker logs "$controller_name" > "$workdir/controller.log" 2>&1
  docker rm --force "$runtime_name" "$controller_name" >/dev/null 2>&1
  if grep -Fq "$secret" "$workdir/runtime.log" "$workdir/controller.log"; then
    echo "sandbox secret leaked into runtime/controller logs" >&2
    exit 1
  fi
  rm -rf "$workdir"
}
trap cleanup EXIT

docker build \
  --file docker/Dockerfile.sandbox-controller \
  --tag "$controller_image" \
  .

cat > "$config_dir/agent.yaml" <<YAML
model:
  provider: fake
  name: fake/default
workflow:
  path: /config/container.yaml
knowledge:
  path: /knowledge
  database: /data/knowledge.sqlite3
  reload:
    mode: startup
sandbox:
  enabled: true
  backend: docker
  timeout_seconds: 5
  max_input_bytes: 1048576
  max_output_bytes: 1048576
  max_workspace_bytes: 16777216
  memory_bytes: 268435456
  process_count: 32
  secret_environment:
    - SANDBOX_TEST_SECRET
  docker:
    controller_socket: /run/owa-sandbox/controller.sock
    allowed_images:
      - "$worker_image"
    require_digest: false
    run_as_user: "65532:65532"
    network: denied
YAML

cat > "$config_dir/container.yaml" <<YAML
document:
  dsl: '1.0.3'
  namespace: ci
  name: docker-external-sandbox
  version: '1.0.0'
do:
  - execute:
      run:
        container:
          image: "$worker_image"
          command: /opt/venv/bin/python
          arguments:
            - -c
            - |
              import json
              import os
              import socket
              from pathlib import Path
              workspace_ok = False
              root_read_only = False
              try:
                  Path('/workspace/probe.txt').write_text('ok')
                  workspace_ok = True
              except OSError:
                  pass
              try:
                  Path('/forbidden-root-write').write_text('no')
              except OSError:
                  root_read_only = True
              sock = socket.socket()
              sock.settimeout(0.2)
              network_denied = sock.connect_ex(('1.1.1.1', 53)) != 0
              sock.close()
              print(json.dumps({
                  'workspace_ok': workspace_ok,
                  'root_read_only': root_read_only,
                  'network_denied': network_denied,
                  'secret_resolved': os.getenv('TOKEN') == '$secret',
              }, sort_keys=True))
          environment:
            TOKEN:
              fromEnv: SANDBOX_TEST_SECRET
YAML

docker run --detach \
  --name "$controller_name" \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m \
  --user 65532:0 \
  --group-add "$docker_gid" \
  --volume /var/run/docker.sock:/var/run/docker.sock \
  --volume "$socket_dir:/run/owa-sandbox:rw" \
  --env "OWA_SANDBOX_CONTROLLER_ALLOWED_IMAGES=$worker_image" \
  --env OWA_SANDBOX_CONTROLLER_MAX_TIMEOUT_SECONDS=10 \
  --env OWA_SANDBOX_CONTROLLER_MAX_OUTPUT_BYTES=1048576 \
  --env OWA_SANDBOX_CONTROLLER_MAX_WORKSPACE_BYTES=16777216 \
  --env OWA_SANDBOX_CONTROLLER_MAX_MEMORY_BYTES=268435456 \
  --env OWA_SANDBOX_CONTROLLER_MAX_PROCESS_COUNT=32 \
  "$controller_image"

for _ in $(seq 1 60); do
  if test -S "$socket_dir/controller.sock" \
    && curl --fail --silent --unix-socket "$socket_dir/controller.sock" \
      http://localhost/health/ready >/dev/null; then
    break
  fi
  sleep 1
done
curl --fail --silent --unix-socket "$socket_dir/controller.sock" \
  http://localhost/health/ready >/dev/null

docker exec "$controller_name" sh -c 'test "$(id -u)" != 0 && test -S /var/run/docker.sock'
controller_capabilities="$(curl --fail --silent --unix-socket "$socket_dir/controller.sock" \
  http://localhost/v1/capabilities)"
python - "$controller_capabilities" <<'PY'
import json
import sys

value = json.loads(sys.argv[1])
assert value["backend"] == "docker"
assert value["imagePolicy"] == "exact_immutable_allowlist"
assert value["pullPolicy"] == "never"
assert value["network"] == "denied"
assert value["readOnlyRoot"] is True
assert value["hostMounts"] is False
assert value["hostNetwork"] is False
assert value["privileged"] is False
assert value["dropAllCapabilities"] is True
assert value["noNewPrivileges"] is True
assert value["defaultSeccomp"] is True
PY

docker run --detach \
  --name "$runtime_name" \
  --publish "$port:8080" \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=256m \
  --user 12345:0 \
  --volume "$config_dir:/config:ro" \
  --volume "$knowledge_dir:/knowledge:ro" \
  --volume "$data_dir:/data:rw" \
  --volume "$socket_dir:/run/owa-sandbox:ro" \
  --env "SANDBOX_TEST_SECRET=$secret" \
  "$runtime_image"

for _ in $(seq 1 60); do
  if curl --fail --silent "http://127.0.0.1:${port}/health/ready" >/dev/null; then
    break
  fi
  sleep 1
done
curl --fail --silent "http://127.0.0.1:${port}/health/ready" >/dev/null
docker exec "$runtime_name" sh -c 'test ! -S /var/run/docker.sock'

capabilities="$(curl --fail --silent "http://127.0.0.1:${port}/v1/capabilities")"
python - "$capabilities" <<'PY'
import json
import sys

value = json.loads(sys.argv[1])["sandbox"]
assert value["backend"] == "docker"
assert value["container"]["enabled"] is True
assert value["container"]["ports"] is False
assert value["container"]["volumes"] is False
assert value["filesystemIsolation"] == "isolated_root"
assert value["networkIsolation"] == "denied"
assert value["hardIsolation"] is True
assert value["controllerTransport"] == "unix_socket"
PY

invocation="$(curl --fail --silent \
  --request POST \
  --header 'content-type: application/json' \
  --data '{"input":{}}' \
  "http://127.0.0.1:${port}/v1/invoke")"
python - "$invocation" <<'PY'
import json
import sys

value = json.loads(sys.argv[1])
assert value["status"] == "completed"
output = value["output"]
assert output["exitCode"] == 0
assert output["stderr"] == ""
probe = json.loads(output["stdout"])
assert probe == {
    "network_denied": True,
    "root_read_only": True,
    "secret_resolved": True,
    "workspace_ok": True,
}
PY

make_payload() {
  local execution_id="$1"
  local code="$2"
  local output_limit="$3"
  local timeout_seconds="$4"
  python - "$worker_image" "$execution_id" "$code" "$output_limit" "$timeout_seconds" <<'PY'
import json
import sys

image, execution_id, code, output_limit, timeout_seconds = sys.argv[1:]
print(json.dumps({
    "execution_id": execution_id,
    "image": image,
    "command": "/opt/venv/bin/python",
    "arguments": ["-c", code],
    "stdin": None,
    "environment": {},
    "limits": {
        "timeout_seconds": float(timeout_seconds),
        "max_output_bytes": int(output_limit),
        "max_workspace_bytes": 1048576,
        "memory_bytes": 134217728,
        "process_count": 16,
    },
    "isolation": {
        "run_as_user": "65532:65532",
        "network": "denied",
        "read_only_root": True,
        "drop_all_capabilities": True,
        "no_new_privileges": True,
        "host_mounts": False,
        "host_network": False,
    },
}, separators=(",", ":")))
PY
}

timeout_payload="$(make_payload "timeout-${engine}" 'import time; time.sleep(5)' 1024 0.5)"
timeout_response="$workdir/timeout.json"
timeout_status="$(curl --silent --output "$timeout_response" --write-out '%{http_code}' \
  --unix-socket "$socket_dir/controller.sock" \
  --header 'content-type: application/json' \
  --data "$timeout_payload" \
  http://localhost/v1/executions)"
test "$timeout_status" = "500"
python - "$timeout_response" <<'PY'
import json
import sys
from pathlib import Path

value = json.loads(Path(sys.argv[1]).read_text())
assert value["error"]["code"] == "sandbox_timeout"
PY

output_payload="$(make_payload "output-${engine}" "print('x' * 4096)" 128 5)"
output_response="$workdir/output.json"
output_status="$(curl --silent --output "$output_response" --write-out '%{http_code}' \
  --unix-socket "$socket_dir/controller.sock" \
  --header 'content-type: application/json' \
  --data "$output_payload" \
  http://localhost/v1/executions)"
test "$output_status" = "500"
python - "$output_response" <<'PY'
import json
import sys
from pathlib import Path

value = json.loads(Path(sys.argv[1]).read_text())
assert value["error"]["code"] == "sandbox_output_limit"
PY

cancel_id="cancel-${engine}"
cancel_payload="$(make_payload "$cancel_id" 'import time; time.sleep(30)' 1024 10)"
cancel_response="$workdir/cancel-post.json"
curl --silent --output "$cancel_response" \
  --unix-socket "$socket_dir/controller.sock" \
  --header 'content-type: application/json' \
  --data "$cancel_payload" \
  http://localhost/v1/executions &
cancel_curl_pid=$!
container_name="owa-sbx-$(printf '%s' "$cancel_id" | sha256sum | cut -c1-24)"
for _ in $(seq 1 100); do
  if docker ps --format '{{.Names}}' | grep -Fxq "$container_name"; then
    break
  fi
  sleep 0.05
done
docker ps --format '{{.Names}}' | grep -Fxq "$container_name"
cancel_result="$(curl --fail --silent --request DELETE \
  --unix-socket "$socket_dir/controller.sock" \
  "http://localhost/v1/executions/${cancel_id}")"
python - "$cancel_result" <<'PY'
import json
import sys
assert json.loads(sys.argv[1])["status"] == "cancelled"
PY
wait "$cancel_curl_pid" || true
for _ in $(seq 1 100); do
  if ! docker ps --all --format '{{.Names}}' | grep -Fxq "$container_name"; then
    break
  fi
  sleep 0.05
done
! docker ps --all --format '{{.Names}}' | grep -Fxq "$container_name"

if docker ps --all --format '{{.Names}}' | grep -q '^owa-sbx-'; then
  echo "orphan sandbox container remained after acceptance" >&2
  docker ps --all --filter name=owa-sbx- >&2
  exit 1
fi

echo "Docker external sandbox acceptance passed for ${engine}."
