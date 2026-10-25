"""Capture/compare finite native literal outcomes; never a full conformance claim."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import importlib.metadata
import json
import logging
import os
import platform
import re
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from open_workflow_agent.workflow import WorkflowPlan

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = (
    "tests/fixtures/javascript-literal-execution.json",
    "tests/fixtures/javascript-literal-faults.json",
)
ENGINES = frozenset({"adk", "langgraph", "langgraph-js"})
SCHEMA = "resources/open-workflow/1.0.3/workflow.yaml"
FORMAT = "owa/literal-outcomes/v1"
MAX_REPORT_BYTES = 4_194_304
_SHA = re.compile(r"[0-9a-f]{40}\Z")


class ParityError(ValueError):
    """Fixed diagnostic; never include untrusted artifact values."""


def fixture_hashes() -> dict[str, str]:
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FIXTURES}


def sdk_versions() -> dict[str, str]:
    versions = {}
    for engine, sdk in (("adk", "google-adk"), ("langgraph", "langgraph")):
        project = tomllib.loads(
            (ROOT / f"engines/{engine}/pyproject.toml").read_text(encoding="utf-8")
        )["project"]
        dependencies = project["dependencies"] + project["optional-dependencies"].get("native", [])
        matches = [
            value.split("==", 1)[1] for value in dependencies if value.startswith(sdk + "==")
        ]
        if len(matches) != 1:
            raise ParityError("literal SDK pin is ambiguous")
        versions[engine] = matches[0]
    package = json.loads((ROOT / "engines/langgraph-js/package.json").read_text(encoding="utf-8"))
    versions["langgraph-js"] = package["dependencies"]["@langchain/langgraph"]
    return versions


def expected_records() -> list[dict[str, Any]]:
    records = []
    for index, filename in enumerate(FIXTURES):
        for fixture in json.loads((ROOT / filename).read_text(encoding="utf-8")):
            status = "completed" if index == 0 else "faulted"
            field = "output" if index == 0 else "error"
            records.append({"id": fixture["id"], "status": status, field: fixture["expected"]})
    if len({record["id"] for record in records}) != len(records):
        raise ParityError("literal fixture identity is ambiguous")
    return sorted(records, key=lambda record: record["id"])


def validate_literal_plan(plan: WorkflowPlan) -> None:
    """Development capture must not turn fixture edits into protocol/model I/O."""

    def literal(value: Any) -> bool:
        if isinstance(value, str):
            return "${" not in value
        if isinstance(value, list):
            return all(literal(item) for item in value)
        if isinstance(value, dict):
            return all(literal(item) for item in value.values())
        return True

    tasks = [task for scope in plan.scopes for task in scope.tasks]
    if len(tasks) > 256:
        raise ParityError("literal capture task count exceeds its profile")
    for task in tasks:
        body = task.definition
        if task.kind not in {"set", "do", "raise"} or set(body) - {
            "set",
            "do",
            "raise",
            "then",
            "metadata",
        }:
            raise ParityError("literal capture contains an unsupported operation")
        if task.kind != "do" and not literal(body[task.kind]):
            raise ParityError("literal capture cannot evaluate expressions")


def envelope(
    engine: str, source: str, sdk: str, runtime: str, records: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "source_commit": source,
        "engine": engine,
        "sdk_version": sdk,
        "runtime_version": runtime,
        "native": True,
        "fixture_sha256": fixture_hashes(),
        "schema_sha256": hashlib.sha256((ROOT / SCHEMA).read_bytes()).hexdigest(),
        "records": sorted(records, key=lambda record: record["id"]),
    }


def validate_report(value: object, source: str) -> dict[str, Any]:
    fields = {
        "format",
        "source_commit",
        "engine",
        "sdk_version",
        "runtime_version",
        "native",
        "fixture_sha256",
        "schema_sha256",
        "records",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ParityError("literal outcome report fields are invalid")
    engine = value["engine"]
    if not isinstance(engine, str) or engine not in ENGINES or value["native"] is not True:
        raise ParityError("literal outcome native engine evidence is invalid")
    if not _SHA.fullmatch(source) or value["source_commit"] != source or value["format"] != FORMAT:
        raise ParityError("literal outcome source or format does not match")
    if value["sdk_version"] != sdk_versions()[engine]:
        raise ParityError("literal outcome SDK does not match its source pin")
    runtime = value["runtime_version"]
    if not isinstance(runtime, str) or not re.fullmatch(r"\d+\.\d+\.\d+", runtime):
        raise ParityError("literal outcome runtime version is invalid")
    if not runtime.startswith("24." if engine == "langgraph-js" else "3.12."):
        raise ParityError("literal outcome runtime is outside the selected matrix")
    if (
        value["fixture_sha256"] != fixture_hashes()
        or value["schema_sha256"] != hashlib.sha256((ROOT / SCHEMA).read_bytes()).hexdigest()
    ):
        raise ParityError("literal outcome fixture or schema source differs")
    records = value["records"]
    expected = expected_records()
    if not isinstance(records, list) or len(records) != len(expected):
        raise ParityError("literal outcome case coverage is incomplete")
    # Compare types as well as values: Python bool/int equality must not hide a
    # JSON type difference. Canonical JSON also rejects non-finite numbers.
    try:
        canonical = json.dumps(records, ensure_ascii=False, allow_nan=False, sort_keys=True)
        oracle = json.dumps(expected, ensure_ascii=False, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError):
        raise ParityError("literal outcome records are not finite JSON") from None
    if canonical != oracle:
        raise ParityError("literal outcome records differ from the complete shared fixtures")
    return value


def aggregate(values: list[object], source: str) -> dict[str, Any]:
    if len(values) != len(ENGINES):
        raise ParityError("all three literal native reports are required")
    reports = [validate_report(value, source) for value in values]
    if {report["engine"] for report in reports} != ENGINES:
        raise ParityError("literal native engine set is incomplete or duplicated")
    return {
        "format": "owa/literal-parity/v1",
        "source_commit": source,
        "engines": sorted(ENGINES),
        "sdk_versions": sdk_versions(),
        "fixture_sha256": fixture_hashes(),
        "case_count": len(expected_records()),
        "comparison": "exact_shared_success_and_fault_json",
        "outcome_parity": True,
        "public_identity_parity": False,
        "durability_parity": False,
        "full_conformance": False,
    }


async def capture(engine_name: str, source: str) -> dict[str, Any]:
    from open_workflow_agent.catalog import FakeModel
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.services import RuntimeServices
    from open_workflow_agent.workflow import compile_workflow

    if engine_name not in {"adk", "langgraph"} or not _SHA.fullmatch(source):
        raise ParityError("literal native capture selection is invalid")
    actual = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        env={
            name: os.environ[name]
            for name in ("PATH", "SYSTEMROOT", "WINDIR")
            if name in os.environ
        },
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=True,
        timeout=5,
    ).stdout.strip()
    if actual != source:
        raise ParityError("literal capture source does not match its checkout")
    subprocess.run(
        ["git", "-c", "core.autocrlf=true", "diff", "--quiet", "HEAD", "--"],
        cwd=ROOT,
        env={
            name: os.environ[name]
            for name in ("PATH", "SYSTEMROOT", "WINDIR")
            if name in os.environ
        },
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=True,
        timeout=5,
    )
    untracked = subprocess.run(
        [
            "git",
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            "ci",
            "core",
            "engines",
            "resources",
            "runtime-catalog",
            "tests",
            ".github",
        ],
        cwd=ROOT,
        env={
            name: os.environ[name]
            for name in ("PATH", "SYSTEMROOT", "WINDIR")
            if name in os.environ
        },
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=True,
        timeout=5,
    ).stdout
    if untracked:
        raise ParityError("literal capture has untracked runtime or evidence inputs")
    module_name, class_name, sdk_name = (
        ("open_workflow_agent_adk", "AdkWorkflowEngine", "google-adk")
        if engine_name == "adk"
        else ("open_workflow_agent_langgraph", "LangGraphWorkflowEngine", "langgraph")
    )
    factory = getattr(importlib.import_module(module_name), class_name)
    records = []
    with tempfile.TemporaryDirectory(prefix="owa-literal-outcomes-") as root:
        for index, filename in enumerate(FIXTURES):
            for fixture in json.loads((ROOT / filename).read_text(encoding="utf-8")):
                if not isinstance(fixture["id"], str) or not re.fullmatch(
                    r"[a-z][a-z0-9-]{0,127}", fixture["id"]
                ):
                    raise ParityError("literal capture fixture identity is invalid")
                plan = compile_workflow(
                    {
                        "document": {
                            "dsl": "1.0.3",
                            "namespace": "tests",
                            "name": fixture["id"],
                            "version": "1.0.0",
                        },
                        "do": fixture["tasks"],
                    }
                )
                validate_literal_plan(plan)
                model = FakeModel()
                services = RuntimeServices(
                    RuntimeConfig(), model=model, database_root=Path(root) / fixture["id"]
                )
                engine = factory()
                available = (
                    engine.native.available
                    if engine_name == "adk"
                    else engine.native.task_nodes_available
                )
                if not available:
                    services.close()
                    raise ParityError("literal capture requires the actual native SDK")
                try:
                    await engine.initialize(services)
                    await services.recovery_definitions.prepare_profile(engine_name)
                    handle = services.invocations.create(
                        engine=engine_name,
                        session_id=None,
                        user_id=None,
                        workflow_name=plan.name,
                        workflow_version=plan.version,
                        workflow_fingerprint=plan.fingerprint,
                    )
                    logger = logging.getLogger("google_adk.google.adk.workflow._node_runner")
                    previous_level = logger.level
                    if index == 1 and engine_name == "adk":
                        logger.setLevel(logging.CRITICAL)
                    try:
                        result = await engine.invoke(
                            plan,
                            handle,
                            fixture["input"] if index == 0 else {"private": "not-error-data"},
                        )
                    finally:
                        logger.setLevel(previous_level)
                    field = "output" if result.status == "completed" else "error"
                    records.append(
                        {
                            "id": fixture["id"],
                            "status": result.status,
                            field: getattr(result, field),
                        }
                    )
                    if model.calls:
                        raise ParityError(
                            "literal capture dispatched an unexpected model operation"
                        )
                finally:
                    try:
                        await engine.shutdown()
                    finally:
                        services.close()
    return envelope(
        engine_name,
        source,
        importlib.metadata.version(sdk_name),
        platform.python_version(),
        records,
    )


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        if key in value:
            raise ParityError("literal outcome report contains duplicate fields")
        value[key] = item
    return value


def read_report(path: Path) -> object:
    if not 0 < path.stat().st_size <= MAX_REPORT_BYTES:
        raise ParityError("literal outcome report exceeds its bound")
    with path.open("rb") as stream:
        data = stream.read(MAX_REPORT_BYTES + 1)
    if len(data) > MAX_REPORT_BYTES:
        raise ParityError("literal outcome report exceeds its bound")
    return json.loads(data, object_pairs_hook=_pairs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("capture", "aggregate"):
        command = subcommands.add_parser(name)
        command.add_argument("--source-commit", required=True)
        command.add_argument("--output", type=Path, required=True)
        if name == "capture":
            command.add_argument("--engine", choices=["adk", "langgraph"], required=True)
        else:
            command.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "capture":
            value = asyncio.run(capture(args.engine, args.source_commit))
            validate_report(value, args.source_commit)
        else:
            paths = sorted(args.directory.glob("literal-outcomes-*.json"))
            if len(paths) != len(ENGINES):
                raise ParityError("all literal outcome files are required")
            value = aggregate([read_report(path) for path in paths], args.source_commit)
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)
        if len(payload.encode("utf-8")) > MAX_REPORT_BYTES:
            raise ParityError("literal outcome result exceeds its bound")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    except Exception:
        parser.exit(2, "literal outcome evidence rejected\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
