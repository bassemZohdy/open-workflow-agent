#!/usr/bin/env bash
set -euo pipefail

# Run against a disposable OpenShift project. Both images must be immutable
# digests so this check exercises the same supply-chain contract as production.
# Example:
#   OWA_K8S_CONTROLLER_IMAGE=ghcr.io/org/controller@sha256:... \
#   OWA_SANDBOX_IMAGE=ghcr.io/org/controller@sha256:... \
#   tests/ci/openshift_sandbox_acceptance.sh

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "required command not found: $1" >&2
    exit 2
  }
}

require_command oc
require_command curl
require_command python3

controller_image="${OWA_K8S_CONTROLLER_IMAGE:?set OWA_K8S_CONTROLLER_IMAGE to an immutable controller image digest}"
sandbox_image="${OWA_SANDBOX_IMAGE:?set OWA_SANDBOX_IMAGE to an immutable sandbox image digest}"

validate_image() {
  local image="$1"
  local name="${image%@sha256:*}"
  local digest="${image##*@sha256:}"
  [[ -n "$name" && "$image" != *[[:space:]\"]* ]] || return 1
  [[ "$image" == *@sha256:* && "${#digest}" -eq 64 ]] || return 1
  [[ "$digest" =~ ^[0-9a-fA-F]+$ ]]
}

validate_image "$controller_image" || {
  echo "OWA_K8S_CONTROLLER_IMAGE must use an immutable sha256 digest" >&2
  exit 2
}
validate_image "$sandbox_image" || {
  echo "OWA_SANDBOX_IMAGE must use an immutable sha256 digest" >&2
  exit 2
}

project="${OWA_OPENSHIFT_PROJECT:-owa-openshift-acceptance-${RANDOM}}"
if [[ ! "$project" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ || "${#project}" -gt 63 ]]; then
  echo "OWA_OPENSHIFT_PROJECT must be a DNS label of at most 63 characters" >&2
  exit 2
fi

suffix="${RANDOM}"
controller_name="owa-sandbox-controller-${suffix}"
controller_sa="owa-sandbox-controller-${suffix}"
workload_sa="owa-sandbox-workload-${suffix}"
role_name="owa-sandbox-controller-${suffix}"
network_policy_name="owa-sandbox-default-deny-${suffix}"
created_project=0
port_forward_pid=""
port_forward_log=""
request_pid=""
response_file=""

cleanup() {
  if [[ -n "$request_pid" ]]; then
    kill "$request_pid" >/dev/null 2>&1 || true
    wait "$request_pid" >/dev/null 2>&1 || true
  fi
  if [[ -n "$port_forward_pid" ]]; then
    kill "$port_forward_pid" >/dev/null 2>&1 || true
    wait "$port_forward_pid" >/dev/null 2>&1 || true
  fi
  if [[ -n "$response_file" ]]; then
    rm -f -- "$response_file"
  fi
  if [[ -n "$port_forward_log" ]]; then
    rm -f -- "$port_forward_log"
  fi
  if [[ "$created_project" -eq 1 ]]; then
    oc delete project "$project" --wait=true >/dev/null 2>&1 || true
  else
    oc delete deployment "$controller_name" -n "$project" --ignore-not-found >/dev/null 2>&1 || true
    oc delete networkpolicy "$network_policy_name" -n "$project" --ignore-not-found >/dev/null 2>&1 || true
    oc delete role "$role_name" -n "$project" --ignore-not-found >/dev/null 2>&1 || true
    oc delete serviceaccount "$controller_sa" "$workload_sa" -n "$project" --ignore-not-found >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if oc get project "$project" >/dev/null 2>&1; then
  if [[ "${OWA_OPENSHIFT_ALLOW_EXISTING:-0}" != "1" ]]; then
    echo "refusing to use an existing project; set OWA_OPENSHIFT_ALLOW_EXISTING=1 only for a disposable project" >&2
    exit 2
  fi
else
  oc new-project "$project" --display-name="OWA OpenShift sandbox acceptance" >/dev/null
  created_project=1
fi

oc apply -n "$project" -f - >/dev/null <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: $controller_sa
automountServiceAccountToken: true
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: $workload_sa
automountServiceAccountToken: false
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: $role_name
rules:
  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["create", "get", "list", "delete"]
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list"]
  - apiGroups: [""]
    resources: ["pods/log"]
    verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: $role_name
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: $role_name
subjects:
  - kind: ServiceAccount
    name: $controller_sa
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: $network_policy_name
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: open-workflow-agent-sandbox
  policyTypes:
    - Ingress
    - Egress
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: $controller_name
  labels:
    app.kubernetes.io/name: owa-sandbox-controller
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: owa-sandbox-controller
  template:
    metadata:
      labels:
        app.kubernetes.io/name: owa-sandbox-controller
    spec:
      serviceAccountName: $controller_sa
      automountServiceAccountToken: true
      securityContext:
        runAsNonRoot: true
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: controller
          image: $controller_image
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 8090
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            runAsNonRoot: true
            capabilities:
              drop:
                - ALL
          env:
            - name: OWA_K8S_CONTROLLER_PLATFORM
              value: openshift
            - name: OWA_K8S_CONTROLLER_ALLOWED_IMAGES
              value: $sandbox_image
            - name: OWA_K8S_CONTROLLER_NAMESPACE
              value: $project
            - name: OWA_K8S_CONTROLLER_WORKLOAD_SERVICE_ACCOUNT
              value: $workload_sa
            - name: OWA_K8S_CONTROLLER_TOKEN_FILE
              value: /var/run/secrets/kubernetes.io/serviceaccount/token
            - name: OWA_K8S_CONTROLLER_NETWORK_POLICY_ENFORCED
              value: "true"
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
EOF

controller_identity="system:serviceaccount:${project}:${controller_sa}"
assert_rbac() {
  local verb="$1"
  local resource="$2"
  local expected="$3"
  local actual
  local actual_status=0
  actual="$(oc auth can-i --as="$controller_identity" "$verb" "$resource" -n "$project")" || actual_status=$?
  if [[ "$actual_status" -ne 0 && "$actual" != "no" ]]; then
    echo "controller ServiceAccount can-i $verb $resource failed with status $actual_status" >&2
    exit 1
  fi
  [[ "$actual" == "$expected" ]] || {
    echo "controller ServiceAccount can-i $verb $resource returned $actual, expected $expected" >&2
    exit 1
  }
}

assert_rbac create jobs yes
assert_rbac get jobs yes
assert_rbac delete jobs yes
assert_rbac get pods yes
assert_rbac get pods/log yes
assert_rbac get secrets no
assert_rbac create deployments no

oc rollout status deployment/"$controller_name" -n "$project" --timeout=180s
controller_pod="$(oc get pods -n "$project" -l app.kubernetes.io/name=owa-sandbox-controller -o jsonpath='{.items[0].metadata.name}')"
[[ -n "$controller_pod" ]] || { echo "controller pod was not created" >&2; exit 1; }

pod_json="$(oc get pod "$controller_pod" -n "$project" -o json)"
printf '%s' "$pod_json" | python3 -c '
import json
import sys

pod = json.load(sys.stdin)
scc = pod["metadata"].get("annotations", {}).get("openshift.io/scc")
if scc not in {"restricted-v2", "restricted"}:
    raise SystemExit(f"unexpected SCC: {scc!r}")
pod_security = pod["spec"].get("securityContext", {})
uid = pod_security.get("runAsUser")
container = next(item for item in pod["spec"]["containers"] if item["name"] == "controller")
container_security = container.get("securityContext", {})
uid = uid or container_security.get("runAsUser")
if not isinstance(uid, int) or uid <= 0:
    raise SystemExit(f"OpenShift did not inject a non-root UID: {uid!r}")
if container_security.get("allowPrivilegeEscalation") is not False:
    raise SystemExit("controller allows privilege escalation")
if container_security.get("readOnlyRootFilesystem") is not True:
    raise SystemExit("controller root filesystem is not read-only")
if "ALL" not in container_security.get("capabilities", {}).get("drop", []):
    raise SystemExit("controller does not drop all capabilities")
if pod_security.get("seccompProfile", {}).get("type") != "RuntimeDefault":
    raise SystemExit("controller does not use RuntimeDefault seccomp")
print(f"OpenShift SCC {scc} injected UID {uid}")
'

deployment_json="$(oc get deployment "$controller_name" -n "$project" -o json)"
printf '%s' "$deployment_json" | python3 -c '
import json
import sys

deployment = json.load(sys.stdin)
pod_spec = deployment["spec"]["template"]["spec"]
if "runAsUser" in pod_spec.get("securityContext", {}):
    raise SystemExit("controller template hard-codes a UID")
container = next(item for item in pod_spec["containers"] if item["name"] == "controller")
if "runAsUser" in container.get("securityContext", {}):
    raise SystemExit("controller container template hard-codes a UID")
print("controller template leaves UID allocation to OpenShift")
'

controller_runtime_uid="$(oc exec -n "$project" "$controller_pod" -- python -c 'import os; print(os.getuid())')"
[[ "$controller_runtime_uid" =~ ^[1-9][0-9]*$ ]] || {
  echo "controller process did not run with a non-root UID: $controller_runtime_uid" >&2
  exit 1
}

local_port="${OWA_OPENSHIFT_LOCAL_PORT:-18090}"
if [[ ! "$local_port" =~ ^[0-9]+$ ]] || (( local_port < 1024 || local_port > 65535 )); then
  echo "OWA_OPENSHIFT_LOCAL_PORT must be an integer from 1024 through 65535" >&2
  exit 2
fi
port_forward_log="$(mktemp)"
oc port-forward -n "$project" "deployment/$controller_name" "$local_port:8090" >"$port_forward_log" 2>&1 &
port_forward_pid="$!"
for _ in {1..60}; do
  if curl --silent --show-error --fail "http://127.0.0.1:$local_port/health/ready" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl --silent --show-error --fail "http://127.0.0.1:$local_port/health/ready" >/dev/null

execution_id="openshift-uid-${RANDOM}"
execution_label="$(python3 - "$execution_id" <<'PY'
import hashlib
import sys

print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:32])
PY
)"
request_json="$(python3 - "$sandbox_image" "$execution_id" <<'PY'
import json
import sys

image, execution_id = sys.argv[1:]
command = """import os, pathlib, socket, time
print(f"uid={os.getuid()}")
pathlib.Path("/workspace/owa-uid-check").write_text("ok")
print("workspace-writable")
try:
    pathlib.Path("/owa-root-check").write_text("must-fail")
except OSError:
    print("root-read-only")
else:
    raise SystemExit("root filesystem is writable")
sock = socket.socket()
sock.settimeout(2)
try:
    sock.connect(("kubernetes.default.svc", 443))
except OSError:
    print("network-denied")
else:
    raise SystemExit("network is reachable")
time.sleep(5)
"""
print(json.dumps({
    "execution_id": execution_id,
    "image": image,
    "command": "python",
    "arguments": ["-c", command],
    "environment": {},
    "limits": {
        "timeout_seconds": 30,
        "max_output_bytes": 4096,
        "max_workspace_bytes": 1048576,
        "memory_bytes": 134217728,
    },
    "isolation": {
        "network": "denied",
        "network_policy_enforced": True,
        "process_limit_enforced": False,
        "read_only_root": True,
        "run_as_non_root": True,
        "drop_all_capabilities": True,
        "allow_privilege_escalation": False,
        "seccomp_profile": "RuntimeDefault",
        "host_mounts": False,
        "host_network": False,
        "host_pid": False,
        "host_ipc": False,
        "automount_service_account_token": False,
    },
}))
PY
)"
response_file="$(mktemp)"
curl --silent --show-error --fail \
  --header 'content-type: application/json' \
  --data "$request_json" \
  "http://127.0.0.1:$local_port/v1/executions" >"$response_file" &
