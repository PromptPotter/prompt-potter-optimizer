"""Stdlib only: the gate imports this before it has chosen its interpreter."""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

Outcome = tuple[int, str]

_KILL_ON_JOB_CLOSE = 0x2000
_EXTENDED_LIMIT_INFORMATION = 9
# A child starts suspended and joins its check's job before it can start a grandchild.
_CREATE_SUSPENDED = 0x4
_SET_QUOTA_TERMINATE_RESUME = 0x0100 | 0x0001 | 0x0800


class _Basic(ctypes.Structure):
    _fields_ = (
        ("per_process_user_time", ctypes.c_longlong),
        ("per_job_user_time", ctypes.c_longlong),
        ("limit_flags", ctypes.c_ulong),
        ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t),
        ("active_process_limit", ctypes.c_ulong),
        ("affinity", ctypes.c_size_t),
        ("priority_class", ctypes.c_ulong),
        ("scheduling_class", ctypes.c_ulong),
    )


class _Extended(ctypes.Structure):
    _fields_ = (
        ("basic", _Basic),
        ("io_counters", ctypes.c_ulonglong * 6),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    )


class _MemoryStatus(ctypes.Structure):  # MEMORYSTATUSEX
    _fields_ = (
        ("length", ctypes.c_ulong),
        ("load", ctypes.c_ulong),
        ("total_phys", ctypes.c_ulonglong),
        ("avail_phys", ctypes.c_ulonglong),
        *((f"field{i}", ctypes.c_ulonglong) for i in range(5)),
    )


class _Counters(ctypes.Structure):  # PROCESS_MEMORY_COUNTERS
    _fields_ = (
        ("cb", ctypes.c_ulong),
        ("page_faults", ctypes.c_ulong),
        *((f"field{i}", ctypes.c_size_t) for i in range(7)),
        ("peak_commit", ctypes.c_size_t),
    )


if sys.platform == "win32":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    _kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    _kernel32.SetInformationJobObject.argtypes = (
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_ulong,
    )
    _kernel32.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    _kernel32.QueryInformationJobObject.argtypes = (
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
    )
    _kernel32.OpenProcess.restype = ctypes.c_void_p
    _kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
    _kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    _kernel32.K32GetProcessMemoryInfo.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)
    _ntdll = ctypes.WinDLL("ntdll")
    _ntdll.NtResumeProcess.argtypes = (ctypes.c_void_p,)
    _ntdll.NtResumeProcess.restype = ctypes.c_long  # NTSTATUS


class _Metered(threading.local):
    """The job a lane's running check spawns into: its peak is every process that check started."""

    job: int | None = None


metered = _Metered()


def _join_job(job: int, pid: int) -> None:
    if sys.platform == "win32":
        process = _kernel32.OpenProcess(_SET_QUOTA_TERMINATE_RESUME, False, pid)
        try:
            if not (process and _kernel32.AssignProcessToJobObject(job, process)):
                raise ctypes.WinError(ctypes.get_last_error())
            # A child left suspended would hang `communicate()` with no error.
            if status := _ntdll.NtResumeProcess(process):
                raise OSError(f"NtResumeProcess: NTSTATUS {status & 0xFFFFFFFF:#010x}")
        finally:
            _kernel32.CloseHandle(process)


def open_job() -> int | None:
    if sys.platform == "win32":
        job: int | None = _kernel32.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        return job
    return None


def close_job(job: int | None) -> int | None:
    """The job's peak committed memory in MB, then the job is closed."""
    if sys.platform == "win32" and job is not None:
        usage = _Extended()
        queried = _kernel32.QueryInformationJobObject(
            job, _EXTENDED_LIMIT_INFORMATION, ctypes.byref(usage), ctypes.sizeof(usage), None
        )
        _kernel32.CloseHandle(job)
        if not queried:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(usage.peak_job_memory) >> 20
    return None


def end_children_with_this_process() -> None:
    """Windows only: POSIX reaps a foreground tree through its process group."""
    if sys.platform == "win32":
        limits = _Extended()
        limits.basic.limit_flags = _KILL_ON_JOB_CLOSE
        job = _kernel32.CreateJobObjectW(None, None)
        if not (
            job
            and _kernel32.SetInformationJobObject(
                job, _EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)
            )
            and _kernel32.AssignProcessToJobObject(job, _kernel32.GetCurrentProcess())
        ):
            raise ctypes.WinError(ctypes.get_last_error())


