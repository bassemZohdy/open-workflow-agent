from __future__ import annotations

import sys
import types

from open_workflow_agent.config import RuntimeConfig


def test_server_main_configures_logging_and_starts_uvicorn(monkeypatch, tmp_path):
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(
        "model:\n"
        "  provider: fake\n"
        "knowledge:\n"
        f"  database: {tmp_path / 'knowledge.sqlite3'}\n"
        "memory:\n"
        f"  database: {tmp_path / 'memory.sqlite3'}\n"
        "persistence:\n"
        f"  database: {tmp_path / 'runtime.sqlite3'}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OWA_CONFIG_FILE", str(config_file))
    import open_workflow_agent.server as server

    config = RuntimeConfig.model_validate(
        {"server": {"host": "127.0.0.1", "port": 9090}, "observability": {"log_level": "DEBUG"}}
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(server.RuntimeConfig, "from_file", classmethod(lambda cls: config))
    monkeypatch.setattr(
        server,
        "configure_logging",
        lambda **kwargs: captured.update({"logging": kwargs}),
    )
    uvicorn = types.ModuleType("uvicorn")

    def run(app, **kwargs):
        captured.update({"app": app, **kwargs})

    uvicorn.run = run  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn)

    server.main()

    assert captured["app"] is server.app
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9090
    assert captured["logging"] == {"log_level": "DEBUG", "structured": False}
