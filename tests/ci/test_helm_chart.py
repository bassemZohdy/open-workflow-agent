from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
CHART = ROOT / "deploy" / "helm" / "open-workflow-agent"


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is not installed")
def test_helm_chart_lints_and_renders_optional_integrations() -> None:
    subprocess.run(["helm", "lint", str(CHART)], cwd=ROOT, check=True)
    rendered = subprocess.run(
        [
            "helm",
            "template",
            "owa",
            str(CHART),
            "--namespace",
            "owa",
            "--set",
            "ingress.enabled=true",
            "--set",
            "httpRoute.enabled=true",
            "--set",
            "monitoring.serviceMonitor.enabled=true",
            "--set",
            "monitoring.prometheusRule.enabled=true",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "kind: Deployment" in rendered
    assert "kind: Ingress" in rendered
    assert "kind: HTTPRoute" in rendered
    assert "kind: ServiceMonitor" in rendered
    assert "kind: PrometheusRule" in rendered
    assert "name: owa-open-workflow-agent" in rendered
