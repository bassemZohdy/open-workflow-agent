"""JSONata evaluation and task mapping helpers."""

from __future__ import annotations

import ctypes
import os
import re
import signal
import threading
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any

import jsonata


class ExpressionError(ValueError):
    """Raised when an OpenWorkflow expression cannot be evaluated."""


_UNSAFE_FUNCTIONS = ("$eval", "$function")


def _limit(expression: str) -> None:
    if any(token in expression for token in _UNSAFE_FUNCTIONS):
        raise ExpressionError("dynamic expression functions are disabled")
    maximum = int(os.environ.get("WORKFLOW_EXPRESSION_MAX_LENGTH", "10000"))
    if len(expression) > maximum:
        raise ExpressionError(f"expression exceeds maximum length of {maximum}")
    depth = 0
    maximum_depth = 0
    pairs = {"(": ")", "[": "]", "{": "}"}
    for character in expression:
        if character in pairs:
            depth += 1
            maximum_depth = max(maximum_depth, depth)
        elif character in pairs.values():
            depth -= 1
    configured_depth = int(os.environ.get("WORKFLOW_EXPRESSION_MAX_DEPTH", "100"))
    if depth < 0 or maximum_depth > configured_depth:
        raise ExpressionError(f"expression exceeds maximum depth of {configured_depth}")


def _raise_in_thread(thread_id: int, exception: type[BaseException]) -> None:
    """Inject an exception into a running thread (Windows/non-main-thread fallback)."""
    result = ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_long(thread_id), ctypes.py_object(exception)
    )
    if result == 0:
        raise ValueError("invalid thread id")
    if result > 1:
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(thread_id), None)
        raise RuntimeError("PyThreadState_SetAsyncExc failed")


def _alarm_handler(_signum: int, _frame: Any) -> None:
    """Raise directly in the evaluating frame when the POSIX alarm fires.

    The previous implementation threw ``TimeoutError`` from inside a discarded
    generator frame, which never reached the code under evaluation. Raising
    directly from the signal handler interrupts pure-Python evaluation at the
    next bytecode boundary, which reliably breaks pathological expressions.
    ``jsonata-python`` is currently pure Python; if that implementation gains a
    native evaluation core, this signal-based timeout must be revisited because
    raising from a handler while C code is active is unsafe.
    """
    raise TimeoutError


@contextmanager
def _evaluation_budget() -> Any:
    """Bound synchronous evaluation with a cross-platform timeout."""
    seconds = float(os.environ.get("WORKFLOW_EXPRESSION_TIMEOUT_SECONDS", "0.25"))
    if seconds <= 0:
        yield
        return
    use_signal = os.name == "posix" and threading.current_thread() is threading.main_thread()
    previous: Any = None
    timer: threading.Timer | None = None
    if use_signal:
        previous = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, _alarm_handler)
        signal.setitimer(signal.ITIMER_REAL, seconds)
    else:
        thread_id = threading.current_thread().ident
        if thread_id is not None:
            timer = threading.Timer(seconds, _raise_in_thread, args=(thread_id, TimeoutError))
            timer.start()
    try:
        yield
    except TimeoutError as exc:
        raise ExpressionError("expression evaluation exceeded its time budget") from exc
    finally:
        if use_signal:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
        elif timer is not None:
            timer.cancel()


def _unwrap(expression: str) -> str:
    expression = expression.strip()
    if expression.startswith("${") and expression.endswith("}"):
        expression = expression[2:-1].strip()
    # OpenWorkflow uses `.` for the current input; JSONata uses `$`. A leading
    # dot is rewritten directly; a standalone dot (not part of a path such as
    # `.foo` or `a.b`, e.g. in `( . | length ) > 2`) is rewritten in place.
    if expression.startswith("."):
        expression = "$" + expression
    else:
        expression = re.sub(r"(?<![\w$])\.(?![\w$])", "$", expression)
    # The spec's canonical `( x | length )` idiom counts the current context;
    # translate it to the equivalent (and portable) `$count( x )`.
    expression = re.sub(r"\(\s*([^()]*?)\s*\|\s*length\s*\)", r"$count(\1)", expression)
    # OpenWorkflow examples commonly use JavaScript-style equality; JSONata
    # spells equality with a single equals sign.
    return re.sub(r"(?<![=!])==(?!=)", "=", expression)


def evaluate(expression: Any, data: Mapping[str, Any] | Any = None) -> Any:
    """Evaluate a JSONata expression against workflow data.

    Bare non-string values are returned unchanged. The data mapping may contain
    `context` and `workflow` values; these are also exposed as `$context` and
    `$workflow` through JSONata bindings.
    """
    if not isinstance(expression, str):
        return expression
    expression = _unwrap(expression)
    if not expression:
        return None
    _limit(expression)
    root = data if data is not None else {}
    bindings = {}
    if isinstance(root, Mapping):
        bindings = {
            "context": root.get("context", root.get("$context")),
            "workflow": root.get("workflow", root.get("$workflow")),
        }
    try:
        with _evaluation_budget():
            return jsonata.Jsonata(expression).evaluate(root, bindings)
    except Exception as exc:  # library exposes multiple implementation-specific errors
        raise ExpressionError(f"could not evaluate {expression!r}: {exc}") from exc


def bind(value: Any, data: Mapping[str, Any] | Any = None) -> Any:
    """Recursively evaluate expression-valued strings in a structure."""
    if isinstance(value, str):
        return evaluate(value, data) if value.strip().startswith("${") else value
    if isinstance(value, list):
        return [bind(item, data) for item in value]
    if isinstance(value, dict):
        return {key: bind(item, data) for key, item in value.items()}
    return value


def condition(value: str | None, data: Mapping[str, Any] | Any = None) -> bool:
    """Evaluate an OpenWorkflow condition using JSONata truthiness."""
    if value is None:
        return True
    return bool(evaluate(value, data))


def apply_task_mappings(
    task: Mapping[str, Any], data: Mapping[str, Any], output: Any = None
) -> dict[str, Any]:
    """Apply task `input`, `output`, `export`, and `set` mappings.

    The returned dictionary is a new state mapping. `set` writes named values;
    `export.as` may replace or extend the context using JSONata object output.
    """
    state = dict(data)
    if "set" in task and isinstance(task["set"], Mapping):
        for key, value in task["set"].items():
            state[key] = evaluate(value, state)
    if "output" in task and isinstance(task["output"], Mapping):
        output_as = task["output"].get("as")
        if output_as is not None:
            output = evaluate(output_as, {**state, "output": output})
    if "export" in task and isinstance(task["export"], Mapping):
        export_as = task["export"].get("as")
        if export_as is not None:
            exported = evaluate(export_as, {**state, "output": output})
            if not isinstance(exported, Mapping):
                raise ExpressionError("task export must evaluate to an object")
            state.update(exported)
    return state
