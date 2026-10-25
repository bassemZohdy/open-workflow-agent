"""Finite deployment-enabled gRPC profile without private route bindings."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any


def grpc_capabilities(*, enabled: bool) -> dict[str, Any]:
    try:
        available = version("grpcio") == "1.84.0" and version("protobuf") == "7.36.1"
    except PackageNotFoundError:
        available = False
    return {
        "enabled": enabled and available,
        "sdkVersion": "1.84.0",
        "protobufVersion": "7.36.1",
        "sdkAvailable": available,
        "conformanceClaim": False,
        "transport": "unary",
        "deploymentBindingRequired": True,
        "runtimeCompilation": False,
        "reflection": False,
        "streaming": False,
        "rootWellKnownValue": False,
        "nestedWellKnownValue": True,
        "authentication": "exact_named_endpoint",
        "tlsVerification": True,
        "insecureLoopback": "deployment_opt_in",
        "recovery": {
            "uncertainOutcome": "pending",
            "automaticReplay": False,
            "applicationStatus": "explicit_deployment_profile",
            "nativeProjection": "common_receipt_dependency",
        },
    }
