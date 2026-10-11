from __future__ import annotations

import copy
import gc
import json

import pytest
from open_workflow_agent.errors import ExpressionError
from open_workflow_agent.expressions import ExpressionEvaluator, validate_expression_data
from open_workflow_agent.workflow import (
    WorkflowExecutor,
    compile_workflow,
    generate_default_workflow,
)


@pytest.mark.parametrize(
    "source,data,expected",
    [
        (
            "[.items[] | select(.score >= 3) | .name]",
            {"items": [{"score": 4, "name": "A"}, {"score": 1, "name": "B"}]},
            ["A"],
        ),
        ("reduce .[] as $x (0; . + $x)", [1, 2, 3], 6),
        ("map(. * 2) | sort | unique", [3, 1, 3], [2, 6]),
        (
            "group_by(.kind) | map({kind: .[0].kind, count: length})",
            [{"kind": "b"}, {"kind": "a"}, {"kind": "b"}],
            [{"kind": "a", "count": 1}, {"kind": "b", "count": 2}],
        ),
        ("def twice($x): $x * 2; twice(.n)", {"n": 3}, 6),
        ("if .ok then .value else null end", {"ok": False, "value": 2}, None),
        ('try (.items[]) catch "failed"', {"items": 1}, "failed"),
        ('.missing // "default"', {}, "default"),
        (".items[-1]", {"items": [2, 3]}, 3),
        (".items[1:3]", {"items": [0, 1, 2, 3]}, [1, 2]),
        (".missing", {}, None),
        ('"x}y"', {}, "x}y"),
        (
            '{nested: {a: [true, null]}, text: "}"}',
            {},
            {"nested": {"a": [True, None]}, "text": "}"},
        ),
        ('"name=\\(.name), n=\\(.n + 1)"', {"name": "Ada", "n": 2}, "name=Ada, n=3"),
        ('"brace=\\({x: "}"} | .x)"', {}, "brace=}"),
        ("# A closing brace } in a comment\n.n", {"n": 3}, 3),
        (". | to_entries | from_entries", {"a": 1, "b": False}, {"a": 1, "b": False}),
        ('"é界" | length', {}, 2),
        ("(1 + 2) * 3", {}, 9),
        ("false or (0 and true)", {}, True),
    ],
)
def test_pinned_jq_semantics(source, data, expected):
    evaluator = ExpressionEvaluator()
    evaluator.validate("${ " + source + " }", variables=set())
    assert evaluator.evaluate("${ " + source + " }", data) == expected


@pytest.mark.parametrize("value", [0, "", [], {}, 1, "false"])
def test_condition_uses_jq_truthiness(value):
    assert ExpressionEvaluator().condition("${ . }", value) is True


@pytest.mark.parametrize("value", [None, False])
def test_only_null_and_false_are_false(value):
    assert ExpressionEvaluator().condition("${ . }", value) is False


def test_templates_preserve_atomic_types_and_use_json_for_embedded_values():
    evaluator = ExpressionEvaluator()
    data = {"a": [True, None], "n": 2, "object": {"é": False}}
    assert evaluator.evaluate("  ${ .a }  ", data) == [True, None]
    assert (
        evaluator.evaluate("a=${ .a }; n=${ .n }; obj=${ .object }", data)
        == 'a=[true,null]; n=2; obj={"é":false}'
    )
    assert evaluator.evaluate('text=${ "left}right" }', data) == "text=left}right"
    assert evaluator.evaluate({"nested": ["${ .n }", "plain"]}, data) == {"nested": [2, "plain"]}


@pytest.mark.parametrize("literal", [".n", "$input", "[1,2]", '{"a":2}', "true", "null", "plain"])
def test_strict_mode_does_not_interpret_unwrapped_strings(literal):
    assert ExpressionEvaluator().evaluate(literal, {"n": 2}) == literal


def test_loose_mode_evaluates_strings_and_preserves_failures_as_literals():
    evaluator = ExpressionEvaluator(mode="loose")
    assert evaluator.evaluate(".n + 2", {"n": 3}) == 5
    assert evaluator.evaluate("true", {}) is True
    assert evaluator.evaluate("[1,2]", {}) == [1, 2]
    for value in ("plain text", "${ missing( }", '${ error("private") }', "${ empty }", "${ 1,2 }"):
        assert evaluator.evaluate(value, {}) == value


@pytest.mark.parametrize(
    "source",
    [
        "empty",
        "1,2",
        ".items[]",
        'error("private")',
        ".n + []",
        "unknown_filter",
        "@flag",
        "'single-quoted'",
        "$missing",
        ".(",
    ],
)
def test_invalid_or_non_scalar_result_is_a_sanitized_expression_error(source):
    with pytest.raises(ExpressionError) as failure:
        ExpressionEvaluator().evaluate("${ " + source + " }", {"items": [1, 2], "n": 3})
    assert failure.value.status_code == 400
    assert "private" not in json.dumps(failure.value.as_dict())


