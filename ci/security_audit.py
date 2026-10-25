"""Enforce the dependency audit policy against pinned pip-audit JSON reports."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


class AuditReportError(Exception):
    """The scanner did not provide a complete, usable report."""


@dataclass(frozen=True, slots=True)
class Advisory:
    dependency: str
    version: str
    identifier: str
    fix_versions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuditResult:
    dependencies: int
    fixable: tuple[Advisory, ...]
    unfixable: tuple[Advisory, ...]


def _token(value: object, pattern: str) -> str:
    if not isinstance(value, str) or len(value) > 1024 or not re.fullmatch(pattern, value):
        raise AuditReportError("dependency audit contains an invalid identifier or version")
    return value


def evaluate_report(report: object, auditor_status: int) -> AuditResult:
    """Read the dependencies[].vulns[] shape emitted by pip-audit 2.7.3.

    A scanner failure, skipped dependency or schema mismatch cannot be accepted
    as a report with no fixable advisories. Unfixable advisories remain visible
    warnings under the existing policy; fixable advisories fail the gate.
    """

    if type(auditor_status) is not int or auditor_status not in {0, 1}:
        raise AuditReportError("dependency auditing did not complete")
    if not isinstance(report, dict) or set(report) != {"dependencies", "fixes"}:
        raise AuditReportError("dependency audit report has an unsupported schema")
    dependencies = report["dependencies"]
    if not isinstance(dependencies, list) or not dependencies or report["fixes"] != []:
        raise AuditReportError("dependency audit report is incomplete or has unexpected fixes")
    seen: set[str] = set()
    fixable: list[Advisory] = []
    unfixable: list[Advisory] = []
    for dependency in dependencies:
        if not isinstance(dependency, dict) or set(dependency) != {"name", "version", "vulns"}:
            raise AuditReportError("dependency audit includes an incomplete or skipped dependency")
        name = _token(dependency["name"], r"[A-Za-z0-9][A-Za-z0-9._-]*")
        version = _token(dependency["version"], r"[A-Za-z0-9][A-Za-z0-9.!+_-]*")
        canonical_name = re.sub(r"[._-]+", "-", name).lower()
        if canonical_name in seen:
            raise AuditReportError("dependency audit includes duplicate dependencies")
        seen.add(canonical_name)
        vulnerabilities = dependency["vulns"]
        if not isinstance(vulnerabilities, list):
            raise AuditReportError("dependency audit includes invalid vulnerability records")
        identifiers: set[str] = set()
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict) or not {"id", "fix_versions"} <= set(
                vulnerability
            ):
                raise AuditReportError("dependency audit includes incomplete vulnerability records")
            identifier = _token(vulnerability["id"], r"[A-Za-z0-9][A-Za-z0-9._:-]*")
            if identifier in identifiers:
                raise AuditReportError("dependency audit includes duplicate advisories")
            identifiers.add(identifier)
            versions = vulnerability["fix_versions"]
            if not isinstance(versions, list):
                raise AuditReportError("dependency audit includes invalid fix versions")
            fixes = tuple(_token(value, r"[A-Za-z0-9][A-Za-z0-9.!+_-]*") for value in versions)
            advisory = Advisory(name, version, identifier, fixes)
            (fixable if fixes else unfixable).append(advisory)
    if bool(fixable or unfixable) != (auditor_status == 1):
        raise AuditReportError("dependency audit report disagrees with the scanner exit status")
    return AuditResult(len(dependencies), tuple(fixable), tuple(unfixable))


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AuditReportError("dependency audit contains duplicate JSON fields")
        result[key] = value
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--auditor-status", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if args.report.stat().st_size > 20_000_000:
            raise AuditReportError("dependency audit report exceeds the size limit")
        report = json.loads(
            args.report.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs
        )
        result = evaluate_report(report, args.auditor_status)
    except (AuditReportError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        message = str(exc) if isinstance(exc, AuditReportError) else "report could not be read"
        print(f"FAIL: {message}", file=sys.stderr)
        return 1
    print(f"Audited {result.dependencies} dependencies")
    for advisory in (*result.fixable, *result.unfixable):
        fixes = ",".join(advisory.fix_versions) if advisory.fix_versions else "none"
        print(f"{advisory.dependency}=={advisory.version}: {advisory.identifier} fix={fixes}")
    if result.unfixable:
        print(f"WARNING: {len(result.unfixable)} advisories have no fix available yet")
    if result.fixable:
        print(f"FAIL: {len(result.fixable)} fixable advisories found", file=sys.stderr)
        return 1
    print("No fixable advisories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
