import io
import json

from openworkflow_adk.internal import DiagnosticsServer, serve_stdio

SOURCE = """document:
  dsl: 1.0.3
  namespace: demo
  name: editor
  version: 1.0.0
do:
  - first:
      set:
        value: 1
  - second:
      wait:
        seconds: 0
"""


def test_diagnostics_server_editor_queries() -> None:
    server = DiagnosticsServer()
    uri = "file:///workflow.yaml"
    assert server.open_document(uri, SOURCE) == []
    assert server.hover(uri, 6, 0)["contents"]["value"].startswith("**first**")
    assert server.go_to_task(uri, "second")["range"]["start"]["line"] == 9
    labels = {item["label"] for item in server.completion(uri)}
    assert {"set", "wait", "first", "second"} <= labels


def test_diagnostics_server_stdio_protocol() -> None:
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "textDocument/didOpen",
        "params": {"textDocument": {"uri": "file:///workflow.yaml", "text": SOURCE}},
    }
    output = io.StringIO()
    serve_stdio(io.StringIO(json.dumps(request) + "\n"), output)
    response = json.loads(output.getvalue())
    assert response["id"] == 1
    assert response["result"]["diagnostics"] == []


def test_diagnostics_server_hides_internal_errors(caplog) -> None:
    request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "unsupported/private-secret",
    }
    output = io.StringIO()

    with caplog.at_level("ERROR"):
        serve_stdio(io.StringIO(json.dumps(request) + "\n"), output)

    response = json.loads(output.getvalue())
    assert response["error"] == {"code": -32603, "message": "internal error"}
    assert "unsupported/private-secret" in caplog.text
