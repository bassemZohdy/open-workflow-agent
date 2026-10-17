#!/usr/bin/env bash
set -euo pipefail

engine="${1:?engine is required}"
image="${2:?image is required}"
port="${3:?port is required}"
workdir="$(mktemp -d)"
name="owa-${engine}-embedded-defaults"
restarted="${name}-restarted"
log_root="${RUNNER_TEMP:-$workdir}"
mkdir -p "$workdir/data"
chmod 0777 "$workdir/data"
cleanup() {
  if docker container inspect "$name" >/dev/null 2>&1; then
    docker logs "$name" > "$log_root/owa-${engine}-embedded.log" 2>&1 || true
  fi
  docker logs "$restarted" > "$log_root/owa-${engine}-embedded-restarted.log" 2>&1 || true
  docker rm --force "$name" "$restarted" >/dev/null 2>&1 || true
  rm -rf "$workdir"
}
trap cleanup EXIT

start_runtime() {
  docker run --detach --name "$1" --publish "$port":8080 \
    --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --user 12345:0 \
    --volume "$workdir/data:/data:rw" "$image"
  for attempt in $(seq 1 60); do
    if curl --fail --silent "http://127.0.0.1:$port/health/ready" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  curl --fail --silent "http://127.0.0.1:$port/health/ready" >/dev/null
}

# No config/workflow/knowledge mounts, model credentials, database/broker
# service, or bootstrap provider variables. Only embedded data is persisted.
start_runtime "$name"
result="$(curl --fail --silent --request POST --header 'content-type: application/json' \
  --data '{"input":{"question":"embedded-default"}}' \
  "http://127.0.0.1:$port/v1/invoke")"
invocation_id="$(python -c 'import json,sys; d=json.loads(sys.argv[1]); assert d["status"] == "completed"; assert d["output"]["response"]["question"] == "embedded-default"; print(d["invocation_id"])' "$result")"
docker logs "$name" > "$log_root/owa-${engine}-embedded.log" 2>&1 || true
docker stop --time 5 "$name" >/dev/null
docker rm "$name" >/dev/null
start_runtime "$restarted"
stored="$(curl --fail --silent --request POST --header 'content-type: application/json' \
  --data '{"input":null}' "http://127.0.0.1:$port/v1/invocations/$invocation_id/resume")"
python -c 'import json,sys; d=json.loads(sys.argv[1]); assert d["status"] == "completed"; assert d["invocation_id"] == sys.argv[2]; assert d["output"]["response"]["question"] == "embedded-default"' "$stored" "$invocation_id"
curl --fail --silent --request POST --header 'content-type: application/json' \
  --data '{"input":{"question":"after-restart"}}' "http://127.0.0.1:$port/v1/invoke" \
  | python -c 'import json,sys; d=json.load(sys.stdin); assert d["status"] == "completed"; assert d["output"]["response"]["question"] == "after-restart"'
