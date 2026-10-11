from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openworkflow_adk import load, run_workflow
from openworkflow_adk.internal import WorkflowTelemetry


async def test_workflow_telemetry_records_run_and_task_spans() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "telemetry",
                "version": "1.0.0",
            },
            "do": [{"save": {"set": {"value": "1"}}}],
        }
    )

    await run_workflow(document, telemetry=WorkflowTelemetry())

    spans = exporter.get_finished_spans()
    names = [span.name for span in spans]
    assert "workflow.task" in names
    assert "workflow.run" in names
    task_span = next(span for span in spans if span.name == "workflow.task")
    assert task_span.attributes["workflow.task"]
