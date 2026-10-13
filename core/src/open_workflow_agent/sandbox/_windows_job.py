"""Common restricted-process lifetime: assign a suspended child before it can spawn."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any

from ..config import SandboxConfig
from ..errors import SandboxProcessError


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_longlong),
        ("job_time", ctypes.c_longlong),
        ("flags", wintypes.DWORD),
        ("min_working_set", ctypes.c_size_t),
        ("max_working_set", ctypes.c_size_t),
        ("active_processes", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "read_ops",
            "write_ops",
            "other_ops",
            "read_bytes",
            "write_bytes",
            "other_bytes",
        )
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits),
        ("io", _IoCounters),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("usage", wintypes.DWORD),
        ("thread", wintypes.DWORD),
        ("owner", wintypes.DWORD),
        ("priority", wintypes.LONG),
        ("delta", wintypes.LONG),
        ("flags", wintypes.DWORD),
    ]


class WindowsProcessJob:
    def __init__(self, config: SandboxConfig) -> None:
        factory = getattr(ctypes, "WinDLL", None)
        if factory is None:
            raise SandboxProcessError("restricted stdio process ownership is unavailable")
        kernel: Any = factory("kernel32", use_last_error=True)
        self.kernel = kernel
        for name, arguments, result in (
            ("CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            (
                "SetInformationJobObject",
                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
                wintypes.BOOL,
            ),
            ("OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            ("AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
            ("CreateToolhelp32Snapshot", [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            ("Thread32First", [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
            ("Thread32Next", [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
            ("OpenThread", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            ("ResumeThread", [wintypes.HANDLE], wintypes.DWORD),
        ):
            function = getattr(kernel, name)
            function.argtypes, function.restype = arguments, result
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise SandboxProcessError("restricted stdio process ownership could not be created")
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if config.process_count is not None:
            limits.basic.flags |= 0x8
            limits.basic.active_processes = config.process_count
        if config.memory_bytes is not None:
            limits.basic.flags |= 0x100
            limits.process_memory = config.memory_bytes
        if config.cpu_seconds is not None:
            limits.basic.flags |= 0x2
            limits.basic.process_time = config.cpu_seconds * 10_000_000
        if not kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            self.close()
            raise SandboxProcessError("restricted stdio process limits could not be applied")

    def assign_and_resume(self, pid: int) -> None:
        kernel = self.kernel
        process = kernel.OpenProcess(0x0100 | 0x0001, False, pid)
        if not process:
            raise SandboxProcessError("restricted stdio process could not be owned")
        try:
            if not kernel.AssignProcessToJobObject(self.handle, process):
                raise SandboxProcessError("restricted stdio process could not be owned")
        finally:
            kernel.CloseHandle(process)
        snapshot = kernel.CreateToolhelp32Snapshot(0x4, 0)
        if not snapshot or snapshot == ctypes.c_void_p(-1).value:
            raise SandboxProcessError("restricted stdio process could not be resumed")
        try:
            entry = _ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            found = kernel.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.owner == pid:
                    thread = kernel.OpenThread(0x0002, False, entry.thread)
                    if not thread:
                        break
                    try:
                        if kernel.ResumeThread(thread) != 0xFFFFFFFF:
                            return
                    finally:
                        kernel.CloseHandle(thread)
                    break
                found = kernel.Thread32Next(snapshot, ctypes.byref(entry))
            raise SandboxProcessError("restricted stdio process could not be resumed")
        finally:
            kernel.CloseHandle(snapshot)

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