@pytest.mark.parametrize(
    "source",
    [
        "env",
        "$ENV",
        "env | keys",
        '"\\($ENV)"',
        "def e: env; e",
        "input",
        "inputs",
        "debug",
        'debug("private")',
        "stderr",
        "halt",
        "halt_error",
        "halt_error(1)",
        "modulemeta",
        "now",
        'include "private"; .',
        'import "private" as p; .',
    ],
)
def test_ambient_io_and_module_access_are_disabled(source, monkeypatch, capfd):
    monkeypatch.setenv("OWA_JQ_TEST_SECRET", "private-environment-value")
    with pytest.raises(ExpressionError) as failure:
        ExpressionEvaluator().evaluate("${ " + source + " }", {})
    assert "private-environment-value" not in json.dumps(failure.value.as_dict())
    captured = capfd.readouterr()
    assert not captured.out and not captured.err


def test_io_named_data_fields_and_string_literals_remain_data():
    evaluator = ExpressionEvaluator()
    assert evaluator.evaluate(
        '${ {import: .import, include: .include, literal: "$ENV env"} }',
        {"import": 1, "include": 2},
    ) == {"import": 1, "include": 2, "literal": "$ENV env"}


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), 2**53, -(2**53), float(2**53), (1, 2), {1: "value"}, object()],
)
def test_non_portable_data_is_rejected(value):
    with pytest.raises(ExpressionError):
        validate_expression_data(value)
    with pytest.raises(ExpressionError):
        ExpressionEvaluator(mode="loose").evaluate(".", value)


def test_safe_integer_boundaries_and_defensive_copies():
    evaluator = ExpressionEvaluator()
    data = {"numbers": [-(2**53 - 1), 2**53 - 1], "nested": []}
    original = copy.deepcopy(data)
    result = evaluator.evaluate("${ . }", data)
    result["nested"].append(1)
    assert data == original
    with pytest.raises(ExpressionError):
        evaluator.evaluate("${ 9007199254740992 }", {})
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ExpressionError):
        validate_expression_data(cycle)


@pytest.mark.parametrize(
    "slot",
    [
        "set",
        "input",
        "output",
        "export",
        "if",
        "with",
        "wait",
        "switch",
        "for",
        "emit",
        "listen",
        "raise",
    ],
)
def test_expression_syntax_is_validated_before_effects_in_all_slots(slot):
    invalid = '${ error("private") | missing_function }'
    workflow = generate_default_workflow()
    task = {"set": {"ok": True}}
    if slot in {"input", "output", "export"}:
        task[slot] = {"from" if slot == "input" else "as": invalid}
    elif slot == "with":
        task = {"call": "llm:1.0.0@default", "with": {"prompt": invalid}}
    elif slot == "wait":
        task = {"wait": invalid}
    elif slot == "switch":
        task = {"switch": [{"one": {"when": invalid, "then": "continue"}}]}
    elif slot == "for":
        task = {"for": {"each": "item", "in": invalid}, "do": [{"body": {"set": {"ok": True}}}]}
    elif slot == "emit":
        task = {"emit": {"event": {"with": {"type": "test", "data": invalid}}}}
    elif slot == "listen":
        task = {"listen": {"to": {"one": {"with": {"type": invalid}}}}}
    elif slot == "raise":
        task = {"raise": {"error": {"type": "urn:test:error", "status": 400, "detail": invalid}}}
    else:
        task[slot] = invalid if slot == "if" else {"value": invalid}
    workflow["do"] = [{"bad": task}]
    with pytest.raises(ExpressionError) as failure:
        compile_workflow(workflow)
    assert failure.value.details["reference"].startswith("/do/0/bad")
    assert "private" not in json.dumps(failure.value.as_dict())


@pytest.mark.asyncio
async def test_user_input_is_never_evaluated_as_workflow_code(services):
    data = {"prompt": "${ env }", "nested": ['${ error("input injection") }', ".value"]}
    received = []

    class RecordingExecutor(WorkflowExecutor):
        async def _call(self, call, payload, state):
            received.append(copy.deepcopy(payload))
            return payload

    for mode in ("strict", "loose"):
        workflow = generate_default_workflow()
        workflow["evaluate"] = {"mode": mode}
        assert (
            await RecordingExecutor(services.catalog).execute(compile_workflow(workflow), data)
            == data
        )
    assert received == [data, data]


def test_unknown_scope_variables_fail_admission_and_valid_loop_variables_compile():
    workflow = generate_default_workflow()
    workflow["do"] = [
        {
            "loop": {
                "for": {"each": "color", "in": ["a"]},
                "do": [{"body": {"set": {"item": "${ $color }", "index": "${ $index }"}}}],
            }
        }
    ]
    compile_workflow(workflow)
    workflow["do"].append({"outside": {"set": {"bad": "${ $color }"}}})
    with pytest.raises(ExpressionError) as failure:
        compile_workflow(workflow)
    assert failure.value.details == {"reference": "/do/1/outside/set"}


