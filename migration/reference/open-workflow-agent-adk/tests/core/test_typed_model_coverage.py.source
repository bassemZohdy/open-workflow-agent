from openworkflow_adk import load
from openworkflow_adk.models import TASK_KEYS, Task
from openworkflow_adk.translator import task_kind


def test_typed_document_model_accepts_all_task_kinds() -> None:
    configurations = {
        "call": {"call": "named"},
        "do": {"do": [{"nested": {"wait": {"seconds": 0}}}]},
        "fork": {"fork": {"branches": [{"branch": {"wait": {"seconds": 0}}}]}},
        "emit": {"emit": {"event": {"with": {"source": "https://example.test", "type": "demo"}}}},
        "for": {"for": {"in": ".items"}, "do": [{"nested": {"wait": {"seconds": 0}}}]},
        "listen": {"listen": {"to": {"one": {"with": {"type": "demo"}}}}},
        "raise": {"raise": {"error": {"type": "https://example.test/error", "status": 500}}},
        "run": {"run": {"shell": {"command": "true"}}},
        "set": {"set": {"value": '"ok"'}},
        "switch": {"switch": [{"default": {"then": "end"}}]},
        "try": {
            "try": [{"nested": {"wait": {"seconds": 0}}}],
            "catch": {"do": []},
        },
        "wait": {"wait": {"seconds": 0}},
    }
    raw = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "typed",
            "name": "all-tasks",
            "version": "1.0.0",
        },
        "do": [{f"task_{name}": value} for name, value in configurations.items()],
    }

    document = load(raw)

    assert set(TASK_KEYS) == set(configurations)
    assert all(task_kind(item.task) in configurations for item in document.do)
    field_names = set(Task.model_fields)
    assert field_names >= (set(TASK_KEYS) - {"for", "raise", "try"}) | {
        "for_",
        "raise_",
        "try_",
        "with_",
    }
