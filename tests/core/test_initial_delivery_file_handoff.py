"""Transient IPC sharing cannot bypass deadlines or hide corrupt evidence."""

import json
import sys
import threading
from types import SimpleNamespace

import pytest
from contract import test_a2a_initial_delivery_process_race as witness


def wait(kind, path, process):
    if kind == "one":
        return witness._wait_for(path, process, 1)
    if kind == "any":
        return witness._wait_for_any([path], [process], 1)[1]
    return witness._wait_for_shared_file(path, [process], 1)


@pytest.mark.parametrize("kind", ["one", "any", "shared"])
def test_transient_permission_retry_retains_committed_result_even_after_process_exit(
    tmp_path, monkeypatch, kind
):
    path = tmp_path / "owner-result.json"
    path.write_text('{"commits":1}', encoding="utf-8")
    original = witness._read
    attempts = []

    def read(selected):
        attempts.append(selected)
        if len(attempts) < 3:
            raise PermissionError("controlled Windows sharing violation")
        return original(selected)

    monkeypatch.setattr(witness, "_read", read)
    monkeypatch.setattr(witness.time, "sleep", lambda _seconds: None)
    process = SimpleNamespace(poll=lambda: 0)
    assert wait(kind, path, process) == {"commits": 1}
    assert attempts == [path, path, path]


@pytest.mark.parametrize("kind", ["one", "any", "shared"])
def test_persistent_permission_error_cannot_extend_original_deadline(tmp_path, monkeypatch, kind):
    clock = iter([0, 0.1, 0.2, 0.3, 1.1])
    monkeypatch.setattr(witness.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(witness.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(witness, "_read", lambda _path: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(pytest.fail.Exception, match="timed out"):
        wait(kind, tmp_path / "owner-result.json", SimpleNamespace(poll=lambda: 0))


@pytest.mark.parametrize("kind", ["one", "any", "shared"])
def test_committed_malformed_json_is_never_retried_or_treated_as_pending(tmp_path, kind):
    path = tmp_path / "owner-result.json"
    path.write_text("malformed committed evidence", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        wait(kind, path, SimpleNamespace(poll=lambda: None))


@pytest.mark.skipif(sys.platform != "win32", reason="actual Windows file sharing is required")
def test_real_windows_exclusive_handle_releases_before_original_deadline(tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes

    path = tmp_path / "owner-result.json"
    path.write_text('{"commits":1}', encoding="utf-8")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(path), 0x80000000, 0, None, 3, 0x80, None)
    assert handle not in (None, ctypes.c_void_p(-1).value)
    release = threading.Event()
    denied = []
    original = witness._read

    def read(selected):
        try:
            return original(selected)
        except PermissionError:
            denied.append(selected)
            release.set()
            raise

    def unlock():
        release.wait(timeout=1)
        close(handle)

    thread = threading.Thread(target=unlock)
    monkeypatch.setattr(witness, "_read", read)
    thread.start()
    try:
        assert wait("one", path, SimpleNamespace(poll=lambda: 0)) == {"commits": 1}
        assert denied
    finally:
        release.set()
        thread.join(timeout=2)
        assert not thread.is_alive()