def run(argv: Sequence[str], cwd: Path, *, input: str | None = None, **extra_env: str) -> Outcome:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", **extra_env}
    job = metered.job
    with subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=env,
        stdin=None if input is None else subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        creationflags=0 if job is None else _CREATE_SUSPENDED,
    ) as proc:
        if job is not None:
            try:
                _join_job(job, proc.pid)
            except OSError:
                proc.kill()
                raise
        out, err = proc.communicate(input)
    if proc.returncode:
        return proc.returncode, (out + err).strip()
    return 0, out.strip()


def git(*args: str, input: str | None = None) -> Outcome:
    return run(["git", *args], REPO, input=input)


def _locked(fd: int) -> bool:
    # Byte 0 is the lock; the holder's name is written after it, where a waiter can read it.
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


# Exported by the process that holds (or is exempt from) the lock, so the heavy work it starts
# does not queue behind its own parent.
_HELD = "PROMPTPOTTER_HEAVY_LOCK_HELD"


def queue_behind_siblings(label: str, *, exempt: bool = False) -> None:
    """One heavy job per clone at a time, every worktree's included: an OS lock, dropped when we exit."""
    if os.environ.get(_HELD):
        return
    os.environ[_HELD] = label
    rc, common = git("rev-parse", "--path-format=absolute", "--git-common-dir")
    if exempt or rc:  # no clone, so no sibling
        return
    path = Path(common).parent / ".gate_cache" / "gate.lock"
    path.parent.mkdir(exist_ok=True)
    # Never closed: the process holds the lock to its end, whichever way it ends.
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    # No timeout: a whole gate legitimately holds it for many minutes, and a dead holder's lock
    # is gone with its process, so only a hung live holder blocks — re-named on a beat until killed.
    began = time.monotonic()
    said: tuple[str | None, float] = (None, 0.0)
    while not _locked(fd):
        with path.open("rb") as held:
            held.seek(1)
            holder = held.read().decode(errors="replace") or "a sibling"
        waited = time.monotonic() - began
        if holder != said[0] or waited - said[1] >= 60:
            print(f"{label}: waiting on {holder} ({waited / 60:.0f} min so far)", flush=True)
            said = holder, waited
        time.sleep(2)
    os.ftruncate(fd, 1)
    os.lseek(fd, 1, os.SEEK_SET)
    since = time.strftime("%H:%M:%S")
    os.write(fd, f"{label} in {REPO}, pid {os.getpid()}, since {since}".encode())


def free_mb() -> int:
    if sys.platform == "win32":
        status = _MemoryStatus(length=ctypes.sizeof(_MemoryStatus))
        if not _kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(status.avail_phys) >> 20
    if sys.platform == "darwin":
        # What Linux names MemAvailable: free pages plus the ones the kernel reclaims on demand.
        _, stat = run(["vm_stat"], REPO)
        page = re.search(r"page size of (\d+) bytes", stat)
        pages = dict(re.findall(r"^Pages (free|inactive|speculative):\s+(\d+)\.$", stat, re.M))
        if page is None or len(pages) != 3:
            raise RuntimeError(f"vm_stat said what it is not read for:\n{stat}")
        return sum(map(int, pages.values())) * int(page[1]) >> 20
    with open("/proc/meminfo", encoding="ascii") as info:
        return next(int(line.split()[1]) >> 10 for line in info if line.startswith("MemAvailable:"))


def peak_mb() -> int:
    """This process's peak COMMITTED memory on Windows, the figure a gate job prints (its working
    set grows with what the box has free); its peak RSS elsewhere."""
    if sys.platform == "win32":
        counters = _Counters(cb=ctypes.sizeof(_Counters))
        current = ctypes.c_void_p(-1)  # GetCurrentProcess's pseudo-handle
        if not _kernel32.K32GetProcessMemoryInfo(current, ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(counters.peak_commit) >> 20
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # bytes on macOS, KiB elsewhere
    return peak >> 20 if sys.platform == "darwin" else peak >> 10
