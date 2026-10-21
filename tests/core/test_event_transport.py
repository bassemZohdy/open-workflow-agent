"""Independent codec and actual committed-inbox acknowledgement boundaries."""

from __future__ import annotations

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import EventValidationError, WorkflowExecutionError
from open_workflow_agent.event_transport import CloudEventCodec, EmbeddedEventTransport
from open_workflow_agent.events import EventEnvelope
from open_workflow_agent.execution_authority import ExecutionAuthority, authority_scope
from open_workflow_agent.security import Principal
from open_workflow_agent.services import RuntimeServices


def event(**changes):
    return {
        "specversion": "1.0",
        "id": uuid4().hex,
        "source": "urn:contract:embedded-transport",
        "type": "contract.transport",
        "time": "2026-10-13T01:02:03.004Z",
        "subject": "same-correlation",
        "datacontenttype": "application/json",
        "traceparent": "00-unchanged",
        "counter": 3,
        "enabled": False,
        "data": None,
        **changes,
    }


@pytest.mark.parametrize(
    "data",
    [None, False, 0, "π", [], {"x": None}],
    ids=["null", "false", "zero", "unicode", "array", "object"],
)
def test_codec_keeps_exact_null_value_and_extension_types(data):
    codec = CloudEventCodec()
    value = event(data=data)
    original = codec.capture(value)
    assert original.as_dict() == value
    decoded = codec.decode(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    assert decoded == original
    output = decoded.as_dict()
    output["data"] = "different"
    assert decoded.as_dict() == value
    assert "π" not in repr(original) and "same-correlation" not in repr(original)


def test_optional_time_and_absent_data_remain_absent_in_codec_only():
    value = event()
    value.pop("time")
    value.pop("data")
    message = CloudEventCodec().capture(value)
    assert "time" not in message.as_dict() and "data" not in message.as_dict()
    with pytest.raises(EventValidationError):
        message.properties()


@pytest.mark.parametrize(
    "change",
    [
        {"specversion": "2.0"},
        {"id": ""},
        {"source": " "},
        {"subject": ""},
        {"source": "https://user:secret@example.test/event"},
        {"dataschema": "/relative"},
        {"time": "2026-02-30T01:02:03Z"},
        {"time": "2026-01-01T01:02:60Z"},
        {"time": "2026-01-01"},
        {"datacontenttype": "text/plain"},
        {"data_base64": "AA=="},
        {"Object": "invalid-name"},
        {"extra": {}},
        {"extra": None},
        {"extra": 2**31},
        {"extra": 1.5},
        {"extra": "\n"},
        {"data": (1, 2)},
        {"data": {1: "integer-key"}},
        {"data": float("nan")},
    ],
    ids=[
        "version",
        "empty-id",
        "source-space",
        "empty-subject",
        "credential-source",
        "relative-schema",
        "invalid-date",
        "leap-second",
        "date-only",
        "non-json",
        "binary",
        "uppercase-name",
        "object-extension",
        "null-extension",
        "large-extension",
        "float-extension",
        "control-extension",
        "tuple-data",
        "integer-key",
        "nan",
    ],
)
def test_unsupported_or_invalid_envelopes_fail_closed_without_raw_diagnostics(change):
    with pytest.raises(EventValidationError) as failure:
        CloudEventCodec().capture(event(**change))
    assert "secret" not in str(failure.value) and "invalid-name" not in str(failure.value)


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff",
        b'{"specversion":"1.0","specversion":"1.0"}',
        b"[]",
        b"{}",
        b'{"data":"\\ud800"}',
        b'{"data":NaN}',
    ],
    ids=["utf8", "duplicate", "array-root", "missing-context", "surrogate", "nan-wire"],
)
def test_raw_codec_rejects_malformed_or_ambiguous_json(raw):
    with pytest.raises(EventValidationError):
        CloudEventCodec().decode(raw)


def test_codec_byte_cap_and_reserved_context_cannot_be_overridden():
    codec = CloudEventCodec(max_event_bytes=256)
    with pytest.raises(EventValidationError):
        codec.capture(event(data="x" * 512))
    envelope = EventEnvelope(
        "original", "urn:source", "kind", "2026-01-01T00:00:00Z", extensions={"id": "foreign"}
    )
    with pytest.raises(EventValidationError):
        CloudEventCodec().encode(envelope)


def runtime(path, backend):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("actual PostgreSQL datasource required")
    services = RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=FakeModel(),
        database_root=None if datasource else path,
    )
    assert (
        services.effect_journal.connection.kind == services.invocations.connection.kind == backend
    )
    return services, EmbeddedEventTransport(services.event_store)


def saved(transport, message):
    value = message.as_dict()
    return transport.store.connection.execute(
        "SELECT sequence,envelope,digest FROM event_inbox WHERE source=? AND event_id=?",
        (value["source"], value["id"]),
    ).fetchall()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.asyncio