request_pid="$!"

job_name=""
for _ in {1..60}; do
  job_name="$(oc get jobs -n "$project" -l "openworkflow.agent/execution=$execution_label" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  [[ -n "$job_name" ]] && break
  sleep 1
done
[[ -n "$job_name" ]] || { echo "sandbox Job was not created" >&2; exit 1; }

sandbox_pod=""
for _ in {1..60}; do
  sandbox_pod="$(oc get pods -n "$project" -l "job-name=$job_name" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  [[ -n "$sandbox_pod" ]] && break
  sleep 1
done
[[ -n "$sandbox_pod" ]] || { echo "sandbox pod was not created" >&2; exit 1; }

job_json="$(oc get job "$job_name" -n "$project" -o json)"
JOB_JSON="$job_json" EXPECTED_SERVICE_ACCOUNT="$workload_sa" python3 - <<'PY'
import json
import os
import sys

job = json.loads(os.environ["JOB_JSON"])
expected_service_account = os.environ["EXPECTED_SERVICE_ACCOUNT"]
pod_spec = job["spec"]["template"]["spec"]
if pod_spec.get("serviceAccountName") != expected_service_account:
    raise SystemExit("sandbox Job uses the wrong ServiceAccount")
if pod_spec.get("automountServiceAccountToken") is not False:
    raise SystemExit("sandbox Job mounts a ServiceAccount token")
