from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).parents[2]


def _documents(path: str) -> list[dict[str, Any]]:
    return [
        document
        for document in yaml.safe_load_all((ROOT / path).read_text(encoding="utf-8"))
        if isinstance(document, dict)
    ]


@pytest.mark.parametrize(
    "path",
    [
        "deploy/kubernetes/sandbox-boundary.yaml",
        "deploy/openshift/sandbox-boundary.yaml",
    ],
)
def test_sandbox_rbac_is_namespace_scoped_and_has_no_secret_or_scc_access(path: str) -> None:
    documents = _documents(path)
    roles = [document for document in documents if document.get("kind") == "Role"]
    assert len(roles) == 1
    role = roles[0]
    assert role["metadata"]["namespace"] == "owa-sandbox"
    rules = role["rules"]
    resources = {resource for rule in rules for resource in rule["resources"]}
    assert resources == {"jobs", "pods", "pods/log"}
    assert "secrets" not in resources
    assert "securitycontextconstraints" not in resources
    assert all("*" not in rule["verbs"] for rule in rules)
    assert all("*" not in rule["resources"] for rule in rules)

    assert not any(document.get("kind") == "ClusterRole" for document in documents)
    assert not any(document.get("kind") == "ClusterRoleBinding" for document in documents)


@pytest.mark.parametrize(
    "path",
    [
        "deploy/kubernetes/sandbox-boundary.yaml",
        "deploy/openshift/sandbox-boundary.yaml",
    ],
)
def test_sandbox_workload_network_is_default_deny(path: str) -> None:
    documents = _documents(path)
    policies = [document for document in documents if document.get("kind") == "NetworkPolicy"]
    assert len(policies) == 1
    policy = policies[0]["spec"]
    assert policy["podSelector"]["matchLabels"] == {
        "app.kubernetes.io/name": "open-workflow-agent-sandbox"
    }
    assert set(policy["policyTypes"]) == {"Ingress", "Egress"}
    assert "ingress" not in policy
    assert "egress" not in policy


@pytest.mark.parametrize(
    "path",
    [
        "deploy/kubernetes/sandbox-boundary.yaml",
        "deploy/openshift/sandbox-boundary.yaml",
    ],
)
def test_controller_and_workload_service_accounts_do_not_auto_mount_tokens(path: str) -> None:
    accounts = [
        document for document in _documents(path) if document.get("kind") == "ServiceAccount"
    ]
    assert {account["metadata"]["name"] for account in accounts} == {
        "owa-sandbox-controller",
        "owa-sandbox-workload",
    }
    assert all(account["automountServiceAccountToken"] is False for account in accounts)
