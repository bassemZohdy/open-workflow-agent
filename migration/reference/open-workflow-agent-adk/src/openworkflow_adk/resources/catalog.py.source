"""External OpenWorkflow function catalogs."""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

import yaml

from openworkflow_adk.models import OpenWorkflowDocument, Task, TaskItem
from openworkflow_adk.security.auth import resolve_authentication
from openworkflow_adk.security.security import guarded_async_client, validate_egress

__all__ = ["CatalogFunctionRegistry", "resolve_catalog_functions"]


_CATALOG_REFERENCE = re.compile(r"^(?P<name>[^:@/]+):(?P<version>[^@/]+)@(?P<catalog>[^@/]+)$")


def _catalog_reference(value: Any) -> tuple[str, str, str] | None:
    if not isinstance(value, str):
        return None
    match = _CATALOG_REFERENCE.fullmatch(value)
    if match is None:
        return None
    return match.group("name"), match.group("version"), match.group("catalog")


class CatalogFunctionRegistry:
    """Fetch, validate, and cache versioned functions from OpenWorkflow catalogs."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, dict[str, Any]]] = {}

    async def load(
        self,
        document: OpenWorkflowDocument,
        function_name: str,
        version: str,
        catalog_name: str,
        *,
        base_dir: str | Path | None = None,
        environ: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Load one ``functions/<name>/<version>/function.yaml`` resource."""
        catalog = document.use.catalogs.get(catalog_name)
        if catalog is None:
            raise KeyError(f"catalog {catalog_name!r} is not defined in use.catalogs")
        source, authentication = self._source(catalog.endpoint, function_name, version, base_dir)
        if source.startswith(("http://", "https://")):
            validate_egress(source, environ)
            auth, headers = resolve_authentication(
                authentication, document.use.authentications, environ
            )
            async with guarded_async_client(environ, follow_redirects=False) as client:
                response = await client.get(source, headers=headers, auth=auth)
                response.raise_for_status()
                content = response.content
        else:
            content = Path(source).read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        cached = self._cache.get(source)
        if cached is not None and cached[0] == digest:
            return deepcopy(cached[1])
        try:
            text = content.decode()
            raw = json.loads(text) if source.lower().endswith(".json") else yaml.safe_load(text)
        except (UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError) as error:
            raise ValueError(f"catalog function {source!r} is not valid YAML/JSON") from error
        if not isinstance(raw, dict):
            raise ValueError(f"catalog function {source!r} must contain a task object")
        # Catalog functions are task documents rather than complete workflows.
        # Reuse the same task validation and exec-expression hardening as load().
        from openworkflow_adk.loader import _exec_expression_errors

        errors = _exec_expression_errors(raw, "$catalog")
        if errors:
            raise ValueError("catalog function contains unsafe exec expressions: " + str(errors))
        try:
            task = Task.model_validate(raw)
        except Exception as error:
            raise ValueError(f"invalid catalog function {source!r}: {error}") from error
        result = task.model_dump(by_alias=True, exclude_none=True)
        self._cache[source] = (digest, result)
        return deepcopy(result)

    @staticmethod
    def _source(
        endpoint: Any,
        function_name: str,
        version: str,
        base_dir: str | Path | None,
    ) -> tuple[str, Any]:
        authentication = None
        if isinstance(endpoint, dict):
            authentication = endpoint.get("authentication")
            endpoint = endpoint.get("uri")
        if not isinstance(endpoint, str) or not endpoint:
            raise ValueError("catalog endpoint requires a URI")
        resource_path = "/functions/{}/{}/function.yaml".format(
            quote(function_name, safe=""), quote(version, safe="")
        )
        parsed = urlparse(endpoint)
        if parsed.scheme in {"http", "https"}:
            return CatalogFunctionRegistry._remote_source(endpoint, resource_path), authentication
        if parsed.scheme == "file":
            path = Path(unquote(parsed.path))
        else:
            path = Path(endpoint)
            if not path.is_absolute() and base_dir is not None:
                path = Path(base_dir) / path
        root = Path(base_dir or os.environ.get("WORKFLOW_RESOURCE_BASE_DIR", Path.cwd())).resolve()
        path = path.resolve()
        try:
            path.relative_to(root)
        except ValueError as error:
            raise ValueError(
                f"catalog root {endpoint!r} escapes the configured resource base"
            ) from error
        return str(path / resource_path.lstrip("/")), authentication

    @staticmethod
    def _remote_source(endpoint: str, resource_path: str) -> str:
        """Resolve repository browsing URLs to raw catalog documents."""
        parsed = urlparse(endpoint)
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if parsed.netloc == "github.com" and len(parts) >= 2:
            owner, repository = parts[:2]
            if len(parts) >= 4 and parts[2] in {"tree", "blob"}:
                ref = parts[3:]
            else:
                ref = ["main"]
            return (
                f"https://raw.githubusercontent.com/{quote(owner)}/{quote(repository)}"
                f"/refs/heads/{quote('/'.join(ref), safe='/')}"
                f"{resource_path}"
            )
        if parsed.netloc == "gitlab.com" and "-" in parts:
            marker = parts.index("-")
            if marker + 2 < len(parts) and parts[marker + 1] in {"tree", "blob"}:
                prefix = "/".join(quote(part) for part in parts[:marker])
                ref = quote("/".join(parts[marker + 2 :]), safe="/")
                return f"https://gitlab.com/{prefix}/-/raw/{ref}{resource_path}"
        return endpoint.rstrip("/") + resource_path


def _iter_catalog_calls(items: list[TaskItem] | list[dict[str, Any]]) -> set[str]:
    calls: set[str] = set()
    for raw_item in items:
        item = raw_item if isinstance(raw_item, TaskItem) else TaskItem.model_validate(raw_item)
        task = item.task
        if _catalog_reference(task.call) is not None:
            calls.add(task.call or "")
        calls.update(_iter_catalog_calls(task.do or []))
        calls.update(_iter_catalog_calls(task.try_ or []))
        if isinstance(task.catch, dict):
            calls.update(_iter_catalog_calls(task.catch.get("do", []) or []))
        if isinstance(task.fork, dict):
            calls.update(_iter_catalog_calls(task.fork.get("branches", []) or []))
        for case in task.switch or []:
            if isinstance(case, dict):
                config = next(iter(case.values()), {})
                if isinstance(config, dict):
                    calls.update(_iter_catalog_calls(config.get("do", []) or []))
    return calls


async def resolve_catalog_functions(
    document: OpenWorkflowDocument,
    registry: CatalogFunctionRegistry | None = None,
    *,
    base_dir: str | Path | None = None,
    environ: dict[str, str] | None = None,
) -> OpenWorkflowDocument:
    """Resolve all qualified catalog calls before the workflow graph is built."""
    registry = registry or CatalogFunctionRegistry()
    functions = dict(document.use.functions)
    pending = _iter_catalog_calls(document.do)
    while pending:
        reference = pending.pop()
        if reference in functions:
            continue
        parsed = _catalog_reference(reference)
        if parsed is None:
            continue
        function_name, version, catalog_name = parsed
        functions[reference] = await registry.load(
            document,
            function_name,
            version,
            catalog_name,
            base_dir=base_dir,
            environ=environ,
        )
        pending.update(_iter_catalog_calls([{"catalog_function": functions[reference]}]))
    if functions == document.use.functions:
        return document
    return document.model_copy(
        update={"use": document.use.model_copy(update={"functions": functions})}
    )