@pytest.mark.parametrize("value", ["${ .n", '${ "unterminated }'])
def test_unclosed_templates_fail_validation(value):
    with pytest.raises(ExpressionError):
        ExpressionEvaluator().validate(value, variables=set())


@pytest.mark.parametrize("source", ["nan", "infinite", "-infinite", "[nan]", "{value: infinite}"])
def test_nonfinite_native_results_are_rejected_before_binding_conversion(source):
    with pytest.raises(ExpressionError):
        ExpressionEvaluator().evaluate("${ " + source + " }", {})


def test_native_errors_are_released_before_public_errors_are_created():
    evaluator = ExpressionEvaluator()
    for _ in range(20):
        for source in ('error("private")', "unknown_filter", "1,2", "empty"):
            with pytest.raises(ExpressionError) as failure:
                evaluator.evaluate("${ " + source + " }", {})
            assert failure.value.__cause__ is None
            assert failure.value.__context__ is None
        gc.collect()
        assert evaluator.evaluate("${ .n + 1 }", {"n": 1}) == 2


def test_multiline_comments_strings_and_local_variables_are_parsed_as_jq():
    evaluator = ExpressionEvaluator()
    assert (
        evaluator.evaluate(
            "${ # continued \\\nclosing } and $ENV are comment text\n .n }", {"n": 3}
        )
        == 3
    )
    assert evaluator.evaluate('${ "escaped \\" brace }" }', {}) == 'escaped " brace }'
    assert (
        evaluator.evaluate('${ "literal \\\\(not interpolation)" }', {})
        == "literal \\(not interpolation)"
    )
    assert evaluator.evaluate("${ $_local }", {}, variables={"_local": 2}) == 2
    assert (
        evaluator.evaluate(
            "${ $context._user }",
            {},
            variables={"context": {"_user": 3, "_lifecycle": "ordinary data"}},
        )
        == 3
    )
    with pytest.raises(ExpressionError):
        evaluator.evaluate("${ $input }", {}, variables={"bad-key": 1})
    with pytest.raises(ExpressionError):
        evaluator.evaluate("${ $input }", {}, variables={"input": {1: "invalid"}})


@pytest.mark.parametrize("mode", ["strict", "loose"])
def test_ambient_jq_configuration_fails_closed_before_compilation(tmp_path, monkeypatch, mode):
    from open_workflow_agent import expressions

    (tmp_path / ".jq").write_text("def ambient: env;", encoding="utf8")
    monkeypatch.setenv("HOME", str(tmp_path))
    calls = []
    monkeypatch.setattr(expressions.jq, "compile", lambda *args, **kwargs: calls.append(args))
    evaluator = ExpressionEvaluator(mode=mode)
    with pytest.raises(ExpressionError, match="ambient jq module"):
        evaluator.validate("${ . }", variables=set())
    with pytest.raises(ExpressionError, match="ambient jq module"):
        evaluator.evaluate("${ . }", {})
    assert not calls


def test_missing_home_is_not_mutated_by_evaluation(monkeypatch):
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.delenv("HOMEPATH", raising=False)
    assert ExpressionEvaluator().evaluate("${ .n }", {"n": 2}) == 2


@pytest.mark.asyncio
async def test_non_json_task_results_fail_without_output_transformation(services):
    class InvalidResultExecutor(WorkflowExecutor):
        async def _call(self, call, payload, state):
            return {"value": float("nan")}

    with pytest.raises(ExpressionError):
        await InvalidResultExecutor(services.catalog).execute(compile_workflow(), {})


@pytest.mark.asyncio
async def test_named_loop_bindings_are_available_to_the_pinned_evaluator(services):
    workflow = generate_default_workflow()
    workflow["do"] = [
        {
            "loop": {
                "for": {"each": "_color", "at": "position", "in": "${ .colors }"},
                "do": [{"shape": {"set": {"color": "${ $_color }", "position": "${ $position }"}}}],
            }
        }
    ]
    result = await WorkflowExecutor(services.catalog).execute(
        compile_workflow(workflow), {"colors": ["a", "b"]}
    )
    assert result["color"] == "b"
    assert result["position"] == 1


def test_descriptor_wrappers_do_not_reduce_the_user_input_depth_limit():
    data = "leaf"
    for _ in range(64):
        data = [data]
    evaluator = ExpressionEvaluator()
    variables = {"workflow": {"input": data}, "task": {"input": data}}
    assert evaluator.evaluate("${ $workflow.input }", data, variables=variables) == data
    assert evaluator.evaluate("${ $task.input }", data, variables=variables) == data
    with pytest.raises(ExpressionError):
        evaluator.evaluate("${ . }", [data], variables=variables)


def test_unused_descriptor_values_are_not_native_jq_arguments():
    assert (
        ExpressionEvaluator().evaluate(
            "${ .value }", {"value": 2}, variables={"workflow": {"input": object()}}
        )
        == 2
    )
