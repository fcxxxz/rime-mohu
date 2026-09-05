#!/usr/bin/env python3
"""Probe the primary Tiger model mapping in a live Windows child process."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
MEM_MAPPED = 0x40000


class MemoryBasicInformation(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("_padding", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


class WorkingSetExInformation(ctypes.Structure):
    _fields_ = [
        ("VirtualAddress", ctypes.c_void_p),
        ("VirtualAttributes", ctypes.c_size_t),
    ]


class SystemInfo(ctypes.Structure):
    _fields_ = [
        ("wProcessorArchitecture", wintypes.WORD),
        ("wReserved", wintypes.WORD),
        ("dwPageSize", wintypes.DWORD),
        ("lpMinimumApplicationAddress", ctypes.c_void_p),
        ("lpMaximumApplicationAddress", ctypes.c_void_p),
        ("dwActiveProcessorMask", ctypes.c_size_t),
        ("dwNumberOfProcessors", wintypes.DWORD),
        ("dwProcessorType", wintypes.DWORD),
        ("dwAllocationGranularity", wintypes.DWORD),
        ("wProcessorLevel", wintypes.WORD),
        ("wProcessorRevision", wintypes.WORD),
    ]


def load_engine(dll_path: Path):
    add_directory = getattr(os, "add_dll_directory", None)
    directory = add_directory(str(dll_path.parent)) if add_directory else None
    try:
        dll = ctypes.CDLL(str(dll_path))
    finally:
        if directory is not None:
            directory.close()
    dll.tiger_engine_create.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    dll.tiger_engine_create.restype = ctypes.c_int
    dll.tiger_engine_free.argtypes = [ctypes.c_int]
    dll.tiger_engine_free.restype = None
    return dll


def child_main() -> int:
    dll_path = Path(os.environ["TIGER_ENGINE_DLL"]).resolve()
    model_path = Path(os.environ["TIGER_NGRAM"]).resolve()
    lexicon_path = Path(os.environ["TIGER_LEXICON"]).resolve()
    engine = load_engine(dll_path)
    error = ctypes.create_string_buffer(512)
    handle = engine.tiger_engine_create(
        os.fsencode(model_path), os.fsencode(lexicon_path), 200, 1, error, len(error)
    )
    if handle < 0:
        print(f"create failed: {error.value.decode(errors='replace')}", flush=True)
        return 1
    print("READY", flush=True)
    try:
        sys.stdin.buffer.readline()
    finally:
        engine.tiger_engine_free(handle)
    print("FREED", flush=True)
    sys.stdin.buffer.readline()
    return 0


def dos_path(device_path: str) -> str:
    if not sys.platform == "win32":
        return device_path
    query = ctypes.windll.kernel32.QueryDosDeviceW
    query.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    query.restype = wintypes.DWORD
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        target = ctypes.create_unicode_buffer(1024)
        if query(f"{letter}:", target, len(target)) and device_path.lower().startswith(target.value.lower()):
            return f"{letter}:{device_path[len(target.value):]}"
    return device_path


def count_resident_pages(psapi, handle, ranges: list[tuple[int, int]], page_size: int) -> int:
    query = getattr(psapi, "QueryWorkingSetEx", None)
    if query is None:
        query = psapi.K32QueryWorkingSetEx
    query.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    query.restype = wintypes.BOOL
    total = 0
    batch_size = 4096
    for base, size in ranges:
        page_count = (size + page_size - 1) // page_size
        for offset in range(0, page_count, batch_size):
            count = min(batch_size, page_count - offset)
            entries = (WorkingSetExInformation * count)()
            for index in range(count):
                entries[index].VirtualAddress = ctypes.c_void_p(
                    base + (offset + index) * page_size
                )
            if not query(handle, ctypes.byref(entries), ctypes.sizeof(entries)):
                continue
            total += sum(
                1 for entry in entries if int(entry.VirtualAttributes) & 1
            )
    return total


def process_regions(pid: int, model_path: Path) -> dict[str, Any]:
    kernel = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        raise ctypes.WinError()
    try:
        query = kernel.VirtualQueryEx
        query.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.POINTER(MemoryBasicInformation),
            ctypes.c_size_t,
        ]
        query.restype = ctypes.c_size_t
        system_info = SystemInfo()
        kernel.GetSystemInfo(ctypes.byref(system_info))
        page_size = int(system_info.dwPageSize) or 4096
        mapped_name = psapi.GetMappedFileNameW
        mapped_name.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        mapped_name.restype = wintypes.DWORD
        grouped: dict[int, dict[str, Any]] = {}
        address = 0
        pointer_limit = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1
        while address < pointer_limit:
            info = MemoryBasicInformation()
            if query(handle, ctypes.c_void_p(address), ctypes.byref(info), ctypes.sizeof(info)) == 0:
                break
            base = int(info.BaseAddress or 0)
            size = int(info.RegionSize)
            if size <= 0:
                break
            if int(info.State) == MEM_COMMIT and int(info.Type) == MEM_MAPPED:
                name = ctypes.create_unicode_buffer(1024)
                length = mapped_name(handle, ctypes.c_void_p(base), name, len(name))
                if length:
                    path = dos_path(name.value)
                    key = int(info.AllocationBase or base)
                    item = grouped.setdefault(
                        key, {"path": path, "bytes": 0, "regions": 0, "_ranges": []}
                    )
                    item["bytes"] += size
                    item["regions"] += 1
                    item["_ranges"].append((base, size))
            address = base + size
        expected = str(model_path).lower()
        expected_size = model_path.stat().st_size
        matched_items = [
            item
            for item in grouped.values()
            if str(item["path"]).lower() == expected
            or (Path(str(item["path"])).name.lower() == model_path.name.lower()
                and item["bytes"] >= expected_size)
        ]
        resident_pages = count_resident_pages(
            psapi, handle,
            [segment for item in matched_items for segment in item["_ranges"]],
            page_size,
        )
        matches = [
            {key: value for key, value in item.items() if key != "_ranges"}
            for item in matched_items
        ]
        return {
            "groups": len(matched_items),
            "regions": sum(int(item["regions"]) for item in matched_items),
            "bytes": sum(int(item["bytes"]) for item in matched_items),
            "resident_pages": resident_pages,
            "page_size": page_size,
            "matches": matches,
        }
    finally:
        kernel.CloseHandle(handle)


def parent_main() -> int:
    if sys.platform != "win32":
        print("probe not run: Windows-only mapping probe", file=sys.stderr)
        return 2
    required = ["TIGER_ENGINE_DLL", "TIGER_NGRAM", "TIGER_LEXICON"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        print(f"probe not run: missing {', '.join(missing)}", file=sys.stderr)
        return 2
    model = Path(os.environ["TIGER_NGRAM"]).resolve()
    dll = Path(os.environ["TIGER_ENGINE_DLL"]).resolve()
    lexicon = Path(os.environ["TIGER_LEXICON"]).resolve()
    for path in (model, dll, lexicon):
        if not path.is_file():
            print(f"probe not run: file not found: {path}", file=sys.stderr)
            return 2
    child = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--child"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    )
    assert child.stdout is not None and child.stdin is not None
    ready = child.stdout.readline().strip()
    if ready != "READY":
        stderr = child.stderr.read() if child.stderr else ""
        child.kill()
        print(f"child failed to initialize: {ready} {stderr}", file=sys.stderr)
        return 1
    live = process_regions(child.pid, model)
    child.stdin.write("free\n")
    child.stdin.flush()
    freed = child.stdout.readline().strip()
    after = process_regions(child.pid, model)
    child.stdin.write("exit\n")
    child.stdin.flush()
    child.wait(timeout=30)
    result = {"live": live, "after_free": after, "model": str(model), "dll": str(dll), "lexicon": str(lexicon)}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if freed != "FREED" or child.returncode != 0:
        return 1
    if live["groups"] != 1 or after["groups"] != 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(child_main() if len(sys.argv) > 1 and sys.argv[1] == "--child" else parent_main())
