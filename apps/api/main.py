from __future__ import annotations

import json
import os
import random
import secrets
import signal
import sqlite3
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT_DIR = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT_DIR / "apps" / "web"
DATA_DIR = ROOT_DIR / "data"
ARTIFACT_DIR = Path(os.environ.get("MARATHONRUNNER_ARTIFACT_DIR", DATA_DIR / "artifacts"))
DB_PATH = Path(os.environ.get("MARATHONRUNNER_DB_PATH", DATA_DIR / "marathonrunner.db"))
SERVICE_ROLE = os.environ.get("MARATHONRUNNER_SERVICE_ROLE", "api")

ENGINES = ["JMeter", "k6", "Gatling", "Locust", "Playwright"]
RUN_STATUSES = {"draft", "ready", "pending_approval", "approved", "queued", "running", "completed", "failed", "cancelled"}

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


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def to_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def from_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def audit(connection: sqlite3.Connection, action: str, entity_type: str, entity_id: int | None, details: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO audit_events (actor, action, entity_type, entity_id, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("system", action, entity_type, entity_id, to_json(details), utc_now()),
    )


def initialize_database() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    with connect_db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                owner TEXT NOT NULL,
                business_unit TEXT NOT NULL,
                risk_tier TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS environments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                region TEXT NOT NULL,
                classification TEXT NOT NULL,
                readiness_status TEXT NOT NULL,
                service_virtualization_enabled INTEGER NOT NULL,
                data_residency TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scenarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                engine TEXT NOT NULL,
                test_type TEXT NOT NULL,
                workload_mix TEXT NOT NULL,
                script_repository TEXT NOT NULL,
                target_endpoint TEXT NOT NULL,
                sla_p95_ms INTEGER NOT NULL,
                max_error_rate REAL NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id)
            );

            CREATE TABLE IF NOT EXISTS load_generator_pools (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                region TEXT NOT NULL,
                engines TEXT NOT NULL,
                max_vusers INTEGER NOT NULL,
                status TEXT NOT NULL,
                current_reservation INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS policies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                scope TEXT NOT NULL,
                rule TEXT NOT NULL,
                severity TEXT NOT NULL,
                enabled INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS test_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                scenario_id INTEGER NOT NULL,
                environment_id INTEGER NOT NULL,
                pool_id INTEGER,
                name TEXT NOT NULL,
                engine TEXT NOT NULL,
                load_profile TEXT NOT NULL,
                target_vusers INTEGER NOT NULL,
                duration_minutes INTEGER NOT NULL,
                status TEXT NOT NULL,
                quality_gate TEXT NOT NULL,
                risk_score INTEGER NOT NULL,
                correlation_id TEXT NOT NULL,
                ai_summary TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                FOREIGN KEY(project_id) REFERENCES projects(id),
                FOREIGN KEY(scenario_id) REFERENCES scenarios(id),
                FOREIGN KEY(environment_id) REFERENCES environments(id),
                FOREIGN KEY(pool_id) REFERENCES load_generator_pools(id)
            );

            CREATE TABLE IF NOT EXISTS run_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL UNIQUE,
                p50_ms INTEGER NOT NULL,
                p95_ms INTEGER NOT NULL,
                p99_ms INTEGER NOT NULL,
                throughput_rps REAL NOT NULL,
                error_rate REAL NOT NULL,
                apdex REAL NOT NULL,
                cpu_peak REAL NOT NULL,
                memory_peak REAL NOT NULL,
                redis_latency_ms INTEGER NOT NULL,
                db_cpu_peak REAL NOT NULL,
                artifact_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES test_runs(id)
            );

            CREATE TABLE IF NOT EXISTS ai_insights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER,
                area TEXT NOT NULL,
                severity TEXT NOT NULL,
                insight TEXT NOT NULL,
                evidence TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES test_runs(id)
            );

            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                reviewer TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                decided_at TEXT,
                FOREIGN KEY(run_id) REFERENCES test_runs(id)
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id INTEGER,
                details TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        seed_database(connection)


