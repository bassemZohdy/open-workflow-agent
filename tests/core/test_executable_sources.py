"""Pure embedded source proof; no retrieval, import, compile or executable launch."""

from __future__ import annotations

import builtins
import hashlib
import json
import socket
import urllib.request
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest
from open_workflow_agent.errors import (
    ConfigurationError,
    UnsupportedWorkflowFeature,
    WorkflowExecutionError,
)
from open_workflow_agent.executable_sources import (
    BINDING_PROFILE,
    MAX_SOURCE_BYTES,
    MAX_SOURCES,
    MAX_TOTAL_SOURCE_BYTES,
    ExecutableScriptBinding,
    ExecutableSource,
    ExecutableSourceRegistry,
    source_uri,
)

URI = "https://sources.test/approved.py"
CODE = b"print('retained exact bytes')\r\n"


def capture(code=CODE, **changes):
    return ExecutableSource.capture(
        **{
            "id": "approved",
            "version": "1.0.0",
            "uri": URI,
            "code": code,
            "digest": hashlib.sha256(code).hexdigest(),
            **changes,
        }
    )


@pytest.mark.parametrize(
    "code", [b"", CODE, "# café\nprint('value')\n".encode(), b"\xef\xbb\xbfprint(1)\n"]
)
def test_source_exact_utf8_bytes_integrity_and_json_roundtrip(code):
    source = capture(code)
    assert source.verified_bytes() == code
    assert source.digest == hashlib.sha256(code).hexdigest()
    saved = source.as_value()
    assert set(saved) == {"profile", "id", "version", "uri", "language", "code", "digest"}
    assert ExecutableSource.from_value(json.loads(json.dumps(saved, ensure_ascii=False))) == source
    assert ExecutableSource.from_value(saved).verified_bytes() == code
    assert source.code not in repr(source) if source.code else "code=" not in repr(source)


def test_source_capture_registry_and_binding_do_not_open_import_compile_or_execute(monkeypatch):
    code = b"import missing_runtime_package\nraise AssertionError('must never execute')\n"
    digest = hashlib.sha256(code).hexdigest()

    def forbidden(*args, **kwargs):
        raise AssertionError("pure source capture performed external work")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        patch.setattr(builtins, "__import__", forbidden)
        patch.setattr(builtins, "compile", forbidden)
        patch.setattr(builtins, "exec", forbidden)
        patch.setattr(Path, "open", forbidden)
        patch.setattr(socket, "create_connection", forbidden)
        patch.setattr(urllib.request, "urlopen", forbidden)
        source = ExecutableSource.capture(
            id="approved", version="1", uri=URI, code=code, digest=digest
        )
        registry = ExecutableSourceRegistry((source,))
        resolved = registry.resolve({"endpoint": {"uri": URI}}, language="python")
        binding = ExecutableScriptBinding.from_value(
            ExecutableScriptBinding("/do/0/run", resolved).as_value()
        )
        registry.verify(binding.source)
    assert binding.source.verified_bytes() == code


@pytest.mark.parametrize(
    "change",
    [
        {"id": ""},
        {"id": "a/b"},
        {"id": "a" * 129},
        {"id": True},
        {"version": ""},
        {"version": "v/1"},
        {"version": "a" * 65},
        {"version": 1},
        {"language": "js"},
        {"language": "python3.13"},
        {"language": None},
        {"digest": ""},
        {"digest": "f" * 64},
        {"digest": "A" * 64},
        {"digest": False},
    ],
)
def test_invalid_deployment_identity_language_or_digest_rejects(change):
    with pytest.raises(ConfigurationError):
        capture(**change)


@pytest.mark.parametrize(
    "uri",
    [
        "http://sources.test/code.py",
        "file:///private/code.py",
        "/private/code.py",
        "../code.py",
        "${ .source }",
        "https://{host}/code.py",
        "https://sources.test/{code}.py",
        "https://owner:credential-literal@sources.test/code.py",
        "https://sources.test/code.py?token=credential-literal",
        "https://sources.test/code.py?",
        "https://sources.test/code.py#fragment",
        "https://sources.test/code.py#",
        "https://sources.test/code.py\n",
        "https://sources.test/code%ZZ.py",
        "https://sources.test:0/code.py",
        "https://sources.test:65536/code.py",
        "https://[broken/code.py",
        "https://sources.test/\\private.py",
        "https://sources.test/café.py",
    ],
)
def test_unsafe_dynamic_or_undeclared_uri_profiles_reject_with_sanitized_error(uri):
    with pytest.raises(ConfigurationError) as failure:
        capture(uri=uri)
    assert "credential-literal" not in json.dumps(failure.value.as_dict())


