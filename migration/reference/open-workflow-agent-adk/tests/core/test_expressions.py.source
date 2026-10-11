import pytest

from openworkflow_adk.expressions import (
    ExpressionError,
    apply_task_mappings,
    bind,
    condition,
    evaluate,
)


def test_jsonata_evaluates_paths_and_conditions() -> None:
    data = {"orderType": "electronic", "order": {"id": 42}}

    assert evaluate(".order.id", data) == 42
    assert condition('.orderType == "electronic"', data)


def test_jsonata_template_and_context_bindings() -> None:
    data = {"context": {"error": None}, "items": [1, 2]}

    assert evaluate("${ $context.error = null ? 'ok' : 'bad' }", data) == "ok"
    assert evaluate("${ $count(items) }", data) == 2


def test_task_mappings_update_state() -> None:
    state = apply_task_mappings(
        {
            "set": {"status": '"ready"'},
            "export": {"as": '$merge([$context, {"result": status}])'},
        },
        {"context": {}},
    )

    assert state["status"] == "ready"
    assert state["result"] == "ready"


def test_expression_boundaries_and_errors(monkeypatch) -> None:
    monkeypatch.setenv("WORKFLOW_EXPRESSION_MAX_LENGTH", "3")
    assert evaluate("123") == 123
    with pytest.raises(ExpressionError, match="maximum length of 3"):
        evaluate("1234")

    monkeypatch.setenv("WORKFLOW_EXPRESSION_MAX_LENGTH", "100")
    monkeypatch.setenv("WORKFLOW_EXPRESSION_MAX_DEPTH", "0")
    with pytest.raises(ExpressionError, match="maximum depth of 0"):
        evaluate("(1)")
    with pytest.raises(ExpressionError, match="maximum depth of 0"):
        evaluate(")")

    monkeypatch.setenv("WORKFLOW_EXPRESSION_MAX_DEPTH", "2")
    assert evaluate("((1))") == 1
    with pytest.raises(ExpressionError, match="maximum depth of 2"):
        evaluate("(((1)))")

    for expression in ("[1]", '{"value": 1}'):
        assert evaluate(expression) is not None
    with pytest.raises(ExpressionError, match="dynamic expression functions are disabled"):
        evaluate('$function("return 1")')
    with pytest.raises(ExpressionError, match="could not evaluate"):
        evaluate("this is not valid JSONata ???")


def test_expression_context_fallbacks() -> None:
    data = {"$context": {"value": 7}, "$workflow": {"value": 8}}

    assert evaluate("$context.value", data) == 7
    assert evaluate("$workflow.value", data) == 8


def test_bind_propagates_data_recursively() -> None:
    value = {"items": ["${.value}", {"nested": "${.value}"}]}

    assert bind(value, {"value": 2}) == {"items": [2, {"nested": 2}]}


def test_condition_none_and_false() -> None:
    assert condition(None) is True
    assert condition("false") is False


def test_task_mappings_output_and_export_errors() -> None:
    state = apply_task_mappings(
        {
            "output": {"as": '"formatted"'},
            "export": {"as": '{"answer": output}'},
        },
        {"context": {}},
        output="raw",
    )
    assert state["answer"] == "formatted"

    with pytest.raises(ExpressionError, match="task export must evaluate to an object"):
        apply_task_mappings({"export": {"as": "1"}}, {})

    assert apply_task_mappings({"set": "not-an-object"}, {"keep": True}) == {"keep": True}
