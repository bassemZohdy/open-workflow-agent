from openworkflow_adk import lint_workflow, load, workflow_mermaid, workflow_plan


def test_linter_catches_unknown_goto() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "lint",
                "version": "1.0.0",
            },
            "do": [{"first": {"wait": {"seconds": 0}, "then": "missing"}}],
        }
    )

    diagnostics = lint_workflow(document)

    assert any(item.code == "unknown-target" for item in diagnostics)


def test_plan_and_mermaid_include_compiled_edges() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "plan",
                "version": "1.0.0",
            },
            "do": [
                {"first": {"wait": {"seconds": 0}}},
                {"second": {"wait": {"seconds": 0}}},
            ],
        }
    )

    plan = workflow_plan(document)

    assert {"first", "second"} <= set(plan["nodes"])
    assert "first --> second" in workflow_mermaid(document)


def test_linter_recurse_into_nested_tasks_for_missing_instruction() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "nested",
                "version": "1.0.0",
            },
            "do": [
                {
                    "outer": {
                        "do": [
                            {
                                "inner": {
                                    "wait": {"seconds": 0},
                                    "metadata": {"adk": {"agent": {"model": "gemini-2.5-flash"}}},
                                }
                            }
                        ]
                    }
                }
            ],
        }
    )

    diagnostics = lint_workflow(document)

    assert any(item.code == "agent-instruction" and "inner" in item.message for item in diagnostics)


def test_linter_reports_each_duplicate_task_name_once() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "duplicates",
                "version": "1.0.0",
            },
            "do": [
                {"same": {"wait": {"seconds": 0}}},
                {"same": {"wait": {"seconds": 0}}},
                {"same": {"wait": {"seconds": 0}}},
            ],
        }
    )

    diagnostics = lint_workflow(document)

    assert sum(item.code == "duplicate-task" for item in diagnostics) == 1