async def test_ack_reads_actual_committed_admission_from_another_connection(tmp_path, backend):
    services, transport = runtime(tmp_path, backend)
    other, reader = runtime(tmp_path, backend)
    message = transport.codec.capture(event())
    calls = []

    async def ack(admission):
        reader.verify(admission)
        assert len(saved(reader, message)) == 1
        calls.append(admission)

    try:
        admission = await transport.admit_and_ack(
            message.encoded, scope="anonymous", acknowledge=ack, timeout_seconds=5
        )
        assert calls == [admission]
        before = saved(transport, message)
        repeated = await transport.admit_and_ack(
            message.encoded, scope="anonymous", acknowledge=ack, timeout_seconds=5
        )
        assert repeated == admission and saved(transport, message) == before
        assert len(calls) == 2
    finally:
        other.close()
        services.close()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("failure", ["admission", "ack", "timeout"])
@pytest.mark.asyncio
async def test_failed_admission_never_acknowledges_and_lost_ack_keeps_known_row(
    tmp_path, backend, failure
):
    services, transport = runtime(tmp_path, backend)
    message = transport.codec.capture(event())
    calls = []

    def participant(connection, envelope):
        if failure == "admission":
            raise EventValidationError("intentional participant rollback")

    async def ack(admission):
        transport.verify(admission)
        calls.append(admission)
        if failure == "ack":
            raise OSError("lost acknowledgement")
        await asyncio.Event().wait()

    try:
        expected = (
            EventValidationError
            if failure == "admission"
            else (OSError if failure == "ack" else TimeoutError)
        )
        with pytest.raises(expected):
            await transport.admit_and_ack(
                message.encoded,
                scope="anonymous",
                acknowledge=ack,
                timeout_seconds=1 if failure == "timeout" else 5,
                participant=participant,
            )
        assert len(calls) == (0 if failure == "admission" else 1)
        assert len(saved(transport, message)) == (0 if failure == "admission" else 1)
        if calls:
            transport.verify(calls[0])
    finally:
        services.close()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("damage", ["inbox", "receipt", "selection", "digest"])
def test_missing_or_corrupt_half_cannot_be_reissued_or_acknowledged(tmp_path, backend, damage):
    services, transport = runtime(tmp_path, backend)
    message = transport.codec.capture(event())
    try:
        admission = transport.admit(message.encoded, scope="anonymous")
        db = transport.store.connection
        if damage == "inbox":
            db.execute("DELETE FROM event_inbox WHERE sequence=?", (admission.sequence,))
        elif damage == "selection":
            db.execute(
                "DELETE FROM event_consumer_selections WHERE inbox_sequence=?",
                (admission.sequence,),
            )
        elif damage == "receipt":
            db.execute(
                "DELETE FROM event_transport_admissions WHERE sequence=?", (admission.sequence,)
            )
        else:
            db.execute(
                "UPDATE event_transport_admissions SET receipt_digest='damaged' WHERE sequence=?",
                (admission.sequence,),
            )
        db.commit()
        before = saved(transport, message)
        with pytest.raises(WorkflowExecutionError):
            transport.verify(admission)
        with pytest.raises(WorkflowExecutionError):
            transport.admit(message.encoded, scope="anonymous")
        assert saved(transport, message) == before
    finally:
        services.close()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_payload_labels_cannot_select_another_principals_scope(tmp_path, backend):
    services, transport = runtime(tmp_path, backend)
    message = transport.codec.capture(event(user="bob", role="admin", scope="bob"))
    original = ExecutionAuthority.capture(Principal("alice"), security_profile="trusted-fixture")
    try:
        with authority_scope(original):
            with pytest.raises(EventValidationError):
                transport.admit(message.encoded, scope="bob")
            assert saved(transport, message) == []
            admission = transport.admit(message.encoded, scope="alice")
            transport.verify(admission)
            with pytest.raises(WorkflowExecutionError):
                transport.verify(replace(admission, scope="bob"))
    finally:
        services.close()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_two_independent_connections_commit_one_original_source_id(tmp_path, backend):
    first, left = runtime(tmp_path, backend)
    second, right = runtime(tmp_path, backend)
    message = left.codec.capture(event())
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(transport.admit, message.encoded, scope="anonymous")
                for transport in (left, right)
            ]
            results = [future.result(timeout=10) for future in futures]
        assert results[0] == results[1] and len(saved(left, message)) == 1
        left.verify(results[0])
        changed = left.codec.capture({**message.as_dict(), "data": "different"})
        with pytest.raises(EventValidationError):
            right.admit(changed.encoded, scope="anonymous")
        assert len(saved(left, message)) == 1
    finally:
        second.close()
        first.close()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_existing_legacy_inbox_cannot_acquire_a_retrofitted_transport_receipt(tmp_path, backend):
    services, transport = runtime(tmp_path, backend)
    message = transport.codec.capture(event())
    try:
        value = message.properties()
        transport.store.ingest(value, default_source=value["source"], scope="anonymous")
        before = saved(transport, message)
        with pytest.raises(WorkflowExecutionError):
            transport.admit(message.encoded, scope="anonymous")
        assert saved(transport, message) == before
        assert (
            transport.store.connection.execute(
                "SELECT 1 FROM event_transport_admissions WHERE source=? AND event_id=?",
                (value["source"], value["id"]),
            ).fetchone()
            is None
        )
    finally:
        services.close()
