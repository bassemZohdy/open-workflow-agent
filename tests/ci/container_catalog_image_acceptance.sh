#!/usr/bin/env bash
set -euo pipefail

engine="${1:?engine is required}"
image="${2:?image is required}"
workdir="$(mktemp -d)"
log_file="${RUNNER_TEMP:-$workdir}/owa-${engine}-catalog-image.log"
secret="catalog-image-secret-${engine}"

cleanup() {
  rm -rf "$workdir"
}
trap cleanup EXIT

cat > "$workdir/catalog_acceptance.py" <<'PY'
import asyncio
import os

import httpx

from open_workflow_agent.catalog import FakeModel, FunctionCatalog
from open_workflow_agent.config import CatalogAuthenticationConfig, ExternalCatalogConfig
from open_workflow_agent.external_catalog import ExternalCatalogResolver
from open_workflow_agent.protocols import HttpClient
from open_workflow_agent.security import ProfileAuthentication, SecurityConfig
from open_workflow_agent.workflow import compile_workflow

FUNCTION_YAML = """\
call: http
with:
  method: post
  endpoint: https://catalog.test/echo
  body:
    value: ${ .value }
"""

WORKFLOW = {
    "document": {
        "dsl": "1.0.3",
        "namespace": "ci",
        "name": "external-catalog-image",
        "version": "1.0.0",
    },
    "use": {
        "catalogs": {
            "trusted": {"endpoint": {"uri": "https://catalog.test/root"}},
        }
    },
    "do": [{"remote": {"call": "echo:1.0.0@trusted"}}],
}


async def main() -> None:
    token = os.environ["CATALOG_TEST_TOKEN"]

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "catalog.test"
        assert request.headers.get("authorization") == f"Bearer {token}"
        assert request.url.path == "/root/functions/echo/1.0.0/function.yaml"
        return httpx.Response(200, text=FUNCTION_YAML, headers={"ETag": '"ci-image"'})

    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "catalog-reader": {
                    "type": "bearer",
                    "token": {"from_env": "CATALOG_TEST_TOKEN"},
                }
            }
        }
    )
    policy = ExternalCatalogConfig(
        allowed_hosts=["catalog.test"],
        authentication=CatalogAuthenticationConfig(security_profile="catalog-reader"),
    )
    client = HttpClient(
        transport=httpx.MockTransport(handler),
        max_response_bytes=policy.max_response_bytes,
        allowed_hosts={"catalog.test"},
        authentication=ProfileAuthentication(security, "catalog-reader"),
    )
    resolver = ExternalCatalogResolver({"trusted": policy}, http=client)
    plan = compile_workflow(WORKFLOW, trusted_catalogs={"trusted": policy})
    catalog = FunctionCatalog.default(FakeModel())
    await resolver.resolve_workflow(plan.source, catalog)
    assert catalog.has("echo:1.0.0@trusted")
    capabilities = resolver.capabilities()
    assert capabilities["resolvedFunctions"] == ["echo:1.0.0@trusted"]
    assert capabilities["policy"]["httpsOnly"] is True
    assert capabilities["policy"]["tlsVerification"] is True
    print("catalog-image-ok")


asyncio.run(main())
PY
chmod a+r "$workdir/catalog_acceptance.py"

docker run --rm \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m \
  --user 12345:0 \
  --env CATALOG_TEST_TOKEN="$secret" \
  --volume "$workdir/catalog_acceptance.py:/config/catalog_acceptance.py:ro" \
  "$image" python /config/catalog_acceptance.py > "$log_file" 2>&1

grep -q '^catalog-image-ok$' "$log_file"
if grep -Fq "$secret" "$log_file"; then
  echo "external catalog image acceptance leaked its credential" >&2
  exit 1
fi