@pytest.mark.parametrize(
    "code",
    [
        b"\xff",
        b"\xc0\xaf",
        b"print(1)\x00",
        b"a" * (MAX_SOURCE_BYTES + 1),
        bytearray(CODE),
        memoryview(CODE),
        "print(1)",
    ],
)
def test_source_bytes_require_bounded_immutable_strict_utf8(code):
    with pytest.raises(ConfigurationError):
        ExecutableSource.capture(id="approved", version="1", uri=URI, code=code, digest="a" * 64)


def test_exact_source_byte_boundary_is_accepted_and_multibyte_size_is_measured_in_bytes():
    source = capture(b"#" * MAX_SOURCE_BYTES)
    assert len(source.verified_bytes()) == MAX_SOURCE_BYTES
    with pytest.raises(ConfigurationError):
        capture(("é" * (MAX_SOURCE_BYTES // 2 + 1)).encode())


@pytest.mark.parametrize(
    "field,value",
    [
        ("profile", "owa/executable-source/v2"),
        ("language", "js"),
        ("id", False),
        ("version", 1),
        ("code", ["print(1)"]),
        ("code", "\ud800"),
        ("code", "changed immutable source"),
        ("digest", "b" * 64),
        ("uri", "https://other.test/code.py"),
    ],
)
def test_saved_source_strict_fields_rehash_and_current_authority(field, value):
    original = capture()
    saved = original.as_value()
    saved[field] = value
    if field == "uri":
        # A different individually valid capture still cannot replace current
        # deployment authority for an original archived invocation.
        changed = ExecutableSource.from_value(saved)
        with pytest.raises(WorkflowExecutionError):
            ExecutableSourceRegistry((original,)).verify(changed)
    else:
        with pytest.raises(WorkflowExecutionError):
            ExecutableSource.from_value(saved)


@pytest.mark.parametrize("shape", [None, [], "{}", {"profile": "owa/executable-source/v1"}])
def test_saved_source_missing_or_non_object_shape_rejects(shape):
    with pytest.raises(WorkflowExecutionError):
        ExecutableSource.from_value(shape)


def test_saved_source_extras_and_mutated_frozen_evidence_reject():
    source = capture()
    with pytest.raises(WorkflowExecutionError):
        ExecutableSource.from_value({**source.as_value(), "credentials": "must-not-accept"})
    with pytest.raises(FrozenInstanceError):
        source.code = "mutable source"
    object.__setattr__(source, "code", "tampered bypassed frozen source")
    with pytest.raises(ConfigurationError):
        source.verified_bytes()


@pytest.mark.parametrize(
    "resource",
    [
        {"endpoint": URI},
        {"name": "workflow-alias", "endpoint": URI},
        {"name": "", "endpoint": URI},
        {"name": "display\nname", "endpoint": URI},
        {"endpoint": {"uri": URI}},
        {"name": "workflow-alias", "endpoint": {"uri": URI}},
    ],
)
def test_official_literal_source_endpoint_shapes_resolve_exact_capture(resource):
    source = capture()
    registry = ExecutableSourceRegistry((source,))
    assert source_uri(resource) == URI
    assert registry.resolve(resource, language="python") is source


@pytest.mark.parametrize(
    "resource",
    [
        URI,
        {"uri": URI},
        {},
        {"endpoint": "${ .uri }"},
        {"endpoint": {"uri": "${ .uri }"}},
        {"endpoint": {"uri": URI, "authentication": {"use": "named"}}},
        {"endpoint": {"uri": URI, "authentication": {"bearer": {"token": "credential-literal"}}}},
        {"endpoint": URI, "digest": "a" * 64},
        {"endpoint": URI, "code": "inline"},
        {"endpoint": {"uri": URI, "headers": {"Authorization": "credential-literal"}}},
        {"endpoint": URI, "name": False},
        {"endpoint": URI, "name": "a" * 257},
        {"endpoint": []},
    ],
)
def test_source_endpoint_dynamic_credentials_and_extra_public_fields_fail_closed(resource):
    with pytest.raises(UnsupportedWorkflowFeature) as failure:
        ExecutableSourceRegistry((capture(),)).resolve(resource, language="python")
    assert "credential-literal" not in json.dumps(failure.value.as_dict())


@pytest.mark.parametrize(
    "resource,language",
    [
        ({"endpoint": "https://other.test/approved.py"}, "python"),
        ({"endpoint": "https://sources.test/APPROVED.py"}, "python"),
        ({"endpoint": URI}, "js"),
        ({"endpoint": URI}, True),
    ],
)
def test_registry_never_discovers_similar_uri_or_different_runtime(resource, language):
    with pytest.raises(UnsupportedWorkflowFeature):
        ExecutableSourceRegistry((capture(),)).resolve(resource, language=language)


@pytest.mark.parametrize("duplicate", ["uri", "identity", "same"])
def test_registry_rejects_duplicate_or_ambiguous_bindings(duplicate):
    original = capture()
    other = (
        original
        if duplicate == "same"
        else capture(
            id="other" if duplicate == "uri" else original.id,
            uri=original.uri if duplicate == "uri" else "https://sources.test/other.py",
        )
    )
    with pytest.raises(ConfigurationError):
        ExecutableSourceRegistry((original, other))


def test_registry_multiple_revisions_are_selected_by_exact_uri_without_latest_lookup():
    first = capture()
    second = capture(version="2.0.0", uri="https://sources.test/v2/approved.py")
    registry = ExecutableSourceRegistry((second, first))
    assert registry.resolve({"endpoint": first.uri}, language="python") == first
    assert registry.resolve({"endpoint": second.uri}, language="python") == second
    assert registry.as_value() == ExecutableSourceRegistry((first, second)).as_value()
    assert (
        ExecutableSourceRegistry.from_value(json.loads(json.dumps(registry.as_value()))) == registry
    )


@pytest.mark.parametrize("damage", ["profile", "extra", "source-order", "tuple", "source-digest"])
def test_registry_saved_value_is_strict_canonical_and_verified(damage):
    registry = ExecutableSourceRegistry(
        (capture(), capture(id="zebra", uri="https://sources.test/z.py"))
    )
    saved = registry.as_value()
    if damage == "profile":
        saved["profile"] = "owa/executable-source-registry/v2"
    elif damage == "extra":
        saved["installer"] = "never"
    elif damage == "source-order":
        saved["sources"].reverse()
    elif damage == "tuple":
        saved["sources"] = tuple(saved["sources"])
    else:
        saved["sources"][0]["digest"] = "d" * 64
    with pytest.raises(WorkflowExecutionError):
        ExecutableSourceRegistry.from_value(saved)


def test_registry_count_and_total_bytes_are_bounded(monkeypatch):
    with pytest.raises(ConfigurationError):
        ExecutableSourceRegistry(
            tuple(
                capture(id=f"source{i}", uri=f"https://sources.test/{i}.py")
                for i in range(MAX_SOURCES + 1)
            )
        )
    import open_workflow_agent.executable_sources as module

    # Exercise the exact aggregate guard with small deterministic bytes; its
    # production constant independently pins the declared 16 MiB closure.
    assert MAX_TOTAL_SOURCE_BYTES == 16_777_216
    monkeypatch.setattr(module, "MAX_TOTAL_SOURCE_BYTES", len(CODE) * 2 - 1)
    with pytest.raises(ConfigurationError):
        ExecutableSourceRegistry(
            (capture(), capture(id="other", uri="https://sources.test/other.py"))
        )


def test_registry_is_default_empty_and_does_not_mutate_caller_captures():
    registry = ExecutableSourceRegistry()
    assert registry.sources == () and registry.as_value()["sources"] == []
    with pytest.raises(UnsupportedWorkflowFeature):
        registry.resolve({"endpoint": URI}, language="python")
    with pytest.raises(ConfigurationError):
        ExecutableSourceRegistry([capture()])
    original = capture()
    changed = replace(
        original, code="print('changed')", digest=hashlib.sha256(b"print('changed')").hexdigest()
    )
    with pytest.raises(WorkflowExecutionError):
        ExecutableSourceRegistry((original,)).verify(changed)


@pytest.mark.parametrize(
    "reference",
    [
        "/do/0/run",
        "/use/functions/runScript",
        "/do/0/fork/fork/branches/1/branch",
        "/use/functions/nested/do/0/name~1with~0escape",
    ],
)
def test_script_binding_retains_original_canonical_source_pointer(reference):
    binding = ExecutableScriptBinding(reference, capture())
    assert binding.as_value()["profile"] == BINDING_PROFILE
    assert ExecutableScriptBinding.from_value(json.loads(json.dumps(binding.as_value()))) == binding
    assert binding.reference == reference


@pytest.mark.parametrize(
    "reference",
    [
        "",
        "/",
        "do/0/run",
        "#/do/0/run",
        "/do//run",
        "/do/0/name~2escape",
        "/do/0/name~",
        "/do/0/name\n",
        "/" + "a" * 2048,
        False,
    ],
)
def test_script_binding_rejects_invalid_pointer_and_noncanonical_escape(reference):
    with pytest.raises(ConfigurationError):
        ExecutableScriptBinding(reference, capture())


@pytest.mark.parametrize(
    "damage", ["profile", "extra", "source-type", "source-digest", "reference"]
)
def test_saved_script_binding_revalidates_capture_and_exact_fields(damage):
    saved = ExecutableScriptBinding("/do/0/run", capture()).as_value()
    if damage == "profile":
        saved["profile"] = "owa/executable-script-binding/v2"
    elif damage == "extra":
        saved["execution_reference"] = "hidden-plan-reference"
    elif damage == "source-type":
        saved["source"] = CODE
    elif damage == "source-digest":
        saved["source"]["digest"] = "e" * 64
    else:
        saved["reference"] = "/do/0/name~2invalid"
    with pytest.raises(WorkflowExecutionError):
        ExecutableScriptBinding.from_value(saved)
