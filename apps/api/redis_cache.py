from __future__ import annotations

import json
import os
import socket
from typing import Any


REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_ENABLED = os.environ.get("MARATHONRUNNER_REDIS_ENABLED", "1") == "1"
RUN_KEY_PREFIX = "marathonrunner:run:"
ACTIVE_RUNS_KEY = "marathonrunner:runs:active"


def _send_command(*parts: str) -> str | None:
    if not REDIS_ENABLED:
        return None
    payload = "".join(f"*{len(parts)}\r\n" + "".join(f"${len(part)}\r\n{part}\r\n" for part in parts))
    try:
        with socket.create_connection((REDIS_HOST, REDIS_PORT), timeout=2) as sock:
            sock.sendall(payload.encode("utf-8"))
            response = sock.recv(4096).decode("utf-8", errors="replace")
            if response.startswith("-"):
                return None
            return response
    except OSError:
        return None


def set_run_state(run_id: int, payload: dict[str, Any], ttl_seconds: int = 3600) -> bool:
    key = f"{RUN_KEY_PREFIX}{run_id}"
    body = json.dumps(payload, separators=(",", ":"))
    response = _send_command("SET", key, body, "EX", str(ttl_seconds))
    return response is not None and response.startswith("+OK")


def get_run_state(run_id: int) -> dict[str, Any] | None:
    key = f"{RUN_KEY_PREFIX}{run_id}"
    response = _send_command("GET", key)
    if not response or not response.startswith("$"):
        return None
    lines = response.split("\r\n")
    if len(lines) < 2 or lines[1] in {"", "nil"}:
        return None
    try:
        return json.loads(lines[1])
    except json.JSONDecodeError:
        return None


def clear_run_state(run_id: int) -> None:
    _send_command("DEL", f"{RUN_KEY_PREFIX}{run_id}")


def track_active_run(run_id: int) -> None:
    _send_command("SADD", ACTIVE_RUNS_KEY, str(run_id))


def untrack_active_run(run_id: int) -> None:
    _send_command("SREM", ACTIVE_RUNS_KEY, str(run_id))


def ping_redis() -> bool:
    response = _send_command("PING")
    return response is not None and "+PONG" in response