if any(key in pod_spec.get("securityContext", {}) for key in ("runAsUser", "runAsGroup")):
    raise SystemExit("sandbox Job template hard-codes an OpenShift UID")
container = next(item for item in pod_spec["containers"] if item["name"] == "sandbox")
container_security = container.get("securityContext", {})
if any(key in container_security for key in ("runAsUser", "runAsGroup")):
    raise SystemExit("sandbox container template hard-codes an OpenShift UID")
if container_security.get("allowPrivilegeEscalation") is not False:
    raise SystemExit("sandbox allows privilege escalation")
if container_security.get("privileged") is not False:
    raise SystemExit("sandbox is privileged")
if container_security.get("readOnlyRootFilesystem") is not True:
    raise SystemExit("sandbox root filesystem is not read-only")
if "ALL" not in container_security.get("capabilities", {}).get("drop", []):
    raise SystemExit("sandbox does not drop all capabilities")
if pod_spec.get("securityContext", {}).get("seccompProfile", {}).get("type") != "RuntimeDefault":
    raise SystemExit("sandbox does not use RuntimeDefault seccomp")
print("sandbox Job template leaves UID allocation to OpenShift")
PY

sandbox_pod_json="$(oc get pod "$sandbox_pod" -n "$project" -o json)"
printf '%s' "$sandbox_pod_json" | python3 -c '
import json
import sys