def table_empty(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def seed_database(connection: sqlite3.Connection) -> None:
    now = utc_now()
    if table_empty(connection, "projects"):
        connection.executemany(
            """
            INSERT INTO projects (name, owner, business_unit, risk_tier, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("Checkout Platform", "Performance Engineering", "Digital Commerce", "critical", now),
                ("Customer API", "Platform Team", "Shared Services", "high", now),
                ("Claims Portal", "QA Enablement", "Insurance", "medium", now),
            ],
        )

    if table_empty(connection, "environments"):
        connection.executemany(
            """
            INSERT INTO environments
            (name, region, classification, readiness_status, service_virtualization_enabled, data_residency, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("performance", "af-south-1", "production-like", "ready", 1, "ZA", now),
                ("integration", "eu-west-1", "shared", "warning", 1, "EU", now),
                ("resilience-lab", "us-east-1", "isolated", "ready", 0, "US", now),
            ],
        )

    if table_empty(connection, "scenarios"):
        connection.executemany(
            """
            INSERT INTO scenarios
            (project_id, name, engine, test_type, workload_mix, script_repository, target_endpoint, sla_p95_ms, max_error_rate, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    1,
                    "Month-End Checkout Baseline",
                    "JMeter",
                    "load",
                    "70% browse, 20% cart, 10% checkout",
                    "git@example.com:performance/checkout-jmeter.git",
                    "https://checkout.perf.example.test",
                    850,
                    1.0,
                    now,
                ),
                (
                    2,
                    "Customer API CI Performance Smoke",
                    "k6",
                    "smoke",
                    "60% read, 30% search, 10% update",
                    "git@example.com:performance/customer-api-k6.git",
                    "https://customer-api.int.example.test",
                    450,
                    0.5,
                    now,
                ),
                (
                    3,
                    "Claims Browser Journey",
                    "Playwright",
                    "browser",
                    "login, claim lookup, document upload",
                    "git@example.com:performance/claims-browser.git",
                    "https://claims.lab.example.test",
                    1500,
                    2.0,
                    now,
                ),
            ],
        )

    if table_empty(connection, "load_generator_pools"):
        connection.executemany(
            """
            INSERT INTO load_generator_pools (name, region, engines, max_vusers, status, current_reservation, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("af-south-1-general", "af-south-1", to_json(["JMeter", "k6", "Locust"]), 12000, "healthy", 0, now),
                ("eu-west-1-browser", "eu-west-1", to_json(["Playwright", "Gatling", "k6"]), 3000, "healthy", 0, now),
                ("us-east-1-resilience", "us-east-1", to_json(["JMeter", "Gatling", "Locust"]), 7000, "maintenance", 0, now),
            ],
        )

    if table_empty(connection, "policies"):
        connection.executemany(
            """
            INSERT INTO policies (name, scope, rule, severity, enabled)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("Production-like approval", "execution", "Require approval for critical projects above 2,000 virtual users", "blocking", 1),
                ("Environment readiness", "execution", "Block execution when environment readiness is not ready", "blocking", 1),
                ("Generator capacity", "execution", "Target virtual users must fit an available load generator pool", "blocking", 1),
                ("SLA evidence", "result", "Every completed run must produce SLA, trend, and artifact evidence", "warning", 1),
                ("Sensitive data masking", "ai", "Mask credentials, tokens, account IDs, and personal data before AI analysis", "blocking", 1),
            ],
        )

    if table_empty(connection, "test_runs"):
        create_run(
            {
                "projectId": 1,
                "scenarioId": 1,
                "environmentId": 1,
                "name": "Month-End Checkout Baseline",
                "loadProfile": "Ramp to 8,000 users over 25 minutes, hold for 45 minutes",
                "targetVusers": 8000,
                "durationMinutes": 70,
            },
            connection,
            seed=True,
        )
        approve_run(1, {"reviewer": "release.manager@example.test", "reason": "Approved baseline execution window."}, connection)
        start_run(1, connection)
        complete_run(1, connection)
        create_run(
            {
                "projectId": 2,
                "scenarioId": 2,
                "environmentId": 2,
                "name": "Customer API CI Performance Smoke",
                "loadProfile": "500 virtual users for 10 minutes",
                "targetVusers": 500,
                "durationMinutes": 10,
            },
            connection,
            seed=True,
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
        """
        SELECT * FROM load_generator_pools
        WHERE region = ? AND status = 'healthy'
        ORDER BY max_vusers DESC
        """,
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
                project_id,
                scenario_id,
                environment_id,
                pool["id"] if pool else None,
                str(payload.get("name") or scenario["name"]),
                engine,
                load_profile,
                target_vusers,
                duration_minutes,
                status,
                quality_gate,
                risk_score,
                correlation_id,
                ai_summary,
                utc_now(),
            ),
        )
        run_id = int(cursor.lastrowid)
        if status == "pending_approval":
            connection.execute(
                """
                INSERT INTO approvals (run_id, status, reviewer, reason, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, "pending", "performance-lead", "; ".join(findings), utc_now()),
            )
        add_ai_insight(
            connection,
            run_id,
            "Test Design",
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


def build_design_summary(scenario: sqlite3.Row, environment: sqlite3.Row, findings: list[str]) -> str:
    if findings:
        return f"AI guardrails found {len(findings)} item(s): {' '.join(findings)}"
    return (
        f"AI design check passed for {scenario['test_type']} scenario in {environment['name']}. "
        "Validate data pools, SLA thresholds, and release evidence before execution."
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
        """
        INSERT INTO ai_insights (run_id, area, severity, insight, evidence, recommendation, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, area, severity, insight, to_json(evidence), recommendation, utc_now()),
    )


def notify(connection: sqlite3.Connection, channel: str, title: str, message: str) -> None:
    connection.execute(
        """
        INSERT INTO notifications (channel, title, message, status, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (channel, title, message, "queued", utc_now()),
    )


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


def get_runs() -> list[dict[str, Any]]:
    with connect_db() as connection:
        rows = connection.execute(
            """
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
            ORDER BY test_runs.id DESC
            """
        ).fetchall()
        return rows_to_dicts(rows)


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
            """
            INSERT INTO approvals (run_id, status, reviewer, reason, created_at, decided_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                "approved",
                str(payload.get("reviewer") or "performance-lead"),
                str(payload.get("reason") or "Approved for controlled execution."),
                utc_now(),
                utc_now(),
            ),
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
            """
            UPDATE test_runs
            SET status = ?, started_at = ?, ai_summary = ?
            WHERE id = ?
            """,
            ("running", utc_now(), "Execution started. AI anomaly watch is active.", run_id),
        )
        if run["pool_id"]:
            connection.execute(
                """
                UPDATE load_generator_pools
                SET current_reservation = current_reservation + ?, updated_at = ?
                WHERE id = ?
                """,
                (run["target_vusers"], utc_now(), run["pool_id"]),
            )
        add_ai_insight(
            connection,
            run_id,
            "Execution Safety",
            "info",
            "Run entered active execution.",
            {"correlationId": run["correlation_id"], "pool": run["pool_name"]},
            "Monitor p95 latency, error rate, generator CPU, and Redis latency.",
        )
        audit(connection, "start_run", "test_run", run_id, {"correlationId": run["correlation_id"]})
        notify(connection, "runs", "Run started", f"{run['name']} is running.")
        if owns_connection:
            connection.commit()
        return get_run(run_id, connection)
    finally:
        if owns_connection:
            connection.close()


def complete_run(run_id: int, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns_connection = connection is None
    if connection is None:
        connection = connect_db()
    try:
        run = get_run(run_id, connection)
        if run["status"] not in {"running", "queued", "approved", "ready"}:
            raise ValueError(f"Run cannot be completed from status {run['status']}")
        random.seed(run["correlation_id"])
        p95 = int(run["sla_p95_ms"] * random.uniform(0.72, 1.28))
        error_rate = round(random.uniform(0.05, max(0.2, run["max_error_rate"] * 1.8)), 2)
        throughput = round(run["target_vusers"] / random.uniform(8.0, 18.0), 2)
        result = {
            "p50_ms": int(p95 * 0.52),
            "p95_ms": p95,
            "p99_ms": int(p95 * random.uniform(1.18, 1.45)),
            "throughput_rps": throughput,
            "error_rate": error_rate,
            "apdex": round(max(0.5, min(0.99, 1.0 - (p95 / max(run["sla_p95_ms"], 1) - 0.7) * 0.25 - error_rate * 0.04)), 2),
            "cpu_peak": round(random.uniform(55.0, 94.0), 1),
            "memory_peak": round(random.uniform(48.0, 88.0), 1),
            "redis_latency_ms": int(random.uniform(2, 38)),
            "db_cpu_peak": round(random.uniform(45.0, 96.0), 1),
        }
        quality_gate = "passed" if result["p95_ms"] <= run["sla_p95_ms"] and result["error_rate"] <= run["max_error_rate"] else "failed"
        risk_score = calculate_risk_score(run, result, quality_gate)
        artifact_path = write_result_artifact(run, result, quality_gate, risk_score)
        connection.execute(
            """
            INSERT OR REPLACE INTO run_results
            (run_id, p50_ms, p95_ms, p99_ms, throughput_rps, error_rate, apdex, cpu_peak,
             memory_peak, redis_latency_ms, db_cpu_peak, artifact_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                result["p50_ms"],
                result["p95_ms"],
                result["p99_ms"],
                result["throughput_rps"],
                result["error_rate"],
                result["apdex"],
                result["cpu_peak"],
                result["memory_peak"],
                result["redis_latency_ms"],
                result["db_cpu_peak"],
                artifact_path,
                utc_now(),
            ),
        )
        connection.execute(
            """
            UPDATE test_runs
            SET status = ?, completed_at = ?, quality_gate = ?, risk_score = ?, ai_summary = ?
            WHERE id = ?
            """,
            ("completed", utc_now(), quality_gate, risk_score, summarize_result(run, result, quality_gate), run_id),
        )
        if run["pool_id"]:
            connection.execute(
                """
                UPDATE load_generator_pools
                SET current_reservation = MAX(0, current_reservation - ?), updated_at = ?
                WHERE id = ?
                """,
                (run["target_vusers"], utc_now(), run["pool_id"]),
            )
        generate_result_insights(connection, run, result, quality_gate)
        audit(connection, "complete_run", "test_run", run_id, {"qualityGate": quality_gate, "riskScore": risk_score})
        notify(connection, "results", "Run completed", f"{run['name']} completed with quality gate {quality_gate}.")
        if owns_connection:
            connection.commit()
        return get_run(run_id, connection)
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
    return str(path)


def generate_result_insights(connection: sqlite3.Connection, run: dict[str, Any], result: dict[str, Any], quality_gate: str) -> None:
    severity = "critical" if quality_gate == "failed" else "info"
    add_ai_insight(
        connection,
        run["id"],
        "Quality Gate",
        severity,
        summarize_result(run, result, quality_gate),
        {"p95Ms": result["p95_ms"], "slaP95Ms": run["sla_p95_ms"], "errorRate": result["error_rate"]},
        "Block release and investigate regressions." if quality_gate == "failed" else "Approve as candidate baseline if business goals were met.",
    )
    if result["db_cpu_peak"] > 82:
        add_ai_insight(
            connection,
            run["id"],
            "Bottleneck Correlation",
            "warning",
            "Database CPU peaked during the highest throughput window.",
            {"dbCpuPeak": result["db_cpu_peak"], "throughputRps": result["throughput_rps"]},
            "Compare slow query logs and database wait events against the run correlation ID.",
        )
    if result["redis_latency_ms"] > 25:
        add_ai_insight(
            connection,
            run["id"],
            "Runtime Data",
            "warning",
            "Redis latency increased enough to affect data-fed transactions.",
            {"redisLatencyMs": result["redis_latency_ms"]},
            "Partition Redis data pools by load pod and inspect exhausted queues.",
        )


def cancel_run(run_id: int) -> dict[str, Any]:
    with connect_db() as connection:
        run = get_run(run_id, connection)
        if run["status"] in {"completed", "failed", "cancelled"}:
            raise ValueError(f"Run cannot be cancelled from status {run['status']}")
        connection.execute("UPDATE test_runs SET status = ?, completed_at = ?, ai_summary = ? WHERE id = ?", ("cancelled", utc_now(), "Run cancelled by operator.", run_id))
        if run["pool_id"] and run["status"] == "running":
            connection.execute(
                """
                UPDATE load_generator_pools
                SET current_reservation = MAX(0, current_reservation - ?), updated_at = ?
                WHERE id = ?
                """,
                (run["target_vusers"], utc_now(), run["pool_id"]),
            )
        audit(connection, "cancel_run", "test_run", run_id, {})
        notify(connection, "runs", "Run cancelled", f"{run['name']} was cancelled.")
        return get_run(run_id, connection)


def queue_ready_runs(limit: int = 2) -> int:
    updated = 0
    with connect_db() as connection:
        rows = connection.execute(
            """
            SELECT id FROM test_runs
            WHERE status IN ('ready', 'approved')
            ORDER BY id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for row in rows:
            connection.execute("UPDATE test_runs SET status = ?, ai_summary = ? WHERE id = ?", ("queued", "Worker queued this run for execution.", row["id"]))
            audit(connection, "queue_run", "test_run", row["id"], {"worker": True})
            updated += 1
    return updated


def process_worker_tick() -> dict[str, int]:
    summary = {"queued": queue_ready_runs(), "started": 0, "completed": 0}
    with connect_db() as connection:
        queued = connection.execute("SELECT id FROM test_runs WHERE status = 'queued' ORDER BY id LIMIT 1").fetchall()
        for row in queued:
            start_run(row["id"], connection)
            summary["started"] += 1
        running = connection.execute(
            """
            SELECT id, started_at FROM test_runs
            WHERE status = 'running'
            ORDER BY id
            LIMIT 2
            """
        ).fetchall()
        for row in running:
            started_at = datetime.fromisoformat(row["started_at"]) if row["started_at"] else datetime.now(timezone.utc) - timedelta(minutes=5)
            if datetime.now(timezone.utc) - started_at >= timedelta(seconds=int(os.environ.get("MARATHONRUNNER_WORKER_COMPLETE_SECONDS", "8"))):
                complete_run(row["id"], connection)
                summary["completed"] += 1
    return summary


def dashboard() -> dict[str, Any]:
    with connect_db() as connection:
        status_rows = connection.execute("SELECT status, COUNT(*) AS total FROM test_runs GROUP BY status").fetchall()
        gate_rows = connection.execute("SELECT quality_gate, COUNT(*) AS total FROM test_runs GROUP BY quality_gate").fetchall()
        latest = connection.execute(
            """
            SELECT test_runs.id, test_runs.name, test_runs.status, test_runs.quality_gate, test_runs.risk_score,
                   projects.name AS project_name, environments.name AS environment_name
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


def can_connect(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def platform_health() -> dict[str, Any]:
    postgres_host = os.environ.get("POSTGRES_HOST", "postgres")
    redis_host = os.environ.get("REDIS_HOST", "redis")
    minio_endpoint = os.environ.get("OBJECT_STORAGE_ENDPOINT", "http://minio:9000")
    return {
        "service": "marathonrunner-enterprise",
        "role": SERVICE_ROLE,
        "status": "ok",
        "timestamp": utc_now(),
        "dependencies": {
            "sqliteControlStore": {"status": "ok", "path": str(DB_PATH)},
            "postgresMetadataTarget": {"status": "reachable" if can_connect(postgres_host, 5432) else "waiting", "host": postgres_host},
            "redisRuntimeData": {"status": "reachable" if can_connect(redis_host, 6379) else "waiting", "host": redis_host},
            "objectStorage": {"status": "configured", "endpoint": minio_endpoint, "artifactPath": str(ARTIFACT_DIR)},
        },
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
                {
                    **dict(row),
                    "evidence": from_json(row["evidence"], {}),
                }
                for row in rows
            ],
        }


def path_id(path: str, prefix: str, suffix: str = "") -> int | None:
    if not path.startswith(prefix):
        return None
    tail = path[len(prefix) :]
    if suffix:
        if not tail.endswith(suffix):
            return None
        tail = tail[: -len(suffix)]
    tail = tail.strip("/")
    if not tail.isdigit():
        return None
    return int(tail)


class MarathonRunnerHandler(BaseHTTPRequestHandler):
    server_version = "MarathonRunnerEnterprise/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/health":
                self.send_json(platform_health())
                return
            if path == "/api/dashboard":
                self.send_json(dashboard())
                return
            if path == "/api/projects":
                self.send_json({"projects": get_table("projects")})
                return
            if path == "/api/environments":
                self.send_json({"environments": get_table("environments")})
                return
            if path == "/api/scenarios":
                self.send_json({"scenarios": get_table("scenarios")})
                return
            if path == "/api/pools":
                pools = get_table("load_generator_pools")
                for pool in pools:
                    pool["engines"] = from_json(pool["engines"], [])
                self.send_json({"pools": pools})
                return
            if path == "/api/policies":
                self.send_json({"policies": get_table("policies")})
                return
            if path == "/api/runs":
                self.send_json({"runs": get_runs()})
                return
            run_id = path_id(path, "/api/runs/")
            if run_id is not None:
                self.send_json({"run": get_run(run_id)})
                return
            if path == "/api/results":
                self.send_json({"results": get_table("run_results", "id DESC")})
                return
            if path == "/api/audit":
                self.send_json({"events": get_table("audit_events", "id DESC")[:80]})
                return
            if path == "/api/notifications":
                self.send_json({"notifications": get_table("notifications", "id DESC")[:30]})
                return
            if path == "/api/roadmap":
                self.send_json(ROADMAP)
                return
            if path == "/api/ai/recommendations":
                self.send_json(ai_recommendations())
                return
            self.serve_static(path)
        except (ValueError, sqlite3.Error) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            payload = self.read_json()
            if path == "/api/runs":
                self.send_json({"run": create_run(payload)}, HTTPStatus.CREATED)
                return
            approve_id = path_id(path, "/api/runs/", "/approve")
            if approve_id is not None:
                self.send_json({"run": approve_run(approve_id, payload)})
                return
            start_id = path_id(path, "/api/runs/", "/start")
            if start_id is not None:
                self.send_json({"run": start_run(start_id)})
                return
            complete_id = path_id(path, "/api/runs/", "/complete")
            if complete_id is not None:
                self.send_json({"run": complete_run(complete_id)})
                return
            cancel_id = path_id(path, "/api/runs/", "/cancel")
            if cancel_id is not None:
                self.send_json({"run": cancel_run(cancel_id)})
                return
            if path == "/api/worker/tick":
                self.send_json({"worker": process_worker_tick()})
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def read_json(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length == 0:
            return {}
        raw_body = self.rfile.read(content_length)
        return json.loads(raw_body.decode("utf-8"))

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, path: str) -> None:
        file_path = WEB_DIR / "index.html" if path == "/" else (WEB_DIR / path.lstrip("/")).resolve()
        if not str(file_path).startswith(str(WEB_DIR.resolve())) or not file_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", self.content_type(file_path))
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def content_type(self, file_path: Path) -> str:
        if file_path.suffix == ".html":
            return "text/html; charset=utf-8"
        if file_path.suffix == ".css":
            return "text/css; charset=utf-8"
        if file_path.suffix == ".js":
            return "application/javascript; charset=utf-8"
        if file_path.suffix == ".json":
            return "application/json; charset=utf-8"
        return "application/octet-stream"

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def run_api() -> None:
    initialize_database()
    host = os.environ.get("MARATHONRUNNER_HOST", "127.0.0.1")
    port = int(os.environ.get("MARATHONRUNNER_PORT", "8080"))
    server = ThreadingHTTPServer((host, port), MarathonRunnerHandler)
    print(f"MarathonRunner Enterprise API running at http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping MarathonRunner Enterprise API.")
    finally:
        server.server_close()


def run_worker() -> None:
    initialize_database()
    stopping = False

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    interval = int(os.environ.get("MARATHONRUNNER_WORKER_INTERVAL_SECONDS", "5"))
    print("MarathonRunner worker started.")
    while not stopping:
        summary = process_worker_tick()
        if any(summary.values()):
            print(f"Worker tick: {summary}")
        time.sleep(interval)
    print("MarathonRunner worker stopped.")


def main() -> None:
    if SERVICE_ROLE == "worker":
        run_worker()
    else:
        run_api()


if __name__ == "__main__":
    main()
