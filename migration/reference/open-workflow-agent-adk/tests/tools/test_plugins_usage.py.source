import hashlib
import json

from openworkflow_adk import PluginRegistry, UsageMetrics


def test_plugin_registry_requires_trusted_manifest_digest(tmp_path) -> None:
    plugin = tmp_path / "demo"
    plugin.mkdir()
    content = json.dumps({"name": "demo", "version": "1.0.0", "entrypoint": "demo:register"})
    path = plugin / "plugin.json"
    path.write_text(content)
    digest = hashlib.sha256(content.encode()).hexdigest()

    assert PluginRegistry().discover(tmp_path) == []
    found = PluginRegistry({digest}).discover(tmp_path)
    assert found[0].name == "demo"


def test_usage_metrics_are_opt_in_and_aggregate_only() -> None:
    disabled = UsageMetrics()
    disabled.record("run", user_id="secret", status="ok")
    assert disabled.snapshot() == {}
    enabled = UsageMetrics(enabled=True)
    enabled.record("run", user_id="secret", status="ok")
    assert enabled.snapshot() == {"run:status=ok": 1}
