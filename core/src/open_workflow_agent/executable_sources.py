"""Private deployment-captured executable bytes; never a schema or importer.

This initial profile maps an exact literal HTTPS resource URI to trusted embedded
Python bytes. It performs no retrieval, filesystem lookup, compile, import or
execution. The original public source endpoint remains in the workflow; callers
bind the separately captured artifact to its original canonical task pointer.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from .errors import ConfigurationError, OwaError, UnsupportedWorkflowFeature, WorkflowExecutionError

SOURCE_PROFILE = "owa/executable-source/v1"
BINDING_PROFILE = "owa/executable-script-binding/v1"
REGISTRY_PROFILE = "owa/executable-source-registry/v1"
MAX_SOURCE_BYTES = 1_048_576
MAX_SOURCES = 32
MAX_TOTAL_SOURCE_BYTES = 16_777_216
MAX_SOURCE_VALUE_BYTES = 8_388_608
MAX_REGISTRY_VALUE_BYTES = 33_554_432
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}")
_DIGEST = re.compile(r"[a-f0-9]{64}")


def _configuration(reason: str) -> ConfigurationError:
    return ConfigurationError(
        "executable source declaration is invalid", details={"reason": reason}
    )


def _evidence() -> WorkflowExecutionError:
    return WorkflowExecutionError("executable source evidence is unavailable or inconsistent")


def _text(value: Any, *, limit: int) -> str:
    if type(value) is not str:
        raise _configuration("invalid_text")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise _configuration("invalid_utf8") from None
    if len(encoded) > limit:
        raise _configuration("text_bound")
    return value


def _uri(value: Any) -> str:
    uri = _text(value, limit=4096)
    if not uri or not uri.isascii() or any(ord(char) < 33 or ord(char) == 127 for char in uri):
        raise _configuration("invalid_source_uri")
    if any(char in uri for char in "{}") or re.search(r"%(?![0-9A-Fa-f]{2})", uri):
        raise _configuration("dynamic_or_invalid_source_uri")
    try:
        parsed = urlsplit(uri)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "?" in uri
            or "#" in uri
            or "\\" in uri
            or parsed.port is not None
            and not 1 <= parsed.port <= 65535
        ):
            raise ValueError
    except ValueError:
        raise _configuration("unsupported_source_uri") from None
    return uri


def _code(value: Any) -> bytes:
    code = _text(value, limit=MAX_SOURCE_BYTES)
    encoded = code.encode("utf-8", errors="strict")
    if "\x00" in code:
        raise _configuration("nul_script_source")
    return encoded


def _json(value: Any, *, limit: int) -> str:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        if len(encoded.encode("utf-8", errors="strict")) > limit:
            raise ValueError
        return encoded
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise _evidence() from None


@dataclass(frozen=True, slots=True)
class ExecutableSource:
    id: str
    version: str
    uri: str
    language: str
    code: str = field(repr=False)
    digest: str

    def __post_init__(self) -> None:
        if type(self.id) is not str or _ID.fullmatch(self.id) is None:
            raise _configuration("invalid_source_id")
        if type(self.version) is not str or _VERSION.fullmatch(self.version) is None:
            raise _configuration("invalid_source_version")
        _uri(self.uri)
        if type(self.language) is not str or self.language != "python":
            raise _configuration("unsupported_source_language")
        encoded = _code(self.code)
        if type(self.digest) is not str or _DIGEST.fullmatch(self.digest) is None:
            raise _configuration("invalid_source_digest")
        if hashlib.sha256(encoded).hexdigest() != self.digest:
            raise _configuration("source_digest_mismatch")

    @classmethod
    def capture(
        cls, *, id: str, version: str, uri: str, code: bytes, digest: str, language: str = "python"
    ) -> ExecutableSource:
        if type(code) is not bytes or len(code) > MAX_SOURCE_BYTES:
            raise _configuration("invalid_source_bytes")
        try:
            text = code.decode("utf-8", errors="strict")
        except UnicodeError:
            raise _configuration("invalid_utf8") from None
        return cls(id, version, uri, language, text, digest)

    def verified_bytes(self) -> bytes:
        self.__post_init__()
        return _code(self.code)

    def as_value(self) -> dict[str, Any]:
        self.verified_bytes()
        value = {
            "profile": SOURCE_PROFILE,
            "id": self.id,
            "version": self.version,
            "uri": self.uri,
            "language": self.language,
            "code": self.code,
            "digest": self.digest,
        }
        _json(value, limit=MAX_SOURCE_VALUE_BYTES)
        return value

    @classmethod
    def from_value(cls, value: Any) -> ExecutableSource:
        try:
            if (
                type(value) is not dict
                or set(value) != {"profile", "id", "version", "uri", "language", "code", "digest"}
                or value["profile"] != SOURCE_PROFILE
            ):
                raise ValueError
            _json(value, limit=MAX_SOURCE_VALUE_BYTES)
            source = cls(
                **{
                    name: value[name]
                    for name in ("id", "version", "uri", "language", "code", "digest")
                }
            )
            if source.as_value() != value:
                raise ValueError
            return source
        except (OwaError, ValueError, TypeError, KeyError, RecursionError):
            raise _evidence() from None


def source_uri(resource: Any) -> str:
    """Validate the finite official ExternalResource/Endpoint shape offline."""
    try:
        if (
            not isinstance(resource, Mapping)
            or set(resource) - {"name", "endpoint"}
            or "endpoint" not in resource
        ):
            raise ValueError
        if "name" in resource:
            _text(resource["name"], limit=256)
        endpoint = resource["endpoint"]
        if isinstance(endpoint, Mapping):
            if set(endpoint) != {"uri"}:
                raise ValueError
            endpoint = endpoint["uri"]
        return _uri(endpoint)
    except (OwaError, ValueError, TypeError, KeyError):
        raise UnsupportedWorkflowFeature(
            "executable source requires an exact declared literal HTTPS resource",
            details={"reason": "unsupported_executable_source_endpoint"},
        ) from None


def _reference(value: Any) -> str:
    reference = _text(value, limit=2048)
    if (
        not reference.startswith("/")
        or reference == "/"
        or any(ord(char) < 32 or ord(char) == 127 for char in reference)
        or any(not token for token in reference[1:].split("/"))
        or re.search(r"~(?![01])", reference)
    ):
        raise _configuration("invalid_source_task_reference")
    return reference


@dataclass(frozen=True, slots=True)
class ExecutableScriptBinding:
    reference: str
    source: ExecutableSource

    def __post_init__(self) -> None:
        _reference(self.reference)
        if type(self.source) is not ExecutableSource:
            raise _configuration("invalid_source_binding")
        self.source.verified_bytes()

    def as_value(self) -> dict[str, Any]:
        self.__post_init__()
        value = {
            "profile": BINDING_PROFILE,
            "reference": self.reference,
            "source": self.source.as_value(),
        }
        _json(value, limit=MAX_SOURCE_VALUE_BYTES)
        return value

    @classmethod
    def from_value(cls, value: Any) -> ExecutableScriptBinding:
        try:
            if (
                type(value) is not dict
                or set(value) != {"profile", "reference", "source"}
                or value["profile"] != BINDING_PROFILE
            ):
                raise ValueError
            _json(value, limit=MAX_SOURCE_VALUE_BYTES)
            binding = cls(value["reference"], ExecutableSource.from_value(value["source"]))
            if binding.as_value() != value:
                raise ValueError
            return binding
        except (OwaError, ValueError, TypeError, KeyError, RecursionError):
            raise _evidence() from None


@dataclass(frozen=True, slots=True)
class ExecutableSourceRegistry:
    sources: tuple[ExecutableSource, ...] = ()
    _by_uri: Mapping[str, ExecutableSource] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.sources) is not tuple or len(self.sources) > MAX_SOURCES:
            raise _configuration("source_registry_bound")
        by_uri: dict[str, ExecutableSource] = {}
        identities: set[tuple[str, str]] = set()
        size = 0
        for source in self.sources:
            if type(source) is not ExecutableSource:
                raise _configuration("invalid_registry_source")
            size += len(source.verified_bytes())
            identity = (source.id, source.version)
            if source.uri in by_uri or identity in identities:
                raise _configuration("duplicate_source_binding")
            if size > MAX_TOTAL_SOURCE_BYTES:
                raise _configuration("source_registry_byte_bound")
            by_uri[source.uri] = source
            identities.add(identity)
        ordered = tuple(
            sorted(self.sources, key=lambda source: (source.id, source.version, source.uri))
        )
        object.__setattr__(self, "sources", ordered)
        object.__setattr__(self, "_by_uri", MappingProxyType(by_uri))
        _json(
            {"profile": REGISTRY_PROFILE, "sources": [source.as_value() for source in ordered]},
            limit=MAX_REGISTRY_VALUE_BYTES,
        )

    def resolve(self, resource: Any, *, language: str) -> ExecutableSource:
        uri = source_uri(resource)
        source = self._by_uri.get(uri)
        if source is None or type(language) is not str or language != source.language:
            raise UnsupportedWorkflowFeature(
                "executable source is not declared for this runtime",
                details={"reason": "undeclared_executable_source"},
            )
        self.verify(source)
        return source

    def verify(self, source: ExecutableSource) -> None:
        try:
            if type(source) is not ExecutableSource:
                raise ValueError
            captured = ExecutableSource.from_value(source.as_value())
            if self._by_uri.get(captured.uri) != captured:
                raise ValueError
        except (OwaError, ValueError, TypeError):
            raise _evidence() from None

    def as_value(self) -> dict[str, Any]:
        self.__post_init__()
        return {
            "profile": REGISTRY_PROFILE,
            "sources": [source.as_value() for source in self.sources],
        }

    @classmethod
    def from_value(cls, value: Any) -> ExecutableSourceRegistry:
        try:
            if (
                type(value) is not dict
                or set(value) != {"profile", "sources"}
                or value["profile"] != REGISTRY_PROFILE
                or type(value["sources"]) is not list
                or len(value["sources"]) > MAX_SOURCES
            ):
                raise ValueError
            _json(value, limit=MAX_REGISTRY_VALUE_BYTES)
            registry = cls(
                tuple(ExecutableSource.from_value(source) for source in value["sources"])
            )
            if registry.as_value() != value:
                raise ValueError
            return registry
        except (OwaError, ValueError, TypeError, KeyError, RecursionError):
            raise _evidence() from None
