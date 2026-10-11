from openworkflow_adk import WorkflowRegistry, load


def test_registry_discovers_workflows_by_intent() -> None:
    documents = [
        load(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "demo",
                    "name": "invoice-alert",
                    "version": "1.0.0",
                    "summary": "Send an invoice payment alert",
                },
                "do": [{"notify": {"set": {"sent": "true"}}}],
            }
        ),
        load(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "demo",
                    "name": "image-resize",
                    "version": "1.0.0",
                    "summary": "Resize uploaded images",
                },
                "do": [{"resize": {"set": {"done": "true"}}}],
            }
        ),
    ]
    results = WorkflowRegistry(documents).search("invoice payment alert")

    assert results[0].document.document.name == "invoice-alert"
    assert results[0].score == 1.0


def test_registry_latest_uses_numeric_pep440_ordering() -> None:
    documents = [
        load(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "demo",
                    "name": "versioned",
                    "version": version,
                },
                "do": [{"finish": {"set": {"version": f'"{version}"'}}}],
            }
        )
        for version in ("1.0.2", "1.0.10", "1.0.9")
    ]

    resolved = WorkflowRegistry(documents).resolve("demo", "versioned")

    assert resolved.document.version == "1.0.10"
