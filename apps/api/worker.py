from __future__ import annotations

import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .database import ARTIFACT_DIR, SCRIPT_DIR, connect_db, release_pool_reservation_sql, utc_now
from .engines import get_engine
from .models import audit, get_run, notify, start_run, store_real_result
from .redis_cache import clear_run_state, set_run_state, track_active_run, untrack_active_run


WORKER_COMPLETE_SECONDS = int(os.environ.get("MARATHONRUNNER_WORKER_COMPLETE_SECONDS", "300"))
WORKER_INTERVAL_SECONDS = int(os.environ.get("MARATHONRUNNER_WORKER_INTERVAL_SECONDS", "5"))
MAX_RETRIES = 5
RETRY_DELAY = 0.5
HOST_DATA_DIR = os.environ.get("MARATHONRUNNER_HOST_DATA_DIR", "")


def to_host_path(container_path: str) -> str:
    if HOST_DATA_DIR and container_path.startswith("/app/data/"):
        return container_path.replace("/app/data/", HOST_DATA_DIR.rstrip("/") + "/", 1)
    return container_path


def _retry_operation(func: Any, *args: Any, **kwargs: Any) -> Any:
    for attempt in range(MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except sqlite3.OperationalError as exc:
            if "database is locked" in str(exc) and attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
                continue
            raise


def queue_ready_runs(connection: Any, limit: int = 2) -> int:
    updated = 0
    rows = connection.execute(
        "SELECT id FROM test_runs WHERE status IN ('ready', 'approved') ORDER BY id LIMIT ?",
        (limit,),
    ).fetchall()
    for row in rows:
        connection.execute(
            "UPDATE test_runs SET status = ?, ai_summary = ? WHERE id = ?",
            ("queued", "Worker queued this run for execution.", row["id"]),
        )
        audit(connection, "queue_run", "test_run", row["id"], {"worker": True})
        updated += 1
    return updated


def launch_engine(connection: Any, run: dict[str, Any]) -> str | None:
    engine = get_engine(run["engine"])
    if engine is None:
        return None

    result_dir = str(ARTIFACT_DIR / f"run-{run['id']}")
    script_dir = str(SCRIPT_DIR / run["engine"].lower())
    Path(result_dir).mkdir(parents=True, exist_ok=True)

    host_script_dir = to_host_path(script_dir)
    host_result_dir = to_host_path(result_dir)

    scenario = connection.execute("SELECT target_endpoint FROM scenarios WHERE id = ?", (run["scenario_id"],)).fetchone()
    target_endpoint = scenario["target_endpoint"] if scenario else "http://localhost:8080"

    command = engine.build_docker_command(
        script_path=host_script_dir,
        result_dir=host_result_dir,
        target_endpoint=target_endpoint,
        target_vusers=run["target_vusers"],
        duration_minutes=run["duration_minutes"],
    )

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            audit(connection, "engine_launch_failed", "test_run", run["id"], {
                "error": result.stderr[:500],
                "command": " ".join(command[:5]),
            })
            return None

        container_id = result.stdout.strip()

        connection.execute(
            "UPDATE test_runs SET execution_id = ? WHERE id = ?",
            (container_id, run["id"]),
        )
        audit(connection, "engine_launched", "test_run", run["id"], {
            "containerId": container_id,
            "engine": run["engine"],
        })
        set_run_state(run["id"], {
            "status": "running",
            "engine": run["engine"],
            "executionId": container_id,
            "targetVusers": run["target_vusers"],
            "durationMinutes": run["duration_minutes"],
        })
        track_active_run(run["id"])
        return container_id
    except subprocess.TimeoutExpired:
        audit(connection, "engine_launch_timeout", "test_run", run["id"], {"engine": run["engine"]})
        return None
    except Exception as exc:
        audit(connection, "engine_launch_error", "test_run", run["id"], {"error": str(exc)})
        return None


def check_container_running(container_id: str) -> bool:
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_id],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() == "true"
    except Exception:
        return False


def remove_container(container_id: str) -> None:
    try:
        subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, timeout=10)
    except Exception:
        pass


