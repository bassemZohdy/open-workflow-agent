"""Small stdio JSON-RPC language service for OpenWorkflow documents.

The service intentionally uses only the project parser and standard-library
JSON-RPC framing so editor integrations do not need a separate web server.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass
from typing import Any, TextIO

from openworkflow_adk.devtools.diagnostics import lint_workflow
from openworkflow_adk.loader import WorkflowValidationError, load
from openworkflow_adk.models import TASK_KEYS

_TASK_LINE = re.compile(r"^\s*-\s*([A-Za-z][A-Za-z0-9_-]*)\s*:")
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceTask:
    name: str
    line: int
    character: int


def _source_tasks(text: str) -> list[SourceTask]:
    tasks: list[SourceTask] = []
    in_do = False
    for line_number, line in enumerate(text.splitlines()):
        if re.match(r"^\s*do\s*:", line):
            in_do = True
            continue
        if not in_do:
            continue
        match = _TASK_LINE.match(line)
        if match:
            tasks.append(SourceTask(match.group(1), line_number, match.start(1)))
    return tasks


def _line_for_path(path: str, tasks: list[SourceTask]) -> int:
    match = re.search(r"do\[(\d+)\]", path)
    if match:
        index = int(match.group(1))
        if index < len(tasks):
            return tasks[index].line
    return 0


class DiagnosticsServer:
    """In-memory document server implementing common editor requests."""

    def __init__(self) -> None:
        self.documents: dict[str, str] = {}

    def open_document(self, uri: str, text: str) -> list[dict[str, Any]]:
        self.documents[uri] = text
        return self.diagnostics(uri)

    def diagnostics(self, uri: str) -> list[dict[str, Any]]:
        text = self.documents.get(uri, "")
        tasks = _source_tasks(text)
        try:
            document = load(text)
            errors = [item.as_dict() for item in lint_workflow(document)]
        except WorkflowValidationError as error:
            errors = [dict(item, severity="error") for item in error.errors]
        return [
            {
                "range": {
                    "start": {"line": _line_for_path(item["path"], tasks), "character": 0},
                    "end": {"line": _line_for_path(item["path"], tasks), "character": 1},
                },
                "severity": 1 if item.get("severity") == "error" else 2,
                "code": item.get("code", "validation"),
                "message": item["message"],
                "source": "owf-adk",
            }
            for item in errors
        ]

    def hover(self, uri: str, line: int, character: int) -> dict[str, Any] | None:
        del character
        text = self.documents.get(uri, "")
        task = next((item for item in _source_tasks(text) if item.line == line), None)
        if task is None:
            return None
        return {
            "contents": {"kind": "markdown", "value": f"**{task.name}**\n\nOpenWorkflow task"},
            "range": {
                "start": {"line": task.line, "character": task.character},
                "end": {"line": task.line, "character": task.character + len(task.name)},
            },
        }

    def completion(self, uri: str) -> list[dict[str, str]]:
        text = self.documents.get(uri, "")
        names = [task.name for task in _source_tasks(text)]
        return [{"label": value, "kind": "keyword"} for value in (*TASK_KEYS, *names)]

    def go_to_task(self, uri: str, name: str) -> dict[str, Any] | None:
        task = next(
            (item for item in _source_tasks(self.documents.get(uri, "")) if item.name == name), None
        )
        if task is None:
            return None
        return {
            "uri": uri,
            "range": {
                "start": {"line": task.line, "character": task.character},
                "end": {"line": task.line, "character": task.character + len(task.name)},
            },
        }

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        if method in {"textDocument/didOpen", "textDocument/didChange"}:
            document = params.get("textDocument", {})
            uri = str(document.get("uri", ""))
            text = str(document.get("text", params.get("content", "")))
            return {"diagnostics": self.open_document(uri, text)}
        if method == "textDocument/hover":
            document = params.get("textDocument", {})
            position = params.get("position", {})
            return self.hover(
                str(document.get("uri", "")),
                int(position.get("line", 0)),
                int(position.get("character", 0)),
            )
        if method == "textDocument/completion":
            document = params.get("textDocument", {})
            return {"isIncomplete": False, "items": self.completion(str(document.get("uri", "")))}
        if method == "workflow/goToTask":
            return self.go_to_task(str(params.get("uri", "")), str(params.get("name", "")))
        if method == "textDocument/diagnostics":
            return {"items": self.diagnostics(str(params.get("textDocument", {}).get("uri", "")))}
        if method == "shutdown":
            return None
        raise ValueError(f"unsupported method: {method}")


def serve_stdio(input_stream: TextIO = sys.stdin, output_stream: TextIO = sys.stdout) -> None:
    """Serve newline-delimited JSON-RPC requests over stdio."""
    server = DiagnosticsServer()
    for line in input_stream:
        if not line.strip():
            continue
        request = json.loads(line)
        response: dict[str, Any] = {"jsonrpc": "2.0", "id": request.get("id")}
        try:
            response["result"] = server.request(request["method"], request.get("params"))
        except Exception:  # pragma: no cover - protocol boundary
            _LOGGER.exception("diagnostics request failed", extra={"method": request.get("method")})
            response["error"] = {"code": -32603, "message": "internal error"}
        output_stream.write(json.dumps(response) + "\n")
        output_stream.flush()