pod = json.load(sys.stdin)
scc = pod["metadata"].get("annotations", {}).get("openshift.io/scc")
if scc not in {"restricted-v2", "restricted"}:
    raise SystemExit(f"unexpected sandbox SCC: {scc!r}")
pod_security = pod["spec"].get("securityContext", {})
uid = pod_security.get("runAsUser")
container = next(item for item in pod["spec"]["containers"] if item["name"] == "sandbox")
container_security = container.get("securityContext", {})
uid = uid or container_security.get("runAsUser")
if not isinstance(uid, int) or uid <= 0:
    raise SystemExit(f"OpenShift did not inject a sandbox non-root UID: {uid!r}")
if pod.get("spec", {}).get("automountServiceAccountToken") is not False:
    raise SystemExit("sandbox pod mounts a ServiceAccount token")
if container_security.get("allowPrivilegeEscalation") is not False:
    raise SystemExit("sandbox pod allows privilege escalation")
if container_security.get("readOnlyRootFilesystem") is not True:
    raise SystemExit("sandbox pod root filesystem is not read-only")
if "ALL" not in container_security.get("capabilities", {}).get("drop", []):
    raise SystemExit("sandbox pod does not drop all capabilities")
print(f"OpenShift sandbox SCC {scc} injected UID {uid}")
'

if ! wait "$request_pid"; then
  cat "$response_file" >&2 || true
  exit 1
fi
request_pid=""
response="$(<"$response_file")"
rm -f -- "$response_file"
response_file=""
stdout="$(printf '%s' "$response" | python3 -c 'import json, sys; print(json.load(sys.stdin)["stdout"], end="")')"

grep -qx 'workspace-writable' <<<"$stdout" || {
  echo "sandbox workspace was not writable" >&2
  exit 1
}
grep -qx 'root-read-only' <<<"$stdout" || {
  echo "sandbox root filesystem was writable" >&2
  exit 1
}
grep -qx 'network-denied' <<<"$stdout" || {
  echo "sandbox network policy did not deny cluster egress" >&2
  exit 1
}
sandbox_uid="$(sed -n 's/^uid=//p' <<<"$stdout")"
[[ "$sandbox_uid" =~ ^[1-9][0-9]*$ ]] || {
  echo "sandbox process did not run with a non-root UID: $sandbox_uid" >&2
  exit 1
}

echo "OpenShift sandbox acceptance passed (SCC, arbitrary UID, restricted security context, RBAC boundary, network denial, and cleanup)."
