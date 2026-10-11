"""Source exporters for portable workflow runtimes."""

from __future__ import annotations

import keyword

from openworkflow_adk.models import OpenWorkflowDocument


def _safe_identifier(value: str, prefix: str) -> str:
    """Convert a task/document name into a valid, non-keyword identifier."""
    candidate = "".join(char if char.isalnum() else "_" for char in value)
    if not candidate.strip("_"):
        candidate = prefix
    elif candidate[0].isdigit() or keyword.iskeyword(candidate):
        candidate = f"{prefix}_{candidate}"
    return candidate


def export_temporal(document: OpenWorkflowDocument) -> str:
    """Generate a deterministic Temporal Python workflow skeleton.

    The generated source requires the optional ``temporalio`` dependency;
    install it with ``pip install open-workflow-agent-adk[temporal]`` first.
    """
    safe_name = _safe_identifier(document.document.name, "workflow")
    class_name = (
        "".join(
            f"_{part}" if part and part[0].isdigit() else part.capitalize()
            for part in safe_name.split("_")
        )
        + "Workflow"
    )
    lines = [
        '"""Generated from OpenWorkflow; implement activities before deployment."""',
        "from datetime import timedelta",
        "from temporalio import workflow",
        "",
        "",
        "@workflow.defn",
        f"class {class_name}:",
        "    @workflow.run",
        "    async def run(self, input: dict) -> dict:",
        "        state = dict(input)",
    ]
    for item in document.do:
        activity_name = _safe_identifier(item.name, "activity")
        lines.extend(
            [
                f"        # OpenWorkflow task: {item.name}",
                f"        state['{activity_name}'] = await workflow.execute_activity(",
                f"            {activity_name}, state, start_to_close_timeout=timedelta(minutes=5)",
                "        )",
            ]
        )
    lines.append("        return state")
    return "\n".join(lines) + "\n"
