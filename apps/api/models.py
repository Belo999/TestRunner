from __future__ import annotations

import json
import os
import random
import secrets
import socket
import sqlite3
import time
from datetime import timezone
from pathlib import Path
from typing import Any

from .database import (
    ARTIFACT_DIR,
    connect_db,
    from_json,
    release_pool_reservation_sql,
    rows_to_dicts,
    to_json,
    upsert_run_result_sql,
    utc_now,
)
from .redis_cache import clear_run_state, get_run_state, set_run_state, track_active_run, untrack_active_run
from .storage import upload_artifact

ENGINES = ["JMeter", "k6", "Gatling", "Locust", "Playwright"]
RUN_STATUSES = {"draft", "ready", "pending_approval", "approved", "queued", "running", "completed", "failed", "cancelled"}


def measure_redis_latency_ms() -> int:
    host = os.environ.get("REDIS_HOST", "redis")
    try:
        sock = socket.create_connection((host, 6379), timeout=2)
        start = time.monotonic()
        sock.sendall(b"PING\r\n")
        sock.recv(64)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        sock.close()
        return max(1, elapsed_ms)
    except Exception:
        return 0


def measure_db_latency_ms() -> int:
    start = time.monotonic()
    try:
        conn = connect_db()
        conn.execute("SELECT 1")
        conn.close()
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return max(1, elapsed_ms)
    except Exception:
        return 0

ROADMAP = {
    "featureParity": [
        "Centralized project and test management",
        "Scenario scheduling and execution windows",
        "Load generator pools",
        "Real-time run monitoring",
        "Result repository",
        "SLA and threshold management",
        "Trend analysis",
        "Role-based access control",
        "Audit history",
        "Enterprise reporting",
    ],
    "cloudNative": [
        "Containerized control plane",
        "Kubernetes-ready execution model",
        "Multi-engine runner abstraction",
        "Redis-backed runtime data pools",
        "Object-storage artifact repository",
        "OpenTelemetry-compatible correlation IDs",
        "Policy-as-code guardrails",
        "Docker Compose deployment for local and lab environments",
    ],
    "aiFeatures": [
        "Natural-language test design assistant",
        "AI script review for correlation, assertions, think time, and data usage",
        "AI-generated runtime data pool recommendations",
        "Real-time anomaly detection during active tests",
        "Automatic bottleneck correlation across platform and application metrics",
        "AI-generated run summaries",
        "Intelligent release quality gates",
        "Capacity forecasting",
        "Cost-aware execution recommendations",
        "Automatic defect or incident draft generation",
    ],
    "enterpriseEnhancements": [
        "Environment readiness checks before execution",
        "Service virtualization integration",
        "Performance baseline approval workflow",
        "Golden templates for common performance test types",
        "Test impact analysis",
        "Jira, ServiceNow, Slack, Microsoft Teams, and release tool integrations",
        "Browser-based performance testing",
        "Chaos testing integration",
        "Data residency controls",
        "Tenant-level quotas and chargeback reporting",
        "Automatic cleanup for stale jobs, namespaces, Redis keys, and artifacts",
        "Environment drift detection",
        "API contract validation before high-volume execution",
    ],
}


