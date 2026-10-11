from openworkflow_adk import load, portability_report


def test_document_round_trip_is_portable() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "portable",
                "version": "1.0.0",
            },
            "do": [{"one": {"set": {"ok": '"yes"'}}}],
        }
    )

    report = portability_report(document)

    assert report["portable"]
    assert report["tasks"] == ["one"]
