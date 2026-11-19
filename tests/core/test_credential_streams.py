"""Streaming credentials cannot escape through chunk boundaries or scope exit."""

from __future__ import annotations

import asyncio
import random
import traceback
from typing import cast

import pytest
from open_workflow_agent.credential_guard import capture_credentials, credential_scope
from open_workflow_agent.credential_streams import CredentialTextStream
from open_workflow_agent.errors import ToolError


def projected(values, chunks, **bounds):
    with credential_scope():
        capture_credentials(*values)
        stream = CredentialTextStream(**bounds)
        pieces = [stream.feed(chunk) for chunk in chunks]
        pieces.append(stream.finish())
        assert stream._pending == "" and stream._values == () and stream._guards == ()
        return "".join(pieces)


@pytest.mark.parametrize(
    "values,text,expected",
    [
        (("controlled-secret",), "before controlled-secret after", "before [REDACTED] after"),
        (("密钥🔑",), "before 密钥🔑 after", "before [REDACTED] after"),
        (("abc", "bcde"), "0abcde1", "0[REDACTED]1"),
        (("abc", "bcde"), "abcdeabcde", "[REDACTED]"),
        (("ab", "abcd"), "0abcde1ab2", "0[REDACTED]e1[REDACTED]2"),
        (("aaaa", "aaa"), "baaaaac", "b[REDACTED]c"),
        (("abcd",), "abcdxabc", "[REDACTED]xabc"),
        ((), "unaltered 🔑 text", "unaltered 🔑 text"),
    ],
)
def test_every_split_and_seeded_partition_preserves_safe_text(values, text, expected):
    for split in range(len(text) + 1):
        assert projected(values, [text[:split], text[split:]]) == expected
    rng = random.Random(391)
    for _ in range(128):
        chunks = []
        position = 0
        while position < len(text):
            if rng.randrange(3) == 0:
                chunks.append("")
            end = position + rng.randint(1, 5)
            chunks.append(text[position:end])
            position = end
        assert projected(values, chunks) == expected


def test_secret_prefix_is_never_published_before_later_chunk_completes_it():
    with credential_scope():
        capture_credentials("abcd")
        stream = CredentialTextStream()
        assert stream.feed("abc") == ""
        assert stream.feed("d") == "[REDACTED]"
        assert stream.feed("e") == ""
        assert stream.finish() == "e"


def test_projector_inherits_outer_and_inner_bindings_without_resolving_them_again():
    with credential_scope() as outer:
        outer.capture(["outer-value"])
        with credential_scope() as inner:
            inner.capture(["inner-value"])
            stream = CredentialTextStream()
            value = stream.feed("outer-val") + stream.feed("ue inner-value") + stream.finish()
            assert value == "[REDACTED] [REDACTED]"
        assert outer._stream_closers == [] and inner._stream_closers == []


@pytest.mark.parametrize(
    "values,text",
    [
        (("[REDACTED]",), "before [REDACTED] after"),
        (("X", "a[REDACTED]b"), "aXb"),
        (("[", "█", "■", "●", "�"), "before [█■●� after"),
    ],
)
def test_replacement_neither_contains_nor_assembles_a_captured_literal(values, text):
    output = projected(values, list(text))
    assert not any(value in output for value in values)


def test_raw_retention_is_bounded_even_when_a_long_literal_matches_many_small_chunks():
    secret = "x" * 65_536
    with credential_scope():
        capture_credentials(secret)
        stream = CredentialTextStream(max_chunks=4096)
        pieces = []
        for _ in range(128):
            pieces.append(stream.feed("x" * 1024))
            assert len(stream._pending) <= len(secret) - 1
            assert len(stream._mask) == len(stream._pending)
        pieces.append(stream.finish())
        assert "".join(pieces) == "[REDACTED]"


@pytest.mark.parametrize("change", ["outer", "inner", "exit", "close"])
def test_revoked_or_changed_scope_discards_raw_suffix_without_flushing(change):
    with credential_scope() as outer:
        capture_credentials("controlled-secret")
        with credential_scope() as inner:
            stream = CredentialTextStream()
            assert stream.feed("controlled-") == ""
            if change == "outer":
                outer.capture(["another-credential"])
            elif change == "inner":
                inner.capture(["another-credential"])
            elif change == "close":
                stream.close()
            else:
                inner._close()
            with pytest.raises(ToolError):
                stream.finish()
            assert stream._pending == "" and stream._values == () and not stream._mask


