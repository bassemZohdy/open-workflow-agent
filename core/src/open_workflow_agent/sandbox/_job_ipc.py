"""Bounded authenticated local files, confined to a private supervisor directory."""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import os
import stat
import uuid
from pathlib import Path
from typing import Any

from ..errors import SandboxPolicyError

MAX_FRAME_BYTES = 2_097_152


def _failure() -> SandboxPolicyError:
    return SandboxPolicyError("private sandbox supervisor evidence is unavailable")


def _windows_private(
    path: Path, *, create_directory: bool = False, verify_only: bool = False
) -> None:
    """Protected DACL: only the current account and SYSTEM, no inherited grants."""
    from ctypes import wintypes

    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise _failure()
    advapi = loader("advapi32", use_last_error=True)
    kernel = loader("kernel32", use_last_error=True)

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.DWORD),
            ("descriptor", ctypes.c_void_p),
            ("inherit", wintypes.BOOL),
        ]

    for library, name, arguments, result in (
        (kernel, "GetCurrentProcess", [], wintypes.HANDLE),
        (kernel, "CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
        (kernel, "LocalFree", [ctypes.c_void_p], ctypes.c_void_p),
        (
            kernel,
            "CreateDirectoryW",
            [wintypes.LPCWSTR, ctypes.POINTER(SecurityAttributes)],
            wintypes.BOOL,
        ),
        (
            advapi,
            "OpenProcessToken",
            [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)],
            wintypes.BOOL,
        ),
        (
            advapi,
            "GetTokenInformation",
            [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
            ],
            wintypes.BOOL,
        ),
        (
            advapi,
            "ConvertSidToStringSidW",
            [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)],
            wintypes.BOOL,
        ),
        (
            advapi,
            "ConvertStringSecurityDescriptorToSecurityDescriptorW",
            [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(wintypes.DWORD),
            ],
            wintypes.BOOL,
        ),
        (
            advapi,
            "SetFileSecurityW",
            [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p],
            wintypes.BOOL,
        ),
        (
            advapi,
            "GetFileSecurityW",
            [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
            ],
            wintypes.BOOL,
        ),
        (
            advapi,
            "ConvertSecurityDescriptorToStringSecurityDescriptorW",
            [
                ctypes.c_void_p,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.LPWSTR),
                ctypes.POINTER(wintypes.DWORD),
            ],
            wintypes.BOOL,
        ),
    ):
        function = getattr(library, name)
        function.argtypes, function.restype = arguments, result
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise _failure()
    sid_string, descriptor = wintypes.LPWSTR(), ctypes.c_void_p()
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not 0 < size.value <= 65_536:
            raise _failure()
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, size.value, ctypes.byref(size)):
            raise _failure()
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_string)):
            raise _failure()
        declaration = f"D:P(A;;FA;;;SY)(A;;FA;;;{sid_string.value})"
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            declaration, 1, ctypes.byref(descriptor), None
        ):
            raise _failure()
        if verify_only:
            length = wintypes.DWORD()
            advapi.GetFileSecurityW(str(path), 4, None, 0, ctypes.byref(length))
            if not 0 < length.value <= 65_536:
                raise _failure()
            actual = ctypes.create_string_buffer(length.value)
            if not advapi.GetFileSecurityW(
                str(path), 4, actual, length.value, ctypes.byref(length)
            ):
                raise _failure()
            expected_text, actual_text = wintypes.LPWSTR(), wintypes.LPWSTR()
            try:
                for source, target in ((descriptor, expected_text), (actual, actual_text)):
                    if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                        source, 1, 4, ctypes.byref(target), None
                    ):
                        raise _failure()
                if expected_text.value != actual_text.value:
                    raise _failure()
            finally:
                for value in (expected_text, actual_text):
                    if value:
                        kernel.LocalFree(ctypes.cast(value, ctypes.c_void_p))
        elif create_directory:
            attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
            if not kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)):
                raise _failure()
        elif not advapi.SetFileSecurityW(str(path), 0x80000004, descriptor):
            raise _failure()
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        if sid_string:
            kernel.LocalFree(ctypes.cast(sid_string, ctypes.c_void_p))
        kernel.CloseHandle(token)


def check_path(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise _failure()
    if directory != stat.S_ISDIR(info.st_mode) or not directory and not stat.S_ISREG(info.st_mode):
        raise _failure()
    if os.name == "posix" and (
        info.st_uid != getattr(os, "getuid", lambda: -1)() or info.st_mode & 0o077
    ):
        raise _failure()
    if os.name == "nt":
        _windows_private(path, verify_only=True)


def private_directory(path: Path) -> None:
    for parent in path.parents:
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise _failure()
    if path.exists() or path.is_symlink():
        check_path(path, directory=True)
    elif os.name == "nt":
        _windows_private(path, create_directory=True)
    else:
        path.mkdir(mode=0o700, parents=False)
    check_path(path, directory=True)


def _encoded(value: Any) -> bytes:
    try:
        result = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        if len(result) > MAX_FRAME_BYTES:
            raise ValueError
        return result
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise _failure() from None


def write_frame(path: Path, value: Any, key: bytes, *, exclusive: bool = False) -> None:
    check_path(path.parent, directory=True)
    frame = _encoded(
        {"payload": value, "mac": hmac.new(key, _encoded(value), hashlib.sha256).hexdigest()}
    )
    temporary = path if exclusive else path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        if os.name == "nt":
            _windows_private(temporary)
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            output.write(frame)
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(descriptor)
    if not exclusive:
        os.replace(temporary, path)
    check_path(path)


def create_key(path: Path) -> bytes:
    check_path(path.parent, directory=True)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    value = os.urandom(32)
    try:
        if os.name == "nt":
            _windows_private(path)
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(descriptor)
    return value


def read_key(path: Path) -> bytes:
    check_path(path.parent, directory=True)
    check_path(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if before.st_size != 32:
            raise _failure()
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            value = source.read(33)
        after = os.fstat(descriptor)
        if len(value) != 32 or (before.st_ino, before.st_mtime_ns, before.st_size) != (
            after.st_ino,
            after.st_mtime_ns,
            after.st_size,
        ):
            raise _failure()
        return value
    finally:
        os.close(descriptor)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise _failure()
        result[name] = value
    return result


def read_frame(path: Path, key: bytes) -> Any:
    check_path(path.parent, directory=True)
    check_path(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if not 0 < before.st_size <= MAX_FRAME_BYTES:
            raise _failure()
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            content = source.read(before.st_size + 1)
        after = os.fstat(descriptor)
        if len(content) != before.st_size or (
            before.st_size,
            before.st_mtime_ns,
            before.st_ino,
        ) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise _failure()
        value = json.loads(content.decode("utf-8"), object_pairs_hook=_unique)
        if (
            not isinstance(value, dict)
            or set(value) != {"payload", "mac"}
            or not isinstance(value["mac"], str)
        ):
            raise _failure()
        expected = hmac.new(key, _encoded(value["payload"]), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, value["mac"]):
            raise _failure()
        return value["payload"]
    except (ValueError, UnicodeError, RecursionError):
        raise _failure() from None
    finally:
        os.close(descriptor)
