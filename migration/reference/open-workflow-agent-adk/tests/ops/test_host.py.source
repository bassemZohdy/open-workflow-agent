from openworkflow_adk import WorkflowHost, load


async def test_host_shutdown_drains_execution() -> None:
    host = WorkflowHost(shutdown_timeout=1)
    host.install_signal_handlers()
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "host",
                "version": "1.0.0",
            },
            "do": [{"save": {"set": {"value": '"ok"'}}}],
        }
    )

    result = await host.execute(document)

    assert result
    assert await host.shutdown() is True
    assert host.health.readiness()["ready"] is False
