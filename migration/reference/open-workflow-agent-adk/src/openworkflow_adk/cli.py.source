"""Command-line entrypoint for the OpenWorkflow ADK translator."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from openworkflow_adk.devtools.diagnostics import (
    Diagnostic,
    lint_workflow,
    workflow_mermaid,
    workflow_plan,
)
from openworkflow_adk.devtools.diagnostics_server import serve_stdio
from openworkflow_adk.loader import _contains_adk_extension, _to_pure_openworkflow, load, load_raw
from openworkflow_adk.models import OpenWorkflowDocument
from openworkflow_adk.ops.polling_worker import PostgresPollingWorker
from openworkflow_adk.ops.postgres_history import PostgresRunHistory, PostgresRunHistoryConfig
from openworkflow_adk.registry import WorkflowRegistry
from openworkflow_adk.runtime import run_workflow


def _add_file_and_mode_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("file", type=Path)
    parser.add_argument("--mode", choices=("auto", "extended"), default="auto")
    parser.add_argument(
        "--catalog-base-dir",
        type=Path,
        help="base directory for resolving relative catalog roots",
    )


def _load_document(args: argparse.Namespace) -> OpenWorkflowDocument:
    return load(args.file, mode=args.mode)


def main() -> int:
    """Run the command-line interface."""
    parser = argparse.ArgumentParser(prog="owf-adk")
    parser.add_argument("--version", action="version", version="%(prog)s 0.2.1")
    commands = parser.add_subparsers(dest="command")

    run_parser = commands.add_parser("run", help="run a workflow document")
    _add_file_and_mode_args(run_parser)
    run_parser.add_argument("--input", default="{}", help="JSON input object")
    run_parser.add_argument("--env", type=Path, help="dotenv-style environment file")

    lint_parser = commands.add_parser("lint", help="lint a workflow document")
    _add_file_and_mode_args(lint_parser)
    lint_parser.add_argument(
        "--strict",
        action="store_true",
        help="also reject ADK extensions so the document is pure OpenWorkflow",
    )

    export_parser = commands.add_parser(
        "export", help="export a workflow document to another format"
    )
    _add_file_and_mode_args(export_parser)
    export_parser.add_argument(
        "--format",
        choices=("openworkflow", "openapi"),
        default="openworkflow",
        help="output format",
    )
    export_parser.add_argument(
        "--base-url",
        default="http://localhost:8080",
        help="base URL for generated OpenAPI specs",
    )
    export_parser.add_argument(
        "--output",
        type=Path,
        help="output file (default: stdout)",
    )

    plan_parser = commands.add_parser("plan", help="print the compiled workflow plan")
    _add_file_and_mode_args(plan_parser)

    graph_parser = commands.add_parser("graph", help="print the compiled workflow as Mermaid")
    _add_file_and_mode_args(graph_parser)

    test_parser = commands.add_parser("test", help="run workflow cases from fixture JSON")
    _add_file_and_mode_args(test_parser)
    test_parser.add_argument("--fixtures", type=Path, required=True)

    serve_parser = commands.add_parser("serve", help="serve a workflow over HTTP")
    _add_file_and_mode_args(serve_parser)
    serve_parser.add_argument("--host", default="127.0.0.1", help="bind host")
    serve_parser.add_argument("--port", type=int, default=8080, help="bind port")
    serve_parser.add_argument(
        "--postgres-url", help="optional PostgreSQL connection URL for durable run history"
    )
    serve_parser.add_argument(
        "--schema", default="openworkflow", help="PostgreSQL schema for history tables"
    )
    serve_parser.add_argument("--namespace", default="default", help="namespace/tenant id")

    dashboard_parser = commands.add_parser(
        "dashboard", help="serve a workflow with PostgreSQL-backed metrics"
    )
    _add_file_and_mode_args(dashboard_parser)
    dashboard_parser.add_argument("--host", default="127.0.0.1", help="bind host")
    dashboard_parser.add_argument("--port", type=int, default=8080, help="bind port")
    dashboard_parser.add_argument("--postgres-url", required=True, help="PostgreSQL connection URL")
    dashboard_parser.add_argument(
        "--schema", default="openworkflow", help="PostgreSQL schema for history tables"
    )
    dashboard_parser.add_argument("--namespace", default="default", help="namespace/tenant id")

    worker_parser = commands.add_parser("worker", help="run a database-polling workflow worker")
    worker_subcommands = worker_parser.add_subparsers(dest="worker_command")
    worker_start = worker_subcommands.add_parser("start", help="start polling worker")
    worker_start.add_argument(
        "--directory", type=Path, required=True, help="directory of workflow YAML files"
    )
    worker_start.add_argument("--postgres-url", required=True, help="PostgreSQL connection URL")
    worker_start.add_argument(
        "--schema", default="openworkflow", help="PostgreSQL schema for history tables"
    )
    worker_start.add_argument("--namespace", default="default", help="namespace/tenant id")
    worker_start.add_argument("--concurrency", type=int, default=4, help="max concurrent runs")
    worker_start.add_argument(
        "--poll-interval", type=float, default=1.0, help="poll interval in seconds"
    )
    worker_start.add_argument(
        "--lease-seconds", type=float, default=30.0, help="run lease duration"
    )
    worker_start.add_argument(
        "--mode", choices=("auto", "extended"), default="auto", help="document loading mode"
    )

    commands.add_parser("diagnostics-server", help="serve editor diagnostics over stdio")
    args = parser.parse_args()

    if args.command == "lint":
        document = load(args.file, mode=args.mode)
        diagnostics = list(lint_workflow(document))
        if args.strict and _contains_adk_extension(document.model_dump(mode="json")):
            diagnostics.append(
                Diagnostic(
                    "adk-extension",
                    "strict mode: document contains ADK extensions",
                    "$",
                )
            )
        print(json.dumps([item.as_dict() for item in diagnostics], indent=2))
        return 1 if any(item.severity == "error" for item in diagnostics) else 0
    if args.command == "export":
        if args.format == "openapi":
            from openworkflow_adk.interop.openapi import export_openapi

            output = export_openapi(_load_document(args), base_url=args.base_url)
        else:
            raw = load_raw(args.file)
            pure = _to_pure_openworkflow(raw)
            import yaml

            output = yaml.safe_dump(pure, sort_keys=False)
        if args.output:
            args.output.write_text(output)
        else:
            print(output, end="")
        return 0
    if args.command == "plan":
        print(json.dumps(workflow_plan(_load_document(args)), indent=2))
        return 0
    if args.command == "graph":
        print(workflow_mermaid(_load_document(args)), end="")
        return 0
    if args.command == "test":
        cases = _load_test_cases(args.fixtures)
        reports = []
        document = load(args.file, mode=args.mode)
        for index, case in enumerate(cases):
            events = asyncio.run(
                run_workflow(
                    document,
                    case.get("input", {}),
                    mode=args.mode,
                    catalog_base_dir=str(args.catalog_base_dir or args.file.parent),
                )
            )
            outputs = [event.output for event in events if event.output is not None]
            for event in events:
                if event.actions:
                    outputs.extend((event.actions.state_delta or {}).values())
            expected = case.get("output")
            passed = expected is None or expected in outputs
            reports.append({"case": case.get("name", str(index)), "passed": passed})
        print(json.dumps(reports, indent=2))
        return 0 if all(item["passed"] for item in reports) else 1
    if args.command == "serve":
        from openworkflow_adk.server import serve as serve_app

        history_config = None
        if args.postgres_url:
            history_config = PostgresRunHistoryConfig(
                url=args.postgres_url,
                schema=args.schema,
                namespace_id=args.namespace,
            )
        serve_app(
            load(args.file, mode=args.mode),
            host=args.host,
            port=args.port,
            history_config=history_config,
        )
        return 0
    if args.command == "dashboard":
        from openworkflow_adk.server import serve as serve_app

        config = PostgresRunHistoryConfig(
            url=args.postgres_url,
            schema=args.schema,
            namespace_id=args.namespace,
        )
        serve_app(
            load(args.file, mode=args.mode),
            host=args.host,
            port=args.port,
            history_config=config,
        )
        return 0
    if args.command == "worker" and args.worker_command == "start":

        async def _run_worker() -> None:
            registry = WorkflowRegistry()
            for path in sorted(args.directory.glob("*.yaml")) + sorted(
                args.directory.glob("*.yml")
            ):
                registry.register(load(path, mode=args.mode))
            config = PostgresRunHistoryConfig(
                url=args.postgres_url,
                schema=args.schema,
                namespace_id=args.namespace,
            )
            history = PostgresRunHistory(config)
            await history.connect()
            worker = PostgresPollingWorker(
                registry=registry,
                history=history,
                max_concurrency=args.concurrency,
                poll_interval_seconds=args.poll_interval,
                lease_seconds=args.lease_seconds,
            )
            try:
                await worker.run_forever()
            except KeyboardInterrupt:
                await worker.stop()
            finally:
                await history.close()

        asyncio.run(_run_worker())
        return 0
    if args.command == "diagnostics-server":
        serve_stdio()
        return 0
    if args.command != "run":
        parser.print_help()
        return 0
    if args.env:
        _load_env_file(args.env)
    input_data = json.loads(args.input)
    if not isinstance(input_data, dict):
        parser.error("--input must be a JSON object")
    events = asyncio.run(
        run_workflow(
            load(args.file, mode=args.mode),
            input_data,
            mode=args.mode,
            catalog_base_dir=str(args.catalog_base_dir or args.file.parent),
        )
    )
    for event in events:
        if event.output is not None:
            print(json.dumps(event.output, default=str))
    return 0


def _load_env_file(path: Path) -> None:
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def _load_test_cases(path: Path) -> list[dict[str, object]]:
    source = path.read_text() if path.is_file() else "[]"
    if path.is_dir():
        cases: list[dict[str, object]] = []
        for fixture in sorted(path.glob("*.json")):
            value = json.loads(fixture.read_text())
            value["name"] = value.get("name", fixture.stem)
            cases.append(value)
        return cases
    value = json.loads(source)
    if isinstance(value, dict):
        return [value]
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("test fixtures must be a JSON object or array of objects")
    return value
