"""Dependency-free exclusive run lock with explicit stale-lock recovery."""
from __future__ import annotations

import atexit
import json
import os
import time
import tempfile
import uuid
from pathlib import Path


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_run_lock(path: Path, fingerprint: str, *, recover_stale: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            prior = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prior = {}
        owner = prior.get("pid")
        if isinstance(owner, int) and _pid_alive(owner):
            raise RuntimeError(f"run lock is owned by live PID {owner}")
        if not recover_stale:
            raise RuntimeError("stale run lock requires explicit --recover-stale-lock")
        path.unlink()
    token = uuid.uuid4().hex
    payload = {"pid": os.getpid(), "process_started_epoch": time.time(), "run_fingerprint": fingerprint, "token": token}
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        raise RuntimeError("run lock was acquired concurrently") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
        json.dump(payload, output, ensure_ascii=True, sort_keys=True)
        output.write("\n")

    def release() -> None:
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            if current.get("token") == token:
                path.unlink()
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            pass

    atexit.register(release)


def acquire_model_lock(endpoint: str, fingerprint: str, *, recover_stale: bool = False) -> Path:
    endpoint_key = __import__("hashlib").sha256(endpoint.encode("utf-8")).hexdigest()[:16]
    path = Path(tempfile.gettempdir()) / "goldset-lab-locks" / f"model-{endpoint_key}.lock"
    acquire_run_lock(path, fingerprint, recover_stale=recover_stale)
    return path
