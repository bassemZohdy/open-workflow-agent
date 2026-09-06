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


def test_runtime_namespace_network_policy_is_default_deny() -> None:
    documents = _documents("deploy/kubernetes/runtime-network-policy.yaml")
    policies = {document["metadata"]["name"]: document for document in documents}

    assert set(policies) == {
        "default-deny-ingress",
        "allow-runtime-ingress",
        "default-deny-egress",
        "allow-runtime-egress",
    }
    assert policies["default-deny-ingress"]["spec"] == {
        "podSelector": {},
        "policyTypes": ["Ingress"],
    }
    assert policies["default-deny-egress"]["spec"] == {
        "podSelector": {},
        "policyTypes": ["Egress"],
    }

    allow_ingress = policies["allow-runtime-ingress"]["spec"]
    assert allow_ingress["podSelector"]["matchLabels"] == {
        "app.kubernetes.io/name": "open-workflow-agent"
    }
    assert allow_ingress["ingress"][0]["ports"] == [{"protocol": "TCP", "port": 8080}]

    allow_egress = policies["allow-runtime-egress"]["spec"]
    egress_ports = {
        (port["protocol"], port["port"])
        for entry in allow_egress["egress"]
        for port in entry["ports"]
    }
    assert egress_ports == {
        ("UDP", 53),
        ("TCP", 53),
        ("TCP", 443),
        ("TCP", 5432),
        ("TCP", 8090),
    }


def test_optional_edge_routes_target_the_reference_runtime_service() -> None:
    documents = _documents("deploy/kubernetes/edge-routes.yaml")
    assert {document["kind"] for document in documents} == {"Ingress", "HTTPRoute"}

    ingress = next(document for document in documents if document["kind"] == "Ingress")
    assert ingress["spec"]["ingressClassName"] == "nginx"
    assert ingress["spec"]["tls"] == [{"hosts": ["owa.example.com"], "secretName": "owa-tls"}]
    ingress_backend = ingress["spec"]["rules"][0]["http"]["paths"][0]["backend"]
    assert ingress_backend == {"service": {"name": "owa-adk", "port": {"name": "http"}}}

    route = next(document for document in documents if document["kind"] == "HTTPRoute")
    assert route["spec"]["parentRefs"] == [{"name": "owa-gateway", "sectionName": "http"}]
    backend = route["spec"]["rules"][0]["backendRefs"][0]
    assert backend == {"name": "owa-adk", "port": 8080}


def test_optional_monitoring_templates_match_runtime_metrics_and_service_labels() -> None:
    documents = _documents("deploy/kubernetes/monitoring.yaml")
    assert {document["kind"] for document in documents} == {"ServiceMonitor", "PrometheusRule"}

    monitor = next(document for document in documents if document["kind"] == "ServiceMonitor")
    assert monitor["spec"]["selector"]["matchLabels"] == {
        "app.kubernetes.io/name": "open-workflow-agent",
        "app.kubernetes.io/instance": "owa-adk",
    }
    assert monitor["spec"]["endpoints"] == [
        {"port": "http", "path": "/metrics", "interval": "30s", "scrapeTimeout": "10s"}
    ]

    rule = next(document for document in documents if document["kind"] == "PrometheusRule")
    alerts = rule["spec"]["groups"][0]["rules"]
    assert {alert["alert"] for alert in alerts} == {
        "OwaRuntimeHighHttpErrorRate",
        "OwaRuntimeHighHttpLatency",
        "OwaSandboxExecutionFailures",
    }
    expressions = "\n".join(alert["expr"] for alert in alerts)
    assert "owa_http_errors_total" in expressions
    assert "owa_http_request_duration_seconds_bucket" in expressions
    assert 'owa_sandbox_executions_total{event="failed"}' in expressions