def test_scope_exit_clears_an_abandoned_stream_without_a_consumer_call():
    with credential_scope() as guard:
        capture_credentials("controlled-secret")
        stream = CredentialTextStream()
        stream.feed("controlled-")
    assert stream._pending == "" and stream._values == () and stream._guards == ()
    assert guard._stream_closers == []
    with pytest.raises(ToolError, match="closed"):
        stream.feed("secret")


@pytest.mark.asyncio
async def test_cancelled_producer_closes_private_state():
    ready = asyncio.Event()
    stream = None

    async def producer():
        nonlocal stream
        with credential_scope():
            capture_credentials("controlled-secret")
            stream = CredentialTextStream()
            stream.feed("controlled-")
            ready.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(producer())
    await ready.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream is not None and stream._pending == "" and stream._values == ()


@pytest.mark.parametrize("kind", ["chunk", "utf8", "surrogate", "total", "chunks", "output"])
def test_limits_fail_closed_with_no_raw_fragment_in_error(kind):
    secret = "controlled-" + "secret"
    with credential_scope():
        capture_credentials(secret)
        stream = CredentialTextStream(
            max_chunk_bytes=32, max_total_bytes=64, max_chunks=2 if kind == "chunks" else 8
        )
        if kind == "output":
            capture_credentials("x")
            stream.close()
            stream = CredentialTextStream(max_chunk_bytes=3, max_total_bytes=3)
        with pytest.raises(ToolError) as error:
            if kind == "chunk":
                stream.feed(secret * 3)
            elif kind == "utf8":
                stream.feed("🔑" * 9)
            elif kind == "surrogate":
                stream.feed(secret + "\ud800")
            elif kind == "total":
                stream.feed("a" * 32)
                stream.feed("b" * 32)
                stream.feed("c")
            elif kind == "chunks":
                stream.feed("")
                stream.feed("")
                stream.feed("")
            else:
                stream.feed("x")
                stream.finish()
        assert secret not in "".join(traceback.format_exception(error.value))
        assert stream._pending == "" and stream._values == ()
        with pytest.raises(ToolError, match="closed"):
            stream.finish()


def test_non_string_fragments_do_not_invoke_conversion_hooks():
    with credential_scope():
        stream = CredentialTextStream()
        with pytest.raises(ToolError, match="invalid"):
            stream.feed(cast(str, b"untrusted"))


@pytest.mark.parametrize(
    "bounds",
    [
        {"max_chunks": True},
        {"max_total_bytes": 0},
        {"max_chunk_bytes": 262_145},
        {"max_chunk_bytes": 2, "max_total_bytes": 1},
    ],
)
def test_invalid_budgets_reject_before_registering_a_stream(bounds):
    with credential_scope() as guard:
        with pytest.raises(ToolError):
            CredentialTextStream(**bounds)
        assert guard._stream_closers == []


def test_scope_requires_active_binding_and_limits_simultaneous_projectors():
    with pytest.raises(ToolError, match="active"):
        CredentialTextStream()
    with credential_scope() as guard:
        streams = [CredentialTextStream() for _ in range(32)]
        with pytest.raises(ToolError, match="capacity"):
            CredentialTextStream()
        streams[0].close()
        replacement = CredentialTextStream()
        replacement.finish()
    assert guard._stream_closers == []


def test_repeated_capture_of_the_same_binding_does_not_invalidate_the_stream():
    with credential_scope():
        capture_credentials("controlled-secret")
        stream = CredentialTextStream()
        first = stream.feed("controlled-")
        capture_credentials("controlled-secret")
        assert first + stream.feed("secret") + stream.finish() == "[REDACTED]"


def test_combined_scopes_cannot_exceed_the_existing_capture_capacity():
    with credential_scope() as outer:
        outer.capture([f"outer-credential-{index}" for index in range(256)])
        with credential_scope() as inner:
            inner.capture(["inner-credential"])
            with pytest.raises(ToolError, match="finite limits"):
                CredentialTextStream()
            assert outer._stream_closers == [] and inner._stream_closers == []
