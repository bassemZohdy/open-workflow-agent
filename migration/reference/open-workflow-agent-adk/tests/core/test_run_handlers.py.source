import shutil
import sys

import pytest

from openworkflow_adk import load, run_workflow


async def test_registered_function_call_returns_value() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "function",
                "version": "1.0.0",
            },
            "do": [{"greet": {"call": "greet", "with": {"name": "Ada"}}}],
        }
    )

    events = await run_workflow(document, function_registry={"greet": lambda name: f"Hi {name}"})

    assert any(event.output == "Hi Ada" for event in events)


async def test_shell_run_returns_stdout() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "shell",
                "version": "1.0.0",
            },
            "do": [
                {
                    "say": {
                        "run": {
                            "shell": {
                                "command": sys.executable,
                                "arguments": ["-c", "import sys; sys.stdout.write('hello')"],
                            }
                        }
                    }
                }
            ],
        }
    )

    events = await run_workflow(document)

    assert any(event.output == "hello" for event in events)


async def test_python_script_run_returns_stdout() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "script",
                "version": "1.0.0",
            },
            "do": [
                {
                    "say": {
                        "run": {"script": {"language": "python", "code": "print('hello-script')"}}
                    }
                }
            ],
        }
    )

    events = await run_workflow(document)

    assert any(event.output == "hello-script\n" for event in events)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for JavaScript scripts")
async def test_javascript_script_run_returns_stdout() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "javascript-script",
                "version": "1.0.0",
            },
            "do": [
                {
                    "say": {
                        "run": {
                            "script": {
                                "language": "javascript",
                                "code": "console.log('hello-js')",
                            }
                        }
                    }
                }
            ],
        }
    )

    events = await run_workflow(document)

    assert any(event.output == "hello-js\n" for event in events)


async def test_process_timeout_terminates_run_handler() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "timeout",
                "version": "1.0.0",
            },
            "do": [
                {
                    "sleep": {
                        "timeout": {"after": "PT0.01S"},
                        "run": {
                            "shell": {
                                "command": sys.executable,
                                "arguments": ["-c", "import time; time.sleep(1)"],
                            }
                        },
                    }
                }
            ],
        }
    )

    with pytest.raises(TimeoutError):
        await run_workflow(document)
