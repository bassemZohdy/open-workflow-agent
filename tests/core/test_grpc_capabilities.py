"""Capability observations must not activate a transport or disclose bindings."""

from importlib.metadata import PackageNotFoundError

import pytest
from open_workflow_agent.grpc_capabilities import grpc_capabilities


def test_grpc_is_not_advertised_as_default_enabled():
    value = grpc_capabilities(enabled=False)
    assert value["enabled"] is False and value["conformanceClaim"] is False
    assert value["deploymentBindingRequired"] is True
    assert not any(value[key] for key in ("runtimeCompilation", "reflection", "streaming"))
    assert value["recovery"]["automaticReplay"] is False
    assert value["recovery"]["uncertainOutcome"] == "pending"


@pytest.mark.parametrize("package", ["grpcio", "protobuf"])
def test_unreviewed_or_absent_sdk_disables_the_configured_capability(monkeypatch, package):
    def observed(name):
        if name == package:
            raise PackageNotFoundError(name)
        return {"grpcio": "1.84.0", "protobuf": "7.36.1"}[name]

    monkeypatch.setattr("open_workflow_agent.grpc_capabilities.version", observed)
    assert grpc_capabilities(enabled=True)["enabled"] is False
    monkeypatch.setattr("open_workflow_agent.grpc_capabilities.version", lambda _name: "future")
    assert grpc_capabilities(enabled=True)["enabled"] is False


def test_configured_profile_projects_only_the_pinned_finite_transport(monkeypatch):
    monkeypatch.setattr(
        "open_workflow_agent.grpc_capabilities.version",
        lambda name: {"grpcio": "1.84.0", "protobuf": "7.36.1"}[name],
    )
    value = grpc_capabilities(enabled=True)
    assert value["enabled"] is True and value["sdkAvailable"] is True
    assert value["transport"] == "unary"
    assert value["authentication"] == "exact_named_endpoint"
    assert value["rootWellKnownValue"] is False and value["nestedWellKnownValue"] is True