def _fail_run(connection: Any, run: dict[str, Any], reason: str) -> None:
    connection.execute(
        "UPDATE test_runs SET status = ?, completed_at = ?, ai_summary = ? WHERE id = ?",
        ("failed", utc_now(), reason, run["id"]),
    )
    if run["pool_id"]:
        connection.execute(
            release_pool_reservation_sql(),
            (run["target_vusers"], utc_now(), run["pool_id"]),
        )
    audit(connection, "run_failed", "test_run", run["id"], {"reason": reason})
    notify(connection, "runs", "Run failed", f"{run['name']} failed: {reason}")
    clear_run_state(run["id"])
    untrack_active_run(run["id"])


def collect_engine_results(connection: Any, run: dict[str, Any]) -> None:
    engine = get_engine(run["engine"])
    if engine is None:
        _fail_run(connection, run, "Engine adapter not registered.")
        return

    result_dir = str(ARTIFACT_DIR / f"run-{run['id']}")
    try:
        engine_result = engine.parse_results(result_dir)
        if engine_result.total_requests > 0:
            store_real_result(connection, run, engine_result)
        else:
            _fail_run(connection, run, "Engine produced no measurable requests.")
    except Exception as exc:
        _fail_run(connection, run, f"Failed to parse engine results: {exc}")


def process_worker_tick() -> dict[str, int]:
    summary = {"queued": 0, "started": 0, "completed": 0, "failed": 0, "scheduled": 0}
    try:
        from .models import check_and_execute_schedules
        sched_result = check_and_execute_schedules()
        summary["scheduled"] = sched_result.get("created", 0)
    except Exception:
        pass
    try:
        connection = connect_db()
    except Exception:
        return summary
    try:
        summary["queued"] = _retry_operation(queue_ready_runs, connection)

        queued = _retry_operation(
            lambda: connection.execute("SELECT id FROM test_runs WHERE status = 'queued' ORDER BY id LIMIT 2").fetchall()
        )
        for row in queued:
            run = _retry_operation(get_run, row["id"], connection)
            try:
                _retry_operation(start_run, row["id"], connection)
                container_id = launch_engine(connection, run)
                if container_id is None:
                    _retry_operation(
                        lambda r=row["id"]: connection.execute(
                            "UPDATE test_runs SET status = ?, ai_summary = ?, completed_at = ? WHERE id = ?",
                            ("failed", "Engine container failed to start.", utc_now(), r),
                        )
                    )
                    clear_run_state(row["id"])
                    untrack_active_run(row["id"])
                    summary["failed"] += 1
                else:
                    summary["started"] += 1
            except Exception:
                try:
                    _retry_operation(
                        lambda r=row["id"]: connection.execute(
                            "UPDATE test_runs SET status = ?, ai_summary = ?, completed_at = ? WHERE id = ?",
                            ("failed", "Worker failed to start run.", utc_now(), r),
                        )
                    )
                except Exception:
                    pass
                clear_run_state(row["id"])
                untrack_active_run(row["id"])
                summary["failed"] += 1

        running = _retry_operation(
            lambda: connection.execute(
                "SELECT id, started_at, execution_id FROM test_runs WHERE status = 'running' ORDER BY id LIMIT 4"
            ).fetchall()
        )
        for row in running:
            started_at = datetime.fromisoformat(row["started_at"]) if row["started_at"] else datetime.now(timezone.utc) - timedelta(minutes=5)
            elapsed = datetime.now(timezone.utc) - started_at
            execution_id = row["execution_id"]

            if execution_id and not check_container_running(execution_id):
                run = _retry_operation(get_run, row["id"], connection)
                collect_engine_results(connection, run)
                remove_container(execution_id)
                updated = _retry_operation(get_run, row["id"], connection)
                summary["completed" if updated["status"] == "completed" else "failed"] += 1
            elif elapsed >= timedelta(seconds=WORKER_COMPLETE_SECONDS):
                try:
                    subprocess.run(["docker", "kill", execution_id], capture_output=True, timeout=10)
                except Exception:
                    pass
                run = _retry_operation(get_run, row["id"], connection)
                collect_engine_results(connection, run)
                remove_container(execution_id)
                updated = _retry_operation(get_run, row["id"], connection)
                summary["completed" if updated["status"] == "completed" else "failed"] += 1

        connection.commit()
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass
    finally:
        connection.close()
    return summary


def run_worker() -> None:
    from .database import initialize_database
    initialize_database()
    stopping = False

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print("MarathonRunner worker started.")
    while not stopping:
        summary = process_worker_tick()
        if any(summary.values()):
            print(f"Worker tick: {summary}")
        time.sleep(WORKER_INTERVAL_SECONDS)
    print("MarathonRunner worker stopped.")
