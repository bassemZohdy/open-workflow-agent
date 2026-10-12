"""Admission-time, deployment-pinned JSON Schema resources and offline closure."""

from __future__ import annotations

import copy
import hashlib
import json
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from .config import ExternalCatalogConfig
from .errors import (
    OwaError,
    ToolError,
    UnsupportedWorkflowFeature,
    WorkflowSchemaError,
    WorkflowSemanticError,
)
from .protocols import HttpClient

MAX_SCHEMA_RESOURCES = 32
MAX_SCHEMA_CLOSURE_BYTES = 16_000_000


@dataclass(frozen=True, slots=True)
class SchemaResource:
    uri: str
    digest: str
    document_json: str
    size_bytes: int

    @property
    def document(self) -> Any:
        return json.loads(self.document_json)


@dataclass(slots=True)
class _CachedSchema:
    resource: SchemaResource
    fetched_at: float
    etag: str | None
    modified: str | None


def schema_registry(resources: tuple[SchemaResource, ...]) -> Registry[Any]:
    identities: dict[str, Any] = {}

    def register(uri: str, resource: Resource[Any]) -> None:
        if uri in identities and identities[uri] != resource.contents:
            raise WorkflowSemanticError(
                "conflicting schema resource identifiers", details={"reference": uri}
            )
        identities[uri] = resource.contents

    def walk(resource: Resource[Any], base: str) -> None:
        uri = urljoin(base, resource.id() or "")
        if resource.id():
            register(uri, resource)
        for child in resource.subresources():
            walk(child, uri)

    for item in resources:
        resource = DRAFT202012.create_resource(item.document)
        register(item.uri, resource)
        walk(resource, item.uri)
    return (
        Registry()
        .with_resources(
            (resource.uri, DRAFT202012.create_resource(resource.document)) for resource in resources
        )
        .crawl()
    )


def schema_specs(workflow: Mapping[str, Any]) -> Iterator[tuple[str, Mapping[str, Any]]]:
    """Walk actual definition slots; ordinary task payloads never become imports."""
    from .workflow import _task_scopes, _walk_tasks

    def data(definition: Mapping[str, Any], path: str) -> Iterator[tuple[str, Mapping[str, Any]]]:
        for key in ("input", "output", "export"):
            value = definition.get(key)
            schema = value.get("schema") if isinstance(value, Mapping) else None
            if isinstance(schema, Mapping):
                yield f"{path}/{key}/schema", schema

    def tasks(
        items: Any, path: str, indexed: bool = True
    ) -> Iterator[tuple[str, Mapping[str, Any]]]:
        for reference, definition in _walk_tasks(items, prefix=path, indexed=indexed):
            yield from data(definition, reference)
            iterator = definition.get("foreach") if "listen" in definition else None
            if isinstance(iterator, Mapping):
                yield from data(iterator, f"{reference}/foreach")
            for suffix, children in _task_scopes(definition):
                yield from tasks(
                    children, f"{reference}/{suffix}", not suffix.startswith("fork/branches/")
                )

    yield from data(workflow, "")
    yield from tasks(workflow.get("do", []), "/do")
    for name, definition in workflow.get("use", {}).get("functions", {}).items():
        yield from tasks([{name: definition}], "/use/functions", False)


def materialize_schema_resources(workflow: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(workflow))
    for _, schema in schema_specs(result):
        if "resource" in schema:
            uri = resource_uri(schema["resource"])
            mutable = cast(dict[str, Any], schema)
            mutable.pop("resource")
            mutable["document"] = {"$ref": uri}
    return result


def resource_uri(value: Any) -> str:
    from .external_catalog import _endpoint_uri

    endpoint = value.get("endpoint") if isinstance(value, Mapping) else None
    uri = _endpoint_uri(endpoint)
    if not isinstance(uri, str) or "${" in uri:
        raise UnsupportedWorkflowFeature("schema resource requires a literal endpoint URI")
    return uri