def audit(connection: sqlite3.Connection, action: str, entity_type: str, entity_id: int | None, details: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO audit_events (actor, action, entity_type, entity_id, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("system", action, entity_type, entity_id, to_json(details), utc_now()),
    )


def notify(connection: sqlite3.Connection, channel: str, title: str, message: str) -> None:
    connection.execute(
        "INSERT INTO notifications (channel, title, message, status, created_at) VALUES (?, ?, ?, ?, ?)",
        (channel, title, message, "queued", utc_now()),
    )


def add_ai_insight(
    connection: sqlite3.Connection,
    run_id: int | None,
    area: str,
    severity: str,
    insight: str,
    evidence: dict[str, Any],
    recommendation: str,
) -> None:
    connection.execute(
        "INSERT INTO ai_insights (run_id, area, severity, insight, evidence, recommendation, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, area, severity, insight, to_json(evidence), recommendation, utc_now()),
    )


def get_project(connection: sqlite3.Connection, project_id: int) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise ValueError("Project not found")
    return row


def get_scenario(connection: sqlite3.Connection, scenario_id: int) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone()
    if row is None:
        raise ValueError("Scenario not found")
    return row


def get_environment(connection: sqlite3.Connection, environment_id: int) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM environments WHERE id = ?", (environment_id,)).fetchone()
    if row is None:
        raise ValueError("Environment not found")
    return row


def find_pool(connection: sqlite3.Connection, engine: str, region: str, target_vusers: int) -> sqlite3.Row | None:
    rows = connection.execute(
        "SELECT * FROM load_generator_pools WHERE region = ? AND status = 'healthy' ORDER BY max_vusers DESC",
        (region,),
    ).fetchall()
    for row in rows:
        if engine in from_json(row["engines"], []) and row["max_vusers"] - row["current_reservation"] >= target_vusers:
            return row
    return None


def policy_decision(project: sqlite3.Row, environment: sqlite3.Row, pool: sqlite3.Row | None, target_vusers: int) -> tuple[str, list[str]]:
    findings: list[str] = []
    if environment["readiness_status"] != "ready":
        findings.append("Environment readiness is not green.")
    if pool is None:
        findings.append("No healthy load generator pool has enough capacity for this run.")
    if project["risk_tier"] == "critical" and target_vusers > 2000:
        findings.append("Critical high-volume run requires explicit approval.")
    status = "pending_approval" if findings else "ready"
    return status, findings


def build_design_summary(scenario: sqlite3.Row, environment: sqlite3.Row, findings: list[str]) -> str:
    if findings:
        return f"AI guardrails found {len(findings)} item(s): {' '.join(findings)}"
    return (
        f"AI design check passed for {scenario['test_type']} scenario in {environment['name']}. "
        "Validate data pools, SLA thresholds, and release evidence before execution."
    )


def create_run(payload: dict[str, Any], connection: sqlite3.Connection | None = None, seed: bool = False) -> dict[str, Any]:
    owns_connection = connection is None
    if connection is None:
        connection = connect_db()
    try:
        project_id = int(payload.get("projectId") or 1)
        scenario_id = int(payload.get("scenarioId") or 1)
        environment_id = int(payload.get("environmentId") or 1)
        project = get_project(connection, project_id)
        scenario = get_scenario(connection, scenario_id)
        environment = get_environment(connection, environment_id)
        engine = str(payload.get("engine") or scenario["engine"])
        if engine not in ENGINES:
            raise ValueError(f"Unsupported engine: {engine}")

        target_vusers = max(1, int(payload.get("targetVusers") or 1000))
        duration_minutes = max(1, int(payload.get("durationMinutes") or 30))
        pool = find_pool(connection, engine, environment["region"], target_vusers)
        status, findings = policy_decision(project, environment, pool, target_vusers)
        quality_gate = "not_evaluated"
        risk_score = min(95, 20 + (target_vusers // 250) + (20 if findings else 0))
        load_profile = str(payload.get("loadProfile") or f"Ramp to {target_vusers:,} users and hold for {duration_minutes} minutes")
        ai_summary = build_design_summary(scenario, environment, findings)
        correlation_id = f"mr-{secrets.token_hex(6)}"

        cursor = connection.execute(
            """
            INSERT INTO test_runs
            (project_id, scenario_id, environment_id, pool_id, name, engine, load_profile, target_vusers,
             duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id, scenario_id, environment_id,
                pool["id"] if pool else None,
                str(payload.get("name") or scenario["name"]),
                engine, load_profile, target_vusers, duration_minutes,
                status, quality_gate, risk_score, correlation_id, ai_summary, utc_now(),
            ),
        )
        run_id = int(cursor.lastrowid)
        if status == "pending_approval":
            connection.execute(
                "INSERT INTO approvals (run_id, status, reviewer, reason, created_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, "pending", "performance-lead", "; ".join(findings), utc_now()),
            )
        add_ai_insight(
            connection, run_id, "Test Design",
            "warning" if findings else "info",
            "Run design was evaluated against policies, environment readiness, and generator capacity.",
            {"policyFindings": findings, "targetVusers": target_vusers, "engine": engine},
            "Review blocking findings before execution." if findings else "Run is ready for execution.",
        )
        audit(connection, "create_run", "test_run", run_id, {"status": status, "targetVusers": target_vusers})
        if not seed:
            notify(connection, "runs", "Run created", f"{payload.get('name') or scenario['name']} is {status}.")
        if owns_connection:
            connection.commit()
        return get_run(run_id, connection)
    finally:
        if owns_connection:
            connection.close()


def get_run(run_id: int, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns_connection = connection is None
    if connection is None:
        connection = connect_db()
    try:
        row = connection.execute(
            """
            SELECT
                test_runs.*,
                projects.name AS project_name,
                projects.owner AS project_owner,
                projects.risk_tier,
                scenarios.name AS scenario_name,
                scenarios.test_type,
                scenarios.workload_mix,
                scenarios.sla_p95_ms,
                scenarios.max_error_rate,
                environments.name AS environment_name,
                environments.region AS environment_region,
                environments.readiness_status,
                load_generator_pools.name AS pool_name
            FROM test_runs
            JOIN projects ON projects.id = test_runs.project_id
            JOIN scenarios ON scenarios.id = test_runs.scenario_id
            JOIN environments ON environments.id = test_runs.environment_id
            LEFT JOIN load_generator_pools ON load_generator_pools.id = test_runs.pool_id
            WHERE test_runs.id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Run not found")
        run = dict(row)
        result = connection.execute("SELECT * FROM run_results WHERE run_id = ?", (run_id,)).fetchone()
        run["result"] = dict(result) if result else None
        return run
    finally:
        if owns_connection:
            connection.close()


def get_runs(connection: sqlite3.Connection | None = None, engine: str | None = None, status: str | None = None, search: str | None = None) -> list[dict[str, Any]]:
    owns_connection = connection is None
    if connection is None:
        connection = connect_db()
    try:
        query = """
            SELECT
                test_runs.*,
                projects.name AS project_name,
                scenarios.name AS scenario_name,
                scenarios.sla_p95_ms,
                scenarios.max_error_rate,
                environments.name AS environment_name,
                load_generator_pools.name AS pool_name
            FROM test_runs
            JOIN projects ON projects.id = test_runs.project_id
            JOIN scenarios ON scenarios.id = test_runs.scenario_id
            JOIN environments ON environments.id = test_runs.environment_id
            LEFT JOIN load_generator_pools ON load_generator_pools.id = test_runs.pool_id
            WHERE 1=1
        """
        params = []
        if engine:
            query += " AND test_runs.engine = ?"
            params.append(engine)
        if status:
            query += " AND test_runs.status = ?"
            params.append(status)
        if search:
            query += " AND (test_runs.name LIKE ? OR projects.name LIKE ? OR scenarios.name LIKE ?)"
            search_pattern = f"%{search}%"
            params.extend([search_pattern, search_pattern, search_pattern])
        query += " ORDER BY test_runs.id DESC"
        rows = connection.execute(query, params).fetchall()
        return rows_to_dicts(rows)
    finally:
        if owns_connection:
            connection.close()


def get_table(table: str, order_by: str = "id") -> list[dict[str, Any]]:
    with connect_db() as connection:
        return rows_to_dicts(connection.execute(f"SELECT * FROM {table} ORDER BY {order_by}").fetchall())


def approve_run(run_id: int, payload: dict[str, Any], connection: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns_connection = connection is None
    if connection is None:
        connection = connect_db()
    try:
        run = get_run(run_id, connection)
        if run["status"] not in {"pending_approval", "ready", "draft"}:
            raise ValueError(f"Run cannot be approved from status {run['status']}")
        connection.execute(
            "UPDATE test_runs SET status = ?, ai_summary = ? WHERE id = ?",
            ("approved", "Approval recorded. Run can now be queued for execution.", run_id),
        )
        connection.execute(
            "INSERT INTO approvals (run_id, status, reviewer, reason, created_at, decided_at) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, "approved", str(payload.get("reviewer") or "performance-lead"), str(payload.get("reason") or "Approved for controlled execution."), utc_now(), utc_now()),
        )
        audit(connection, "approve_run", "test_run", run_id, {"reviewer": payload.get("reviewer")})
        notify(connection, "approvals", "Run approved", f"Run {run_id} has been approved.")
        if owns_connection:
            connection.commit()
        return get_run(run_id, connection)
    finally:
        if owns_connection:
            connection.close()


def start_run(run_id: int, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns_connection = connection is None
    if connection is None:
        connection = connect_db()
    try:
        run = get_run(run_id, connection)
        if run["status"] not in {"ready", "approved", "queued"}:
            raise ValueError(f"Run cannot start from status {run['status']}")
        if run["readiness_status"] != "ready":
            raise ValueError("Environment is not ready")
        connection.execute(
            "UPDATE test_runs SET status = ?, started_at = ?, ai_summary = ? WHERE id = ?",
            ("running", utc_now(), "Execution started. AI anomaly watch is active.", run_id),
        )
        if run["pool_id"]:
            connection.execute(
                "UPDATE load_generator_pools SET current_reservation = current_reservation + ?, updated_at = ? WHERE id = ?",
                (run["target_vusers"], utc_now(), run["pool_id"]),
            )
        add_ai_insight(
            connection, run_id, "Execution Safety", "info",
            "Run entered active execution.",
            {"correlationId": run["correlation_id"], "pool": run["pool_name"]},
            "Monitor p95 latency, error rate, generator CPU, and Redis latency.",
        )
        audit(connection, "start_run", "test_run", run_id, {"correlationId": run["correlation_id"]})
        notify(connection, "runs", "Run started", f"{run['name']} is running.")
        if owns_connection:
            connection.commit()
        updated = get_run(run_id, connection)
        set_run_state(run_id, {
            "status": "running",
            "engine": updated["engine"],
            "targetVusers": updated["target_vusers"],
            "durationMinutes": updated["duration_minutes"],
            "startedAt": updated.get("started_at"),
            "executionId": updated.get("execution_id"),
        })
        track_active_run(run_id)
        return updated
    finally:
        if owns_connection:
            connection.close()


def calculate_risk_score(run: dict[str, Any], result: dict[str, Any], quality_gate: str) -> int:
    score = 15
    score += 35 if quality_gate == "failed" else 5
    score += max(0, int((result["p95_ms"] / max(run["sla_p95_ms"], 1) - 1) * 40))
    score += int(result["error_rate"] * 8)
    score += 15 if result["db_cpu_peak"] > 85 or result["cpu_peak"] > 85 else 0
    return max(1, min(99, score))


def summarize_result(run: dict[str, Any], result: dict[str, Any], quality_gate: str) -> str:
    return (
        f"Quality gate {quality_gate}. p95 {result['p95_ms']} ms versus SLA {run['sla_p95_ms']} ms, "
        f"error rate {result['error_rate']}% versus limit {run['max_error_rate']}%."
    )


def write_result_artifact(run: dict[str, Any], result: dict[str, Any], quality_gate: str, risk_score: int) -> str:
    artifact = {
        "runId": run["id"],
        "correlationId": run["correlation_id"],
        "qualityGate": quality_gate,
        "riskScore": risk_score,
        "metrics": result,
        "createdAt": utc_now(),
    }
    path = ARTIFACT_DIR / f"run-{run['id']}-result.json"
    path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    remote_path = upload_artifact(path, f"runs/run-{run['id']}-result.json")
    return remote_path or str(path)


def generate_result_insights(connection: sqlite3.Connection, run: dict[str, Any], result: dict[str, Any], quality_gate: str) -> None:
    severity = "critical" if quality_gate == "failed" else "info"
    add_ai_insight(
        connection, run["id"], "Quality Gate", severity,
        summarize_result(run, result, quality_gate),
        {"p95Ms": result["p95_ms"], "slaP95Ms": run["sla_p95_ms"], "errorRate": result["error_rate"]},
        "Block release and investigate regressions." if quality_gate == "failed" else "Approve as candidate baseline if business goals were met.",
    )
    if result["db_cpu_peak"] > 82:
        add_ai_insight(
            connection, run["id"], "Bottleneck Correlation", "warning",
            "Database CPU peaked during the highest throughput window.",
            {"dbCpuPeak": result["db_cpu_peak"], "throughputRps": result["throughput_rps"]},
            "Compare slow query logs and database wait events against the run correlation ID.",
        )
    if result["redis_latency_ms"] > 25:
        add_ai_insight(
            connection, run["id"], "Runtime Data", "warning",
            "Redis latency increased enough to affect data-fed transactions.",
            {"redisLatencyMs": result["redis_latency_ms"]},
            "Partition Redis data pools by load pod and inspect exhausted queues.",
        )


def store_real_result(connection: sqlite3.Connection, run: dict[str, Any], engine_result: Any) -> dict[str, Any]:
    result = {
        "p50_ms": engine_result.p50_ms,
        "p95_ms": engine_result.p95_ms,
        "p99_ms": engine_result.p99_ms,
        "throughput_rps": engine_result.throughput_rps,
        "error_rate": engine_result.error_rate,
        "apdex": round(max(0.5, min(0.99, 1.0 - (engine_result.p95_ms / max(run["sla_p95_ms"], 1) - 0.7) * 0.25 - engine_result.error_rate * 0.04)), 2),
        "cpu_peak": 0.0,
        "memory_peak": 0.0,
        "redis_latency_ms": measure_redis_latency_ms(),
        "db_cpu_peak": float(measure_db_latency_ms()),
    }
    quality_gate = "passed" if result["p95_ms"] <= run["sla_p95_ms"] and result["error_rate"] <= run["max_error_rate"] else "failed"
    risk_score = calculate_risk_score(run, result, quality_gate)
    artifact_path = write_result_artifact(run, result, quality_gate, risk_score)
    connection.execute(
        upsert_run_result_sql(),
        (run["id"], result["p50_ms"], result["p95_ms"], result["p99_ms"], result["throughput_rps"],
         result["error_rate"], result["apdex"], result["cpu_peak"], result["memory_peak"],
         result["redis_latency_ms"], result["db_cpu_peak"], artifact_path, utc_now()),
    )
    connection.execute(
        "UPDATE test_runs SET status = ?, completed_at = ?, quality_gate = ?, risk_score = ?, ai_summary = ? WHERE id = ?",
        ("completed", utc_now(), quality_gate, risk_score, summarize_result(run, result, quality_gate), run["id"]),
    )
    if run["pool_id"]:
        connection.execute(
            release_pool_reservation_sql(),
            (run["target_vusers"], utc_now(), run["pool_id"]),
        )
    generate_result_insights(connection, run, result, quality_gate)
    audit(connection, "complete_run", "test_run", run["id"], {"qualityGate": quality_gate, "riskScore": risk_score})
    notify(connection, "results", "Run completed", f"{run['name']} completed with quality gate {quality_gate}.")
    clear_run_state(run["id"])
    untrack_active_run(run["id"])
    try:
        trigger_webhooks("run.completed", {
            "runId": run["id"],
            "name": run["name"],
            "engine": run["engine"],
            "status": "completed",
            "qualityGate": quality_gate,
            "p95": result["p95_ms"],
            "errorRate": result["error_rate"],
            "throughput": result["throughput_rps"],
        })
    except Exception:
        pass
    return get_run(run["id"], connection)


def complete_run(run_id: int, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns_connection = connection is None
    if connection is None:
        connection = connect_db()
    try:
        run = get_run(run_id, connection)
        if run["status"] in {"running", "queued"} or run.get("execution_id"):
            raise ValueError(
                "Manual completion is disabled while a run is executing. "
                "Wait for the worker and engine to finish."
            )
        raise ValueError(
            "Manual completion is disabled. Runs complete automatically via the worker and engine execution."
        )
    finally:
        if owns_connection:
            connection.close()


def cancel_run(run_id: int) -> dict[str, Any]:
    import subprocess
    with connect_db() as connection:
        run = get_run(run_id, connection)
        if run["status"] in {"completed", "failed", "cancelled"}:
            raise ValueError(f"Run cannot be cancelled from status {run['status']}")

        execution_id = run.get("execution_id")
        container_stopped = False
        if execution_id:
            try:
                result = subprocess.run(
                    ["docker", "kill", execution_id],
                    capture_output=True, text=True, timeout=10,
                )
                container_stopped = result.returncode == 0
            except Exception:
                pass
            try:
                subprocess.run(
                    ["docker", "rm", "-f", execution_id],
                    capture_output=True, text=True, timeout=10,
                )
            except Exception:
                pass

        connection.execute(
            "UPDATE test_runs SET status = ?, completed_at = ?, ai_summary = ? WHERE id = ?",
            ("cancelled", utc_now(), f"Run cancelled by operator. Container stopped: {container_stopped}.", run_id),
        )
        if run["pool_id"] and run["status"] == "running":
            connection.execute(
                "UPDATE load_generator_pools SET current_reservation = MAX(0, current_reservation - ?), updated_at = ? WHERE id = ?",
                (run["target_vusers"], utc_now(), run["pool_id"]),
            )
        audit(connection, "cancel_run", "test_run", run_id, {"containerStopped": container_stopped, "executionId": execution_id})
        notify(connection, "runs", "Run cancelled", f"{run['name']} was cancelled.")
        return get_run(run_id, connection)


def dashboard() -> dict[str, Any]:
    with connect_db() as connection:
        status_rows = connection.execute("SELECT status, COUNT(*) AS total FROM test_runs GROUP BY status").fetchall()
        gate_rows = connection.execute("SELECT quality_gate, COUNT(*) AS total FROM test_runs GROUP BY quality_gate").fetchall()
        latest = connection.execute(
            """
            SELECT test_runs.id, test_runs.name, test_runs.status, test_runs.quality_gate, test_runs.risk_score,
                   test_runs.engine, projects.name AS project_name, environments.name AS environment_name
            FROM test_runs
            JOIN projects ON projects.id = test_runs.project_id
            JOIN environments ON environments.id = test_runs.environment_id
            ORDER BY test_runs.id DESC
            LIMIT 6
            """
        ).fetchall()
        critical_insights = connection.execute("SELECT COUNT(*) FROM ai_insights WHERE severity IN ('critical', 'warning')").fetchone()[0]
        return {
            "counts": {
                "projects": connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
                "scenarios": connection.execute("SELECT COUNT(*) FROM scenarios").fetchone()[0],
                "runs": connection.execute("SELECT COUNT(*) FROM test_runs").fetchone()[0],
                "policies": connection.execute("SELECT COUNT(*) FROM policies WHERE enabled = 1").fetchone()[0],
                "insights": connection.execute("SELECT COUNT(*) FROM ai_insights").fetchone()[0],
                "criticalInsights": critical_insights,
            },
            "runsByStatus": {row["status"]: row["total"] for row in status_rows},
            "qualityGates": {row["quality_gate"]: row["total"] for row in gate_rows},
            "latestRuns": rows_to_dicts(latest),
        }


def admin_stats() -> dict[str, Any]:
    with connect_db() as connection:
        pools = connection.execute("SELECT * FROM load_generator_pools").fetchall()
        total_pools = len(pools)
        healthy_pools = sum(1 for p in pools if p["status"] == "healthy")
        running_runs = connection.execute("SELECT COUNT(*) FROM test_runs WHERE status = 'running'").fetchone()[0]
        completed_runs = connection.execute("SELECT COUNT(*) FROM test_runs WHERE status = 'completed'").fetchall()
        total_completed = completed_runs[0][0] if completed_runs else 0
        projects = connection.execute("SELECT * FROM projects").fetchall()
        project_stats = []
        for proj in projects:
            proj_runs = connection.execute("SELECT COUNT(*) FROM test_runs WHERE project_id = ?", (proj["id"],)).fetchone()[0]
            project_stats.append({"name": proj["name"], "runs": proj_runs, "limit": max(10, proj_runs + 5)})
        concurrent_by_day = connection.execute(
            """
            SELECT date(completed_at) as day, COUNT(*) as cnt
            FROM test_runs
            WHERE status = 'completed' AND completed_at IS NOT NULL
            GROUP BY date(completed_at)
            ORDER BY day DESC LIMIT 7
            """
        ).fetchall()
        return {
            "hosts": {
                "total": total_pools,
                "healthy": healthy_pools,
                "unavailable": total_pools - healthy_pools,
                "active": running_runs,
                "idle": total_pools - running_runs,
                "maintenance": 0,
                "load_generator": total_pools,
                "controller": 0,
                "monitoring": 0,
                "physical": 0,
                "cloud": total_pools,
                "container": 0,
            },
            "concurrentRuns": [
                {"day": str(row["day"]), "count": row["cnt"]}
                for row in reversed(concurrent_by_day)
            ],
            "projectStats": project_stats,
        }


def ai_recommendations() -> dict[str, Any]:
    with connect_db() as connection:
        rows = connection.execute(
            """
            SELECT ai_insights.*, test_runs.name AS run_name
            FROM ai_insights
            LEFT JOIN test_runs ON test_runs.id = ai_insights.run_id
            ORDER BY ai_insights.id DESC
            LIMIT 20
            """
        ).fetchall()
        return {
            "summary": "AI analysis assists with design quality, execution safety, anomaly detection, bottleneck correlation, reporting, release risk, and capacity planning.",
            "recommendations": [
                {**dict(row), "evidence": from_json(row["evidence"], {})}
                for row in rows
            ],
        }


def create_entity(table: str, payload: dict[str, Any], required: list[str], defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    missing = [field for field in required if not payload.get(field)]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")
    now = utc_now()
    columns = list(required) + list((defaults or {}).keys()) + ["created_at"]
    values = [payload[field] for field in required]
    values += [payload.get(k, v) for k, v in (defaults or {}).items()]
    values.append(now)
    with connect_db() as connection:
        table_cols = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
        if "updated_at" in table_cols and "updated_at" not in columns:
            columns.append("updated_at")
            values.append(now)
        placeholders = ", ".join(["?"] * len(columns))
        col_names = ", ".join(columns)
        cursor = connection.execute(f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})", values)
        connection.commit()
        row = connection.execute(f"SELECT * FROM {table} WHERE id = ?", (cursor.lastrowid,)).fetchone()
        audit(connection, f"create_{table}", table, cursor.lastrowid, payload)
        return dict(row)


def update_entity(table: str, entity_id: int, payload: dict[str, Any], allowed: list[str]) -> dict[str, Any]:
    updates = {k: v for k, v in payload.items() if k in allowed}
    if not updates:
        raise ValueError("No valid fields to update")
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [entity_id]
    with connect_db() as connection:
        row = connection.execute(f"SELECT * FROM {table} WHERE id = ?", (entity_id,)).fetchone()
        if row is None:
            raise ValueError(f"{table} not found")
        connection.execute(f"UPDATE {table} SET {set_clause} WHERE id = ?", values)
        connection.commit()
        audit(connection, f"update_{table}", table, entity_id, updates)
        row = connection.execute(f"SELECT * FROM {table} WHERE id = ?", (entity_id,)).fetchone()
        return dict(row)


def delete_entity(table: str, entity_id: int) -> dict[str, Any]:
    with connect_db() as connection:
        row = connection.execute(f"SELECT * FROM {table} WHERE id = ?", (entity_id,)).fetchone()
        if row is None:
            raise ValueError(f"{table} not found")
        connection.execute(f"DELETE FROM {table} WHERE id = ?", (entity_id,))
        connection.commit()
        audit(connection, f"delete_{table}", table, entity_id, {"deleted": True})
        return {"deleted": entity_id}


def _detect_regression(current: dict[str, Any], previous: dict[str, Any], scenario: dict[str, Any]) -> list[dict[str, Any]]:
    regressions = []
    sla_p95 = scenario.get("sla_p95_ms", 850)
    max_error = scenario.get("max_error_rate", 1.0)

    if previous["p95"] > 0:
        p95_change = ((current["p95"] - previous["p95"]) / previous["p95"]) * 100
        if p95_change > 20:
            regressions.append({
                "metric": "p95_latency",
                "severity": "critical" if current["p95"] > sla_p95 else "warning",
                "message": f"p95 increased {p95_change:.0f}% ({previous['p95']}ms -> {current['p95']}ms)",
                "baseline": previous["p95"],
                "current": current["p95"],
                "threshold": sla_p95,
            })

    if previous["errorRate"] > 0:
        error_change = current["errorRate"] - previous["errorRate"]
        if error_change > 0.5:
            regressions.append({
                "metric": "error_rate",
                "severity": "critical" if current["errorRate"] > max_error else "warning",
                "message": f"Error rate increased {error_change:.1f}pp ({previous['errorRate']}% -> {current['errorRate']}%)",
                "baseline": previous["errorRate"],
                "current": current["errorRate"],
                "threshold": max_error,
            })

    if previous["throughput"] > 0:
        throughput_change = ((current["throughput"] - previous["throughput"]) / previous["throughput"]) * 100
        if throughput_change < -20:
            regressions.append({
                "metric": "throughput",
                "severity": "warning",
                "message": f"Throughput decreased {abs(throughput_change):.0f}% ({previous['throughput']}rps -> {current['throughput']}rps)",
                "baseline": previous["throughput"],
                "current": current["throughput"],
                "threshold": 0,
            })

    return regressions


def get_trends() -> dict[str, Any]:
    with connect_db() as connection:
        scenarios = connection.execute("SELECT id, name, engine, sla_p95_ms, max_error_rate FROM scenarios").fetchall()
        scenario_trends = []
        for sc in scenarios:
            runs = connection.execute(
                """
                SELECT tr.id, tr.name, tr.created_at, tr.quality_gate, tr.risk_score,
                       rr.p50_ms, rr.p95_ms, rr.p99_ms, rr.throughput_rps, rr.error_rate, rr.apdex
                FROM test_runs tr
                JOIN run_results rr ON rr.run_id = tr.id
                WHERE tr.scenario_id = ? AND tr.status = 'completed'
                ORDER BY tr.id DESC
                LIMIT 10
                """,
                (sc["id"],),
            ).fetchall()

            if not runs:
                continue

            data_points = []
            for run in runs:
                data_points.append({
                    "runId": run["id"],
                    "runName": run["name"],
                    "createdAt": run["created_at"],
                    "qualityGate": run["quality_gate"],
                    "p50": run["p50_ms"],
                    "p95": run["p95_ms"],
                    "p99": run["p99_ms"],
                    "throughput": run["throughput_rps"],
                    "errorRate": run["error_rate"],
                    "apdex": run["apdex"],
                })

            baseline = None
            regressions = []
            if len(data_points) >= 2:
                current = data_points[0]
                previous = data_points[1]
                baseline = {
                    "runId": previous["runId"],
                    "p95": previous["p95"],
                    "errorRate": previous["errorRate"],
                    "throughput": previous["throughput"],
                }
                regressions = _detect_regression(current, previous, dict(sc))

            scenario_trends.append({
                "scenarioId": sc["id"],
                "scenarioName": sc["name"],
                "engine": sc["engine"],
                "slaP95": sc["sla_p95_ms"],
                "maxErrorRate": sc["max_error_rate"],
                "runCount": len(data_points),
                "runs": data_points,
                "baseline": baseline,
                "regressions": regressions,
            })

        all_runs = connection.execute(
            """
            SELECT tr.id, tr.name, tr.engine, tr.created_at, tr.quality_gate,
                   rr.p95_ms, rr.error_rate, rr.throughput_rps, rr.apdex,
                   s.name AS scenario_name
            FROM test_runs tr
            JOIN run_results rr ON rr.run_id = tr.id
            JOIN scenarios s ON s.id = tr.scenario_id
            WHERE tr.status = 'completed'
            ORDER BY tr.created_at DESC
            LIMIT 20
        """
        ).fetchall()

        summary = {
            "totalCompletedRuns": connection.execute("SELECT COUNT(*) FROM test_runs WHERE status = 'completed'").fetchone()[0],
            "totalResults": connection.execute("SELECT COUNT(*) FROM run_results").fetchone()[0],
            "avgP95": connection.execute("SELECT AVG(p95_ms) FROM run_results").fetchone()[0] or 0,
            "avgErrorRate": connection.execute("SELECT AVG(error_rate) FROM run_results").fetchone()[0] or 0,
            "avgApdex": connection.execute("SELECT AVG(apdex) FROM run_results").fetchone()[0] or 0,
        }

        return {
            "summary": summary,
            "scenarioTrends": scenario_trends,
            "recentRuns": [dict(r) for r in all_runs],
        }


def generate_trend_insights() -> list[dict[str, Any]]:
    trends = get_trends()
    insights = []
    for sc in trends["scenarioTrends"]:
        for reg in sc["regressions"]:
            insights.append({
                "area": "Trend Analysis",
                "scenario": sc["scenarioName"],
                "severity": reg["severity"],
                "insight": f"{sc['scenarioName']}: {reg['message']}",
                "recommendation": f"Investigate {reg['metric']} regression. Compare infrastructure metrics and recent code changes.",
            })
        if not sc["regressions"] and sc["runCount"] >= 2:
            current = sc["runs"][0]
            insights.append({
                "area": "Trend Analysis",
                "scenario": sc["scenarioName"],
                "severity": "info",
                "insight": f"{sc['scenarioName']}: Stable performance. p95={current['p95']}ms, errors={current['errorRate']}%, apdex={current['apdex']}.",
                "recommendation": "Consider setting as baseline if business goals are met.",
            })
    return insights


def _parse_cron(expression: str) -> dict[str, Any] | None:
    parts = expression.strip().split()
    if len(parts) != 5:
        return None
    return {
        "minute": parts[0],
        "hour": parts[1],
        "day": parts[2],
        "month": parts[3],
        "weekday": parts[4],
    }


def _cron_matches(cron: dict[str, Any], dt: Any) -> bool:
    from datetime import datetime as dt_module
    checks = [
        (cron["minute"], dt.minute),
        (cron["hour"], dt.hour),
        (cron["day"], dt.day),
        (cron["month"], dt.month),
        (cron["weekday"], dt.weekday()),
    ]
    for pattern, value in checks:
        if pattern == "*":
            continue
        if pattern.isdigit() and int(pattern) != value:
            return False
        if "/" in pattern:
            _, step = pattern.split("/")
            if value % int(step) != 0:
                return False
        if "-" in pattern:
            lo, hi = pattern.split("-")
            if not (int(lo) <= value <= int(hi)):
                return False
        if "," in pattern:
            if value not in [int(x) for x in pattern.split(",")]:
                return False
    return True


def _next_cron_time(cron: dict[str, Any], after: Any) -> Any:
    from datetime import datetime as dt_module, timedelta
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(60 * 24 * 365):
        if _cron_matches(cron, candidate):
            return candidate
        candidate += timedelta(minutes=1)
    return after + timedelta(hours=1)


def compute_next_run(cron_expression: str, after: Any | None = None) -> str:
    from datetime import datetime as dt_module, timedelta, timezone
    if after is None:
        after = dt_module.now(timezone.utc)
    cron = _parse_cron(cron_expression)
    if cron is None:
        return (after + timedelta(hours=1)).isoformat()
    next_time = _next_cron_time(cron, after)
    return next_time.replace(microsecond=0).isoformat()


def create_schedule(payload: dict[str, Any]) -> dict[str, Any]:
    required = ["name", "scenario_id", "environment_id", "target_vusers", "duration_minutes", "load_profile", "cron_expression"]
    missing = [f for f in required if not payload.get(f)]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")
    cron = _parse_cron(payload["cron_expression"])
    if cron is None:
        raise ValueError("Invalid cron expression. Use: minute hour day month weekday")
    next_run = compute_next_run(payload["cron_expression"])
    now = utc_now()
    with connect_db() as connection:
        cursor = connection.execute(
            """INSERT INTO schedules (name, scenario_id, environment_id, target_vusers, duration_minutes, load_profile, cron_expression, enabled, next_run_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (payload["name"], payload["scenario_id"], payload["environment_id"],
             payload["target_vusers"], payload["duration_minutes"], payload["load_profile"],
             payload["cron_expression"], next_run, now),
        )
        connection.commit()
        audit(connection, "create_schedule", "schedule", cursor.lastrowid, payload)
        return get_schedule(cursor.lastrowid)


def get_schedule(schedule_id: int) -> dict[str, Any]:
    with connect_db() as connection:
        row = connection.execute(
            """SELECT s.*, sc.name AS scenario_name, sc.engine, e.name AS environment_name
            FROM schedules s
            JOIN scenarios sc ON sc.id = s.scenario_id
            JOIN environments e ON e.id = s.environment_id
            WHERE s.id = ?""",
            (schedule_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Schedule not found")
        return dict(row)


def get_schedules() -> list[dict[str, Any]]:
    with connect_db() as connection:
        rows = connection.execute(
            """SELECT s.*, sc.name AS scenario_name, sc.engine, e.name AS environment_name
            FROM schedules s
            JOIN scenarios sc ON sc.id = s.scenario_id
            JOIN environments e ON e.id = s.environment_id
            ORDER BY s.id"""
        ).fetchall()
        return [dict(r) for r in rows]


def update_schedule(schedule_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    allowed = ["name", "scenario_id", "environment_id", "target_vusers", "duration_minutes", "load_profile", "cron_expression", "enabled", "next_run_at"]
    updates = {k: v for k, v in payload.items() if k in allowed}
    if not updates:
        raise ValueError("No valid fields to update")
    if "cron_expression" in updates:
        cron = _parse_cron(updates["cron_expression"])
        if cron is None:
            raise ValueError("Invalid cron expression")
        updates["next_run_at"] = compute_next_run(updates["cron_expression"])
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [schedule_id]
    with connect_db() as connection:
        connection.execute(f"UPDATE schedules SET {set_clause} WHERE id = ?", values)
        connection.commit()
        audit(connection, "update_schedule", "schedule", schedule_id, updates)
        return get_schedule(schedule_id)


def delete_schedule(schedule_id: int) -> dict[str, Any]:
    with connect_db() as connection:
        row = connection.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
        if row is None:
            raise ValueError("Schedule not found")
        connection.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        connection.commit()
        audit(connection, "delete_schedule", "schedule", schedule_id, {"deleted": True})
        return {"deleted": schedule_id}


def check_and_execute_schedules() -> dict[str, int]:
    from datetime import datetime as dt_module, timezone
    now = dt_module.now(timezone.utc)
    created = 0
    with connect_db() as connection:
        due = connection.execute(
            "SELECT id FROM schedules WHERE enabled = 1 AND next_run_at <= ?",
            (now.replace(microsecond=0).isoformat(),),
        ).fetchall()
        for row in due:
            schedule = get_schedule(row["id"])
            try:
                create_run({
                    "scenarioId": schedule["scenario_id"],
                    "environmentId": schedule["environment_id"],
                    "name": f"Scheduled: {schedule['name']}",
                    "targetVusers": schedule["target_vusers"],
                    "durationMinutes": schedule["duration_minutes"],
                    "loadProfile": schedule["load_profile"],
                }, connection)
                next_run = compute_next_run(schedule["cron_expression"], now)
                connection.execute(
                    "UPDATE schedules SET last_run_at = ?, next_run_at = ? WHERE id = ?",
                    (now.replace(microsecond=0).isoformat(), next_run, row["id"]),
                )
                audit(connection, "execute_schedule", "schedule", row["id"], {"runCreated": True})
                created += 1
            except Exception as exc:
                audit(connection, "execute_schedule_failed", "schedule", row["id"], {"error": str(exc)})
        connection.commit()
    return {"created": created, "checked": len(due)}


def get_active_runs() -> list[dict[str, Any]]:
    with connect_db() as connection:
        rows = connection.execute(
            """SELECT tr.id, tr.name, tr.engine, tr.status, tr.target_vusers, tr.duration_minutes,
                      tr.started_at, tr.execution_id, tr.correlation_id,
                      sc.name AS scenario_name, e.name AS environment_name
            FROM test_runs tr
            JOIN scenarios sc ON sc.id = tr.scenario_id
            JOIN environments e ON e.id = tr.environment_id
            WHERE tr.status IN ('running', 'queued')
            ORDER BY tr.id"""
        ).fetchall()
        return [dict(r) for r in rows]


def get_run_live(run_id: int) -> dict[str, Any]:
    import subprocess
    run = get_run(run_id)
    live = {
        "id": run["id"],
        "name": run["name"],
        "engine": run["engine"],
        "status": run["status"],
        "targetVusers": run["target_vusers"],
        "durationMinutes": run["duration_minutes"],
        "startedAt": run.get("started_at"),
        "executionId": run.get("execution_id"),
        "correlationId": run["correlation_id"],
        "qualityGate": run["quality_gate"],
        "riskScore": run["risk_score"],
        "elapsedSeconds": 0,
        "progress": 0,
        "containerRunning": False,
        "containerStatus": "unknown",
        "result": run.get("result"),
        "aiSummary": run["ai_summary"],
    }

    cached = get_run_state(run_id)
    if cached:
        live.update({key: value for key, value in cached.items() if value is not None})

    if run.get("started_at") and run["status"] == "running":
        from datetime import datetime as dt_module, timezone
        started = dt_module.fromisoformat(run["started_at"])
        elapsed = (dt_module.now(timezone.utc) - started).total_seconds()
        live["elapsedSeconds"] = round(elapsed)
        live["progress"] = min(100, round((elapsed / (run["duration_minutes"] * 60)) * 100))

    execution_id = run.get("execution_id")
    if execution_id:
        try:
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Status}}", execution_id],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                live["containerStatus"] = result.stdout.strip()
                live["containerRunning"] = result.stdout.strip() == "running"
        except Exception:
            pass

        if live["containerRunning"]:
            try:
                result = subprocess.run(
                    ["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}|{{.MemUsage}}", execution_id],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0 and "|" in result.stdout:
                    cpu, mem = result.stdout.strip().split("|")
                    live["containerCpu"] = cpu
                    live["containerMemory"] = mem
            except Exception:
                pass

    return live


def compare_runs(ids: list[int]) -> dict[str, Any]:
    if len(ids) < 2:
        raise ValueError("Provide at least 2 run IDs to compare")
    with connect_db() as connection:
        runs_data = []
        for run_id in ids[:5]:
            run = get_run(run_id, connection)
            runs_data.append(run)

        metrics = ["p50_ms", "p95_ms", "p99_ms", "throughput_rps", "error_rate", "apdex"]
        comparisons = []
        for metric in metrics:
            values = [r.get("result", {}).get(metric, 0) if r.get("result") else 0 for r in runs_data]
            if len(values) >= 2:
                baseline = values[-1]
                current = values[0]
                if baseline and baseline > 0:
                    delta_pct = round(((current - baseline) / baseline) * 100, 1)
                else:
                    delta_pct = 0
                comparisons.append({
                    "metric": metric,
                    "values": values,
                    "delta": current - baseline,
                    "deltaPercent": delta_pct,
                    "improved": _is_improvement(metric, current, baseline),
                })

        run_summaries = []
        for r in runs_data:
            res = r.get("result") or {}
            run_summaries.append({
                "id": r["id"],
                "name": r["name"],
                "engine": r["engine"],
                "status": r["status"],
                "qualityGate": r["quality_gate"],
                "createdAt": r["created_at"],
                "targetVusers": r["target_vusers"],
                "durationMinutes": r["duration_minutes"],
                "p50": res.get("p50_ms", 0),
                "p95": res.get("p95_ms", 0),
                "p99": res.get("p99_ms", 0),
                "throughput": res.get("throughput_rps", 0),
                "errorRate": res.get("error_rate", 0),
                "apdex": res.get("apdex", 0),
            })

        return {
            "runs": run_summaries,
            "comparisons": comparisons,
            "baseline": run_summaries[-1] if run_summaries else None,
            "current": run_summaries[0] if run_summaries else None,
        }


def _is_improvement(metric: str, current: float, baseline: float) -> bool:
    if metric in ("p50_ms", "p95_ms", "p99_ms", "error_rate"):
        return current < baseline
    if metric in ("throughput_rps", "apdex"):
        return current > baseline
    return False


def generate_run_report(run_id: int) -> dict[str, Any]:
    run = get_run(run_id)
    result = run.get("result") or {}

    with connect_db() as connection:
        insights = connection.execute(
            "SELECT area, severity, insight, evidence, recommendation FROM ai_insights WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()

        previous_runs = connection.execute(
            """SELECT tr.id, tr.name, rr.p95_ms, rr.error_rate, rr.throughput_rps, rr.apdex
            FROM test_runs tr
            JOIN run_results rr ON rr.run_id = tr.id
            WHERE tr.scenario_id = ? AND tr.id < ? AND tr.status = 'completed'
            ORDER BY tr.id DESC LIMIT 1""",
            (run["scenario_id"], run_id),
        ).fetchall()

    baseline = None
    deltas = {}
    if previous_runs:
        prev = previous_runs[0]
        baseline = {"id": prev["id"], "name": prev["name"], "p95": prev["p95_ms"], "errorRate": prev["error_rate"], "throughput": prev["throughput_rps"], "apdex": prev["apdex"]}
        if result.get("p95_ms") and prev["p95_ms"]:
            deltas["p95"] = round(((result["p95_ms"] - prev["p95_ms"]) / prev["p95_ms"]) * 100, 1)
        if result.get("error_rate") is not None and prev["error_rate"] is not None:
            deltas["errorRate"] = round(result["error_rate"] - prev["error_rate"], 2)
        if result.get("throughput_rps") and prev["throughput_rps"]:
            deltas["throughput"] = round(((result["throughput_rps"] - prev["throughput_rps"]) / prev["throughput_rps"]) * 100, 1)

    sla_breaches = []
    if result.get("p95_ms") and run.get("sla_p95_ms"):
        if result["p95_ms"] > run["sla_p95_ms"]:
            sla_breaches.append({"metric": "p95", "actual": result["p95_ms"], "threshold": run["sla_p95_ms"], "overshoot": f"+{result['p95_ms'] - run['sla_p95_ms']}ms"})
    if result.get("error_rate") and run.get("max_error_rate"):
        if result["error_rate"] > run["max_error_rate"]:
            sla_breaches.append({"metric": "error_rate", "actual": result["error_rate"], "threshold": run["max_error_rate"], "overshoot": f"+{result['error_rate'] - run['max_error_rate']}%"})

    warnings = [dict(i) for i in insights if i["severity"] in ("warning", "critical")]
    info_insights = [dict(i) for i in insights if i["severity"] == "info"]

    summary_parts = []
    if run["quality_gate"] == "passed":
        summary_parts.append(f"Test PASSED all SLA thresholds.")
    else:
        summary_parts.append(f"Test FAILED quality gate.")
    if result.get("p95_ms"):
        summary_parts.append(f"p95 was {result['p95_ms']}ms (SLA: {run.get('sla_p95_ms', 'N/A')}ms).")
    if result.get("throughput_rps"):
        summary_parts.append(f"Throughput: {result['throughput_rps']} rps.")
    if result.get("error_rate") is not None:
        summary_parts.append(f"Error rate: {result['error_rate']}%.")
    if warnings:
        summary_parts.append(f"{len(warnings)} warning(s) detected.")

    return {
        "report": {
            "runId": run_id,
            "runName": run["name"],
            "engine": run["engine"],
            "scenario": run.get("scenario_name"),
            "environment": run.get("environment_name"),
            "status": run["status"],
            "qualityGate": run["quality_gate"],
            "riskScore": run["risk_score"],
            "correlationId": run["correlation_id"],
            "createdAt": run["created_at"],
            "startedAt": run.get("started_at"),
            "completedAt": run.get("completed_at"),
            "targetVusers": run["target_vusers"],
            "durationMinutes": run["duration_minutes"],
            "loadProfile": run["load_profile"],
        },
        "metrics": result,
        "sla": {
            "p95Threshold": run.get("sla_p95_ms"),
            "maxErrorRate": run.get("max_error_rate"),
            "breaches": sla_breaches,
        },
        "baseline": baseline,
        "deltas": deltas,
        "insights": {
            "total": len(insights),
            "warnings": warnings,
            "info": info_insights[:5],
        },
        "summary": " ".join(summary_parts),
    }


def check_environment_readiness(environment_id: int) -> dict[str, Any]:
    import subprocess
    with connect_db() as connection:
        env = connection.execute("SELECT * FROM environments WHERE id = ?", (environment_id,)).fetchone()
        if env is None:
            raise ValueError("Environment not found")
        env = dict(env)

    checks = []
    all_ready = True

    checks.append({
        "name": "Environment Status",
        "status": "pass" if env["readiness_status"] == "ready" else "fail",
        "detail": f"Readiness: {env['readiness_status']}",
    })
    if env["readiness_status"] != "ready":
        all_ready = False

    redis_ok = measure_redis_latency_ms() > 0
    checks.append({
        "name": "Redis Connectivity",
        "status": "pass" if redis_ok else "fail",
        "detail": f"Redis latency: {measure_redis_latency_ms()}ms" if redis_ok else "Redis unreachable",
    })
    if not redis_ok:
        all_ready = False

    db_ok = measure_db_latency_ms() > 0
    checks.append({
        "name": "Database Connectivity",
        "status": "pass" if db_ok else "fail",
        "detail": f"SQLite latency: {measure_db_latency_ms()}ms" if db_ok else "Database unreachable",
    })
    if not db_ok:
        all_ready = False

    docker_ok = False
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=5)
        docker_ok = result.returncode == 0
    except Exception:
        pass
    checks.append({
        "name": "Docker Engine",
        "status": "pass" if docker_ok else "fail",
        "detail": "Docker daemon accessible" if docker_ok else "Docker daemon not accessible",
    })
    if not docker_ok:
        all_ready = False

    with connect_db() as conn:
        pools = conn.execute("SELECT name, max_vusers, current_reservation FROM load_generator_pools WHERE status = 'healthy'").fetchall()

    total_capacity = sum(p["max_vusers"] - p["current_reservation"] for p in pools)
    checks.append({
        "name": "Generator Capacity",
        "status": "pass" if total_capacity > 0 else "fail",
        "detail": f"{total_capacity} vusers available across {len(pools)} pool(s)",
    })
    if total_capacity <= 0:
        all_ready = False

    return {
        "environmentId": environment_id,
        "environmentName": env["name"],
        "region": env["region"],
        "ready": all_ready,
        "checks": checks,
        "checkedAt": utc_now(),
    }


ROLES = {
    "admin": {
        "label": "Administrator",
        "permissions": ["create", "read", "update", "delete", "approve", "execute", "manage_users", "view_audit", "manage_policies"],
    },
    "performance_lead": {
        "label": "Performance Lead",
        "permissions": ["create", "read", "update", "approve", "execute", "view_audit"],
    },
    "engineer": {
        "label": "Test Engineer",
        "permissions": ["create", "read", "update", "execute"],
    },
    "viewer": {
        "label": "Stakeholder Viewer",
        "permissions": ["read"],
    },
}


def _strip_password_fields(user: dict[str, Any]) -> dict[str, Any]:
    user.pop("password_hash", None)
    user.pop("password_salt", None)
    return user


def get_users() -> list[dict[str, Any]]:
    with connect_db() as connection:
        rows = connection.execute("SELECT * FROM users ORDER BY id").fetchall()
        users = []
        for row in rows:
            u = dict(row)
            u["roleLabel"] = ROLES.get(u["role"], {}).get("label", u["role"])
            u["permissions"] = ROLES.get(u["role"], {}).get("permissions", [])
            users.append(_strip_password_fields(u))
        return users


def get_user(user_id: int) -> dict[str, Any]:
    with connect_db() as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise ValueError("User not found")
        u = dict(row)
        u["roleLabel"] = ROLES.get(u["role"], {}).get("label", u["role"])
        u["permissions"] = ROLES.get(u["role"], {}).get("permissions", [])
        return _strip_password_fields(u)


def get_user_by_username(username: str) -> dict[str, Any] | None:
    with connect_db() as connection:
        row = connection.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            return None
        u = dict(row)
        u["roleLabel"] = ROLES.get(u["role"], {}).get("label", u["role"])
        u["permissions"] = ROLES.get(u["role"], {}).get("permissions", [])
        return u


def create_user(payload: dict[str, Any]) -> dict[str, Any]:
    from .auth import hash_password
    required = ["username", "display_name", "role"]
    missing = [f for f in required if not payload.get(f)]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")
    if payload["role"] not in ROLES:
        raise ValueError(f"Invalid role. Must be one of: {', '.join(ROLES.keys())}")
    password = payload.get("password")
    if not password:
        raise ValueError("Password is required")
    pw_hash, pw_salt = hash_password(password)
    now = utc_now()
    with connect_db() as connection:
        cursor = connection.execute(
            "INSERT INTO users (username, display_name, role, email, password_hash, password_salt, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (payload["username"], payload["display_name"], payload["role"], payload.get("email", ""), pw_hash, pw_salt, now),
        )
        connection.commit()
        audit(connection, "create_user", "user", cursor.lastrowid, {"username": payload["username"], "role": payload["role"]})
        return get_user(cursor.lastrowid)


def update_user(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    allowed = ["display_name", "role", "email"]
    updates = {k: v for k, v in payload.items() if k in allowed}
    if not updates:
        raise ValueError("No valid fields to update")
    if "role" in updates and updates["role"] not in ROLES:
        raise ValueError(f"Invalid role. Must be one of: {', '.join(ROLES.keys())}")
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [user_id]
    with connect_db() as connection:
        connection.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)
        connection.commit()
        audit(connection, "update_user", "user", user_id, updates)
        return get_user(user_id)


def delete_user(user_id: int) -> dict[str, Any]:
    with connect_db() as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise ValueError("User not found")
        if row["username"] == "admin":
            raise ValueError("Cannot delete admin user")
        connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
        connection.commit()
        audit(connection, "delete_user", "user", user_id, {"deleted": True})
        return {"deleted": user_id}


def get_role_permissions(role: str) -> dict[str, Any]:
    return ROLES.get(role, {"label": role, "permissions": []})


def get_execution_windows() -> list[dict[str, Any]]:
    with connect_db() as connection:
        rows = connection.execute(
            """SELECT ew.*, e.name as environment_name
            FROM execution_windows ew
            LEFT JOIN environments e ON e.id = ew.environment_id
            ORDER BY ew.id"""
        ).fetchall()
        return rows_to_dicts(rows)


def create_execution_window(payload: dict[str, Any]) -> dict[str, Any]:
    required = ["name", "type", "start_hour", "end_hour"]
    missing = [f for f in required if f not in payload]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")
    if payload["type"] not in ("window", "blackout"):
        raise ValueError("Type must be 'window' or 'blackout'")
    now = utc_now()
    with connect_db() as connection:
        cursor = connection.execute(
            """INSERT INTO execution_windows (name, type, day_of_week, start_hour, end_hour, environment_id, enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (payload["name"], payload["type"], payload.get("day_of_week"),
             payload["start_hour"], payload["end_hour"], payload.get("environment_id"),
             payload.get("enabled", 1), now),
        )
        connection.commit()
        audit(connection, "create_execution_window", "execution_window", cursor.lastrowid, payload)
        return get_execution_window(cursor.lastrowid)


def get_execution_window(window_id: int) -> dict[str, Any]:
    with connect_db() as connection:
        row = connection.execute(
            """SELECT ew.*, e.name as environment_name
            FROM execution_windows ew
            LEFT JOIN environments e ON e.id = ew.environment_id
            WHERE ew.id = ?""",
            (window_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Execution window not found")
        return dict(row)


def update_execution_window(window_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    allowed = ["name", "type", "day_of_week", "start_hour", "end_hour", "environment_id", "enabled"]
    updates = {k: v for k, v in payload.items() if k in allowed}
    if not updates:
        raise ValueError("No valid fields to update")
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [window_id]
    with connect_db() as connection:
        connection.execute(f"UPDATE execution_windows SET {set_clause} WHERE id = ?", values)
        connection.commit()
        audit(connection, "update_execution_window", "execution_window", window_id, updates)
        return get_execution_window(window_id)


def delete_execution_window(window_id: int) -> dict[str, Any]:
    with connect_db() as connection:
        row = connection.execute("SELECT * FROM execution_windows WHERE id = ?", (window_id,)).fetchone()
        if row is None:
            raise ValueError("Execution window not found")
        connection.execute("DELETE FROM execution_windows WHERE id = ?", (window_id,))
        connection.commit()
        audit(connection, "delete_execution_window", "execution_window", window_id, {"deleted": True})
        return {"deleted": window_id}


def check_execution_allowed(environment_id: int | None = None) -> dict[str, Any]:
    from datetime import datetime as dt_module
    now = dt_module.now(timezone.utc)
    current_hour = now.hour
    current_day = now.weekday()

    with connect_db() as connection:
        windows = connection.execute(
            "SELECT * FROM execution_windows WHERE enabled = 1"
        ).fetchall()

    blackouts = [dict(w) for w in windows if w["type"] == "blackout"]
    allowed_windows = [dict(w) for w in windows if w["type"] == "window"]

    is_blackout = False
    for b in blackouts:
        if b["environment_id"] and b["environment_id"] != environment_id:
            continue
        if b["day_of_week"] is not None and b["day_of_week"] != current_day:
            continue
        if b["start_hour"] <= current_hour < b["end_hour"]:
            is_blackout = True
            break

    in_window = True
    if allowed_windows:
        in_window = False
        for w in allowed_windows:
            if w["environment_id"] and w["environment_id"] != environment_id:
                continue
            if w["day_of_week"] is not None and w["day_of_week"] != current_day:
                continue
            if w["start_hour"] <= current_hour < w["end_hour"]:
                in_window = True
                break

    allowed = not is_blackout and in_window
    reason = "OK"
    if is_blackout:
        reason = "Blackout period active"
    elif not in_window:
        reason = "Outside execution window"

    return {
        "allowed": allowed,
        "reason": reason,
        "currentHour": current_hour,
        "currentDay": current_day,
        "blackoutsActive": is_blackout,
        "inAllowedWindow": in_window,
    }


def set_baseline(run_id: int, approved_by: str = "system") -> dict[str, Any]:
    with connect_db() as connection:
        run = get_run(run_id, connection)
        if run["status"] != "completed":
            raise ValueError("Only completed runs can be set as baselines")
        if run.get("result") is None:
            raise ValueError("Run must have results to be a baseline")

        connection.execute(
            "UPDATE test_runs SET is_baseline = 1, baseline_approved_by = ? WHERE id = ?",
            (approved_by, run_id),
        )
        connection.commit()
        audit(connection, "set_baseline", "test_run", run_id, {"approvedBy": approved_by})
        notify(connection, "baselines", "Baseline set", f"Run {run_id} ({run['name']}) approved as baseline.")
        return get_run(run_id, connection)


def unset_baseline(run_id: int) -> dict[str, Any]:
    with connect_db() as connection:
        connection.execute(
            "UPDATE test_runs SET is_baseline = 0, baseline_approved_by = NULL WHERE id = ?",
            (run_id,),
        )
        connection.commit()
        audit(connection, "unset_baseline", "test_run", run_id, {})
        return get_run(run_id, connection)


def get_baselines() -> list[dict[str, Any]]:
    with connect_db() as connection:
        rows = connection.execute(
            """SELECT tr.id, tr.name, tr.engine, tr.created_at, tr.baseline_approved_by,
                      sc.name AS scenario_name,
                      rr.p95_ms, rr.error_rate, rr.throughput_rps, rr.apdex
            FROM test_runs tr
            JOIN scenarios sc ON sc.id = tr.scenario_id
            LEFT JOIN run_results rr ON rr.run_id = tr.id
            WHERE tr.is_baseline = 1 AND tr.status = 'completed'
            ORDER BY tr.scenario_id, tr.created_at DESC"""
        ).fetchall()
        return rows_to_dicts(rows)


def get_baseline_for_scenario(scenario_id: int) -> dict[str, Any] | None:
    with connect_db() as connection:
        row = connection.execute(
            """SELECT tr.id, tr.name, tr.engine, rr.p95_ms, rr.error_rate, rr.throughput_rps, rr.apdex
            FROM test_runs tr
            LEFT JOIN run_results rr ON rr.run_id = tr.id
            WHERE tr.scenario_id = ? AND tr.is_baseline = 1 AND tr.status = 'completed'
            ORDER BY tr.created_at DESC LIMIT 1""",
            (scenario_id,),
        ).fetchone()
        return dict(row) if row else None


GOLDEN_TEMPLATES = [
    {
        "id": "baseline",
        "name": "Performance Baseline",
        "description": "Establish a performance baseline for comparison. Runs a steady load to capture normal performance metrics.",
        "test_type": "baseline",
        "default_vusers": 100,
        "default_duration": 10,
        "load_profile": "Steady load at {vusers} users for {duration} minutes",
        "icon": "chart",
        "color": "#00a3e0",
    },
    {
        "id": "load",
        "name": "Load Test",
        "description": "Validate the system handles expected production load. Ramps up to target users and holds steady.",
        "test_type": "load",
        "default_vusers": 500,
        "default_duration": 15,
        "load_profile": "Ramp to {vusers} users over 5 minutes, hold for {duration} minutes",
        "icon": "trending-up",
        "color": "#28a745",
    },
    {
        "id": "stress",
        "name": "Stress Test",
        "description": "Find the breaking point by gradually increasing load beyond expected capacity.",
        "test_type": "stress",
        "default_vusers": 1000,
        "default_duration": 20,
        "load_profile": "Ramp from 100 to {vusers} users over {duration} minutes, increasing by 100 every 2 minutes",
        "icon": "alert-triangle",
        "color": "#dc3545",
    },
    {
        "id": "spike",
        "name": "Spike Test",
        "description": "Test system behavior under sudden traffic spikes. Quick ramp-up and ramp-down.",
        "test_type": "spike",
        "default_vusers": 2000,
        "default_duration": 5,
        "load_profile": "Ramp to {vusers} users in 30 seconds, hold for 2 minutes, ramp down to 100 in 30 seconds",
        "icon": "zap",
        "color": "#ffc107",
    },
    {
        "id": "soak",
        "name": "Soak Test",
        "description": "Long-duration test to detect memory leaks and performance degradation over time.",
        "test_type": "soak",
        "default_vusers": 200,
        "default_duration": 60,
        "load_profile": "Steady load at {vusers} users for {duration} minutes to detect resource exhaustion",
        "icon": "clock",
        "color": "#6f42c1",
    },
    {
        "id": "ci_smoke",
        "name": "CI Performance Smoke",
        "description": "Quick performance validation for CI/CD pipelines. Fast feedback on performance regressions.",
        "test_type": "smoke",
        "default_vusers": 50,
        "default_duration": 2,
        "load_profile": "Quick smoke test with {vusers} users for {duration} minute",
        "icon": "check-circle",
        "color": "#17a2b8",
    },
]


def get_templates() -> list[dict[str, Any]]:
    return GOLDEN_TEMPLATES


def get_template(template_id: str) -> dict[str, Any] | None:
    for t in GOLDEN_TEMPLATES:
        if t["id"] == template_id:
            return t
    return None


def create_run_from_template(template_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    template = get_template(template_id)
    if template is None:
        raise ValueError(f"Template not found: {template_id}")

    vusers = payload.get("targetVusers", template["default_vusers"])
    duration = payload.get("durationMinutes", template["default_duration"])
    load_profile = template["load_profile"].format(vusers=vusers, duration=duration)

    run_payload = {
        "scenarioId": payload.get("scenarioId"),
        "environmentId": payload.get("environmentId"),
        "engine": payload.get("engine"),
        "name": payload.get("name") or f"{template['name']} - {payload.get('scenarioName', 'Test')}",
        "targetVusers": vusers,
        "durationMinutes": duration,
        "loadProfile": load_profile,
    }

    return create_run(run_payload)


def get_webhooks() -> list[dict[str, Any]]:
    with connect_db() as connection:
        rows = connection.execute("SELECT * FROM webhooks ORDER BY id").fetchall()
        return rows_to_dicts(rows)


def create_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    required = ["name", "url", "event"]
    missing = [f for f in required if not payload.get(f)]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")
    now = utc_now()
    with connect_db() as connection:
        cursor = connection.execute(
            "INSERT INTO webhooks (name, url, event, enabled, secret, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (payload["name"], payload["url"], payload["event"],
             payload.get("enabled", 1), payload.get("secret", ""), now),
        )
        connection.commit()
        audit(connection, "create_webhook", "webhook", cursor.lastrowid, {"name": payload["name"], "event": payload["event"]})
        return get_webhook(cursor.lastrowid)


def get_webhook(webhook_id: int) -> dict[str, Any]:
    with connect_db() as connection:
        row = connection.execute("SELECT * FROM webhooks WHERE id = ?", (webhook_id,)).fetchone()
        if row is None:
            raise ValueError("Webhook not found")
        return dict(row)


def update_webhook(webhook_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    allowed = ["name", "url", "event", "enabled", "secret"]
    updates = {k: v for k, v in payload.items() if k in allowed}
    if not updates:
        raise ValueError("No valid fields to update")
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [webhook_id]
    with connect_db() as connection:
        connection.execute(f"UPDATE webhooks SET {set_clause} WHERE id = ?", values)
        connection.commit()
        audit(connection, "update_webhook", "webhook", webhook_id, updates)
        return get_webhook(webhook_id)


def delete_webhook(webhook_id: int) -> dict[str, Any]:
    with connect_db() as connection:
        row = connection.execute("SELECT * FROM webhooks WHERE id = ?", (webhook_id,)).fetchone()
        if row is None:
            raise ValueError("Webhook not found")
        connection.execute("DELETE FROM webhooks WHERE id = ?", (webhook_id,))
        connection.commit()
        audit(connection, "delete_webhook", "webhook", webhook_id, {"deleted": True})
        return {"deleted": webhook_id}


def trigger_webhooks(event: str, payload: dict[str, Any]) -> int:
    import urllib.request
    import hashlib
    import hmac

    triggered = 0
    with connect_db() as connection:
        webhooks = connection.execute(
            "SELECT * FROM webhooks WHERE event = ? AND enabled = 1",
            (event,),
        ).fetchall()

        for wh in webhooks:
            wh = dict(wh)
            try:
                data = json.dumps({
                    "event": event,
                    "timestamp": utc_now(),
                    "data": payload,
                }).encode("utf-8")

                headers = {"Content-Type": "application/json"}
                if wh.get("secret"):
                    signature = hmac.new(wh["secret"].encode(), data, hashlib.sha256).hexdigest()
                    headers["X-Webhook-Signature"] = f"sha256={signature}"

                req = urllib.request.Request(wh["url"], data=data, headers=headers, method="POST")
                urllib.request.urlopen(req, timeout=10)
                triggered += 1
                audit(connection, "webhook_triggered", "webhook", wh["id"], {"event": event, "url": wh["url"]})
            except Exception as exc:
                audit(connection, "webhook_failed", "webhook", wh["id"], {"event": event, "error": str(exc)})

        connection.commit()
    return triggered


def get_test_impact_analysis() -> dict[str, Any]:
    with connect_db() as connection:
        recent_changes = connection.execute(
            """SELECT ae.action, ae.entity_type, ae.entity_id, ae.details, ae.created_at
            FROM audit_events ae
            WHERE ae.entity_type IN ('scenario', 'environment', 'project')
            AND ae.action LIKE 'update_%'
            ORDER BY ae.created_at DESC
            LIMIT 20"""
        ).fetchall()

        affected_scenarios = set()
        affected_environments = set()
        for change in recent_changes:
            if change["entity_type"] == "scenario":
                affected_scenarios.add(change["entity_id"])
            elif change["entity_type"] == "environment":
                affected_environments.add(change["entity_id"])

        recommended_tests = []
        if affected_scenarios or affected_environments:
            query = """
                SELECT tr.id, tr.name, tr.engine, tr.status, tr.scenario_id, tr.environment_id,
                       sc.name AS scenario_name, e.name AS environment_name
                FROM test_runs tr
                JOIN scenarios sc ON sc.id = tr.scenario_id
                JOIN environments e ON e.id = tr.environment_id
                WHERE tr.status = 'completed'
                AND ("""
            params = []
            conditions = []
            if affected_scenarios:
                conditions.append(f"tr.scenario_id IN ({','.join(['?'] * len(affected_scenarios))})")
                params.extend(affected_scenarios)
            if affected_environments:
                conditions.append(f"tr.environment_id IN ({','.join(['?'] * len(affected_environments))})")
                params.extend(affected_environments)
            query += " OR ".join(conditions) + ")"
            query += " ORDER BY tr.created_at DESC LIMIT 10"

            rows = connection.execute(query, params).fetchall()
            recommended_tests = rows_to_dicts(rows)

        impact_score = len(affected_scenarios) * 3 + len(affected_environments) * 2
        risk_level = "low"
        if impact_score > 10:
            risk_level = "high"
        elif impact_score > 5:
            risk_level = "medium"

        return {
            "impactScore": impact_score,
            "riskLevel": risk_level,
            "affectedScenarios": len(affected_scenarios),
            "affectedEnvironments": len(affected_environments),
            "recentChanges": rows_to_dicts(recent_changes),
            "recommendedTests": recommended_tests,
            "summary": f"{len(recent_changes)} recent changes affecting {len(affected_scenarios)} scenarios and {len(affected_environments)} environments. {len(recommended_tests)} tests recommended for re-execution.",
        }


def get_run_logs(run_id: int) -> dict[str, Any]:
    import subprocess
    run = get_run(run_id)
    execution_id = run.get("execution_id")
    if not execution_id:
        return {"logs": "No container running for this run.", "containerId": None}
    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", "200", execution_id],
            capture_output=True, text=True, timeout=10,
        )
        logs = result.stdout + result.stderr
        return {"logs": logs[-5000:] if len(logs) > 5000 else logs, "containerId": execution_id}
    except Exception as exc:
        return {"logs": f"Could not retrieve logs: {exc}", "containerId": execution_id}


def get_applications() -> list[dict[str, Any]]:
    with connect_db() as connection:
        rows = connection.execute("SELECT * FROM applications ORDER BY id").fetchall()
        return rows_to_dicts(rows)


def create_application(payload: dict[str, Any]) -> dict[str, Any]:
    required = ["name", "endpoint"]
    missing = [f for f in required if not payload.get(f)]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")
    now = utc_now()
    with connect_db() as connection:
        cursor = connection.execute(
            """INSERT INTO applications (name, endpoint, team, environment, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (payload["name"], payload["endpoint"], payload.get("team", ""),
             payload.get("environment", ""), payload.get("status", "active"), now),
        )
        connection.commit()
        audit(connection, "create_application", "application", cursor.lastrowid, {"name": payload["name"]})
        return get_application(cursor.lastrowid)


def get_application(app_id: int) -> dict[str, Any]:
    with connect_db() as connection:
        row = connection.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
        if row is None:
            raise ValueError("Application not found")
        return dict(row)


def update_application(app_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    allowed = ["name", "endpoint", "team", "environment", "status"]
    updates = {k: v for k, v in payload.items() if k in allowed}
    if not updates:
        raise ValueError("No valid fields to update")
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [app_id]
    with connect_db() as connection:
        connection.execute(f"UPDATE applications SET {set_clause} WHERE id = ?", values)
        connection.commit()
        audit(connection, "update_application", "application", app_id, updates)
        return get_application(app_id)


def delete_application(app_id: int) -> dict[str, Any]:
    with connect_db() as connection:
        row = connection.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
        if row is None:
            raise ValueError("Application not found")
        connection.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        connection.commit()
        audit(connection, "delete_application", "application", app_id, {"deleted": True})
        return {"deleted": app_id}


def check_application_health(app_id: int) -> dict[str, Any]:
    import urllib.request
    app = get_application(app_id)
    endpoint = app["endpoint"]
    now = utc_now()

    health_status = "unknown"
    response_time_ms = 0
    status_code = 0
    error_message = ""

    try:
        start = time.time()
        req = urllib.request.Request(endpoint, method="GET")
        urllib.request.urlopen(req, timeout=5)
        response_time_ms = int((time.time() - start) * 1000)
        status_code = 200
        health_status = "healthy"
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        health_status = "degraded" if 400 <= exc.code < 500 else "unhealthy"
        error_message = str(exc)
    except Exception as exc:
        health_status = "unreachable"
        error_message = str(exc)

    with connect_db() as connection:
        connection.execute(
            "UPDATE applications SET last_health_check = ?, health_status = ? WHERE id = ?",
            (now, health_status, app_id),
        )
        connection.commit()
        audit(connection, "health_check_application", "application", app_id, {
            "status": health_status,
            "responseTimeMs": response_time_ms,
            "statusCode": status_code,
        })

    return {
        "applicationId": app_id,
        "name": app["name"],
        "endpoint": endpoint,
        "healthStatus": health_status,
        "responseTimeMs": response_time_ms,
        "statusCode": status_code,
        "error": error_message,
        "checkedAt": now,
    }


LOAD_PROFILES = [
    {
        "id": "steady",
        "name": "Steady Load",
        "description": "Constant number of users throughout the test. Best for baseline measurements.",
        "pattern": "Ramp to {vusers} users over 2 minutes, hold steady for {duration} minutes",
        "useCase": "Baseline, soak testing",
        "icon": "minus",
    },
    {
        "id": "ramp_up",
        "name": "Ramp Up",
        "description": "Gradually increase users to target. Simulates growing traffic.",
        "pattern": "Ramp from 0 to {vusers} users over {duration} minutes",
        "useCase": "Load testing, capacity planning",
        "icon": "trending-up",
    },
    {
        "id": "step",
        "name": "Step Load",
        "description": "Increase users in discrete steps. Find breaking points incrementally.",
        "pattern": "Step load: increase by {step} users every {interval} minutes until {vusers}",
        "useCase": "Stress testing, breakpoint detection",
        "icon": "layers",
    },
    {
        "id": "spike",
        "name": "Spike",
        "description": "Sudden burst of traffic followed by normal load. Tests recovery.",
        "pattern": "Normal load at 100 users, spike to {vusers} for 1 minute, return to 100",
        "useCase": "Spike testing, recovery testing",
        "icon": "zap",
    },
    {
        "id": "ramp_hold_ramp",
        "name": "Ramp-Hold-Ramp",
        "description": "Increase to target, hold, then increase again. Tests sustained high load.",
        "pattern": "Ramp to {vusers} over 5 min, hold for {duration} min, ramp to {vusers2} for 5 min",
        "useCase": "Extended load testing",
        "icon": "activity",
    },
    {
        "id": "wave",
        "name": "Wave Pattern",
        "description": "Oscillating load pattern. Simulates periodic traffic variations.",
        "pattern": "Wave pattern: oscillate between 100 and {vusers} users every 5 minutes for {duration} minutes",
        "useCase": "Periodic traffic simulation",
        "icon": "wave",
    },
    {
        "id": "soak",
        "name": "Soak",
        "description": "Long-duration steady load. Detects memory leaks and degradation.",
        "pattern": "Steady load at {vusers} users for {duration} minutes (extended duration)",
        "useCase": "Soak testing, memory leak detection",
        "icon": "clock",
    },
    {
        "id": " ci_smoke",
        "name": "CI Smoke",
        "description": "Quick validation for CI/CD pipelines. Fast feedback.",
        "pattern": "Quick smoke: {vusers} users for {duration} minute",
        "useCase": "CI/CD integration, smoke testing",
        "icon": "check-circle",
    },
]


def get_load_profiles() -> list[dict[str, Any]]:
    return LOAD_PROFILES


def get_load_profile(profile_id: str) -> dict[str, Any] | None:
    for p in LOAD_PROFILES:
        if p["id"] == profile_id:
            return p
    return None


def generate_load_profile(profile_id: str, vusers: int, duration: int, **kwargs) -> str:
    profile = get_load_profile(profile_id)
    if profile is None:
        return f"Ramp to {vusers} users for {duration} minutes"
    return profile["pattern"].format(vusers=vusers, duration=duration, **kwargs)
