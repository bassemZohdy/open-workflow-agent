"""Canonical developer command-line utilities."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from .errors import OwaError
from .workflow import compile_workflow, load_workflow


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="owa")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser(
        "validate", help="validate a workflow against the default OWA portable profile"
    )
    validate.add_argument("path", type=Path, help="Open Workflow YAML or JSON file")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the canonical OWA command-line interface."""
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    args = _parser().parse_args(argv)

    if args.command == "validate":
        try:
            workflow = load_workflow(args.path)
            compile_workflow(workflow)
        except OwaError as error:
            print(f"{error.code}: {error.message}", file=errors)
            return 1
        except UnicodeError:
            print("workflow_schema_error: workflow source must be valid UTF-8", file=errors)
            return 1
        print("Workflow is valid for the default OWA portable profile.", file=output)
        return 0

    # argparse's required subcommand makes this branch unreachable.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