def schema_references(
    document: Any, registry: Registry[Any], reference: str, *, base: str = ""
) -> set[str]:
    """Find unresolved documents through schema resources, excluding const data."""
    if not isinstance(document, (Mapping, bool)):
        raise WorkflowSemanticError(
            "schema resource must be a JSON Schema object or boolean",
            details={"reference": reference},
        )
    try:
        Draft202012Validator.check_schema(document)
    except Exception:
        raise WorkflowSemanticError(
            "invalid data schema", details={"reference": reference}
        ) from None
    root = DRAFT202012.create_resource(document)
    root_base = urljoin(base, root.id() or "")
    current = registry.resolver(root_base) if base else registry.resolver_with_root(root)
    missing: set[str] = set()
    locations: dict[int, str] = {}

    def locate(value: Any, path: str) -> None:
        locations[id(value)] = path
        if isinstance(value, Mapping):
            for key, item in value.items():
                locate(item, f"{path}/{str(key).replace('~', '~0').replace('/', '~1')}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                locate(item, f"{path}/{index}")

    locate(document, reference)

    def walk(resource: Resource[Any], resolver: Any, current_base: str) -> None:
        value = resource.contents
        if not isinstance(value, Mapping):
            return
        path = locations[id(value)]
        if (
            value.get("$schema", "https://json-schema.org/draft/2020-12/schema").rstrip("#")
            != "https://json-schema.org/draft/2020-12/schema"
        ):
            raise UnsupportedWorkflowFeature(
                "unsupported data schema dialect", details={"reference": f"{path}/$schema"}
            )
        for key in ("$ref", "$dynamicRef"):
            target = value.get(key)
            if not isinstance(target, str):
                continue
            try:
                resolved = resolver.lookup(target).contents
            except Exception:
                uri, _ = urldefrag(urljoin(current_base, target))
                if target.startswith("#") or uri == urldefrag(current_base)[0] or uri in registry:
                    raise WorkflowSemanticError(
                        "unresolved or invalid inline schema reference",
                        details={"reference": f"{path}/{key}"},
                    ) from None
                missing.add(uri)
            else:
                if not isinstance(resolved, (Mapping, bool)):
                    raise WorkflowSemanticError(
                        "schema reference target is not a schema",
                        details={"reference": f"{path}/{key}"},
                    )
        for child in resource.subresources():
            walk(child, resolver.in_subresource(child), urljoin(current_base, child.id() or ""))

    walk(root, current, root_base)
    return missing


class SchemaResourceResolver:
    def __init__(
        self,
        policies: Mapping[str, ExternalCatalogConfig],
        client: Callable[[ExternalCatalogConfig, str], HttpClient],
    ) -> None:
        self.policies = dict(policies)
        self.client = client
        self._cache: dict[str, _CachedSchema] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.policies)

    def _policy(self, uri: str) -> ExternalCatalogConfig:
        from .external_catalog import _validate_endpoint

        parsed = urlparse(uri)
        if parsed.fragment:
            raise UnsupportedWorkflowFeature("schema document fetch URI cannot contain a fragment")
        matches = [policy for policy in self.policies.values() if uri in policy.allowed_endpoints]
        if len(matches) != 1:
            raise UnsupportedWorkflowFeature(
                "schema resource requires one exact deployment-approved endpoint"
            )
        policy = matches[0]
        _validate_endpoint(uri, policy)
        if uri not in policy.integrity_pins:
            raise UnsupportedWorkflowFeature(
                "schema resource requires an exact deployment SHA-256 pin"
            )
        return policy

    def verify(self, resources: tuple[SchemaResource, ...]) -> None:
        for resource in resources:
            policy = self._policy(resource.uri)
            if policy.integrity_pins[resource.uri] != resource.digest:
                raise ToolError("captured schema resource integrity policy has changed")

    async def _fetch(self, uri: str) -> SchemaResource:
        from .external_catalog import _validate_network_destination
        from .workflow import load_json_value

        policy = self._policy(uri)
        now = time.monotonic()
        cached = self._cache.get(uri)
        if cached is not None and now - cached.fetched_at > policy.max_cache_age_seconds:
            self._cache.pop(uri)
            cached = None
        if cached is not None and now - cached.fetched_at < policy.cache_ttl_seconds:
            self.verify((cached.resource,))
            return cached.resource
        headers = {}
        if cached is not None and policy.revalidate:
            if cached.etag:
                headers["If-None-Match"] = cached.etag
            if cached.modified:
                headers["If-Modified-Since"] = cached.modified
        try:
            base_client = self.client(policy, uri)
            client = HttpClient(
                timeout=policy.timeout_seconds,
                max_response_bytes=policy.max_response_bytes,
                verify_tls=True,
                follow_redirects=False,
                allowed_hosts=set(policy.allowed_hosts),
                authentication=base_client.authentication,
                transport=base_client.transport,
                destination_resolver=base_client.destination_resolver,
                client_cert=base_client.client_cert,
            )
            if client.transport is not None:
                await _validate_network_destination(uri, policy, client)
            response = await client.request(
                "GET",
                uri,
                headers=headers,
                request_timeout=policy.timeout_seconds,
                allow_not_modified=True,
                _return_response=True,
            )
        except OwaError as exc:
            raise ToolError(
                "schema resource resolution failed", details={"code": exc.code}
            ) from None
        except Exception:
            raise ToolError("schema resource resolution failed") from None
        if not isinstance(response, httpx.Response):
            raise ToolError("schema resource returned an invalid response")
        if response.status_code == 304:
            if cached is None:
                raise ToolError("schema resource returned an unexpected not-modified response")
            self.verify((cached.resource,))
            cached.fetched_at = now
            return cached.resource
        if not 200 <= response.status_code < 300:
            raise ToolError("schema resource returned an invalid status")
        raw = response.content
        digest = hashlib.sha256(raw).hexdigest()
        if digest != policy.integrity_pins[uri]:
            raise ToolError("schema resource integrity verification failed")
        try:
            document = load_json_value(raw.decode("utf-8"))
        except (UnicodeDecodeError, WorkflowSchemaError):
            raise WorkflowSchemaError("schema resource is not safe UTF-8 JSON/YAML") from None
        if not isinstance(document, (Mapping, bool)):
            raise WorkflowSemanticError("schema resource must be a JSON Schema object or boolean")
        resource = SchemaResource(
            uri,
            digest,
            json.dumps(
                document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            ),
            len(raw),
        )
        if uri not in self._cache and len(self._cache) >= policy.max_cache_entries:
            self._cache.pop(min(self._cache, key=lambda key: self._cache[key].fetched_at))
        self._cache[uri] = _CachedSchema(
            resource, now, response.headers.get("etag"), response.headers.get("last-modified")
        )
        return resource

    async def resolve(
        self, workflow: Mapping[str, Any], imports: tuple[Any, ...]
    ) -> tuple[SchemaResource, ...]:
        documents: list[tuple[str, Any]] = []
        roots: set[str] = set()
        sources = [
            workflow,
            *({"do": [{item.reference: item.execution_definition}]} for item in imports),
        ]
        for source in sources:
            for path, specification in schema_specs(source):
                if "resource" in specification:
                    uri = resource_uri(specification["resource"])
                    roots.add(urldefrag(uri)[0])
                    documents.append((path, {"$ref": uri}))
                else:
                    documents.append((path, specification.get("document")))
        captured: dict[str, SchemaResource] = {}
        pending = roots
        while True:
            for uri in sorted(pending):
                self._policy(uri)
            for uri in sorted(pending):
                if len(captured) >= MAX_SCHEMA_RESOURCES:
                    raise WorkflowSemanticError("schema resource closure exceeds document limit")
                captured[uri] = await self._fetch(uri)
                if (
                    sum(
                        max(item.size_bytes, len(item.document_json.encode("utf-8")))
                        for item in captured.values()
                    )
                    > MAX_SCHEMA_CLOSURE_BYTES
                ):
                    raise WorkflowSemanticError("schema resource closure exceeds byte limit")
            resources = tuple(captured[uri] for uri in sorted(captured))
            registry = schema_registry(resources)
            missing: set[str] = set()
            for path, document in documents:
                missing.update(schema_references(document, registry, path))
            for resource in resources:
                missing.update(
                    schema_references(resource.document, registry, resource.uri, base=resource.uri)
                )
            pending = missing - captured.keys()
            if not pending:
                self.verify(resources)
                return resources
