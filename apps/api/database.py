from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT_DIR / "apps" / "web"
DATA_DIR = ROOT_DIR / "data"
ARTIFACT_DIR = Path(os.environ.get("MARATHONRUNNER_ARTIFACT_DIR", DATA_DIR / "artifacts"))
DB_PATH = Path(os.environ.get("MARATHONRUNNER_DB_PATH", DATA_DIR / "marathonrunner.db"))
SCRIPT_DIR = DATA_DIR / "scripts"

DB_BACKEND = os.environ.get("MARATHONRUNNER_DB_BACKEND", "sqlite")


def adapt_query(query: str, backend: str | None = None) -> str:
    backend = backend or DB_BACKEND
    if backend == "postgresql":
        return query.replace("?", "%s")
    return query


def upsert_run_result_sql(backend: str | None = None) -> str:
    backend = backend or DB_BACKEND
    columns = (
        "run_id, p50_ms, p95_ms, p99_ms, throughput_rps, error_rate, apdex, cpu_peak, "
        "memory_peak, redis_latency_ms, db_cpu_peak, artifact_path, created_at"
    )
    placeholders = ", ".join(["?"] * 13)
    if backend == "postgresql":
        return f"""
            INSERT INTO run_results ({columns}) VALUES ({placeholders.replace("?", "%s")})
            ON CONFLICT (run_id) DO UPDATE SET
                p50_ms = EXCLUDED.p50_ms,
                p95_ms = EXCLUDED.p95_ms,
                p99_ms = EXCLUDED.p99_ms,
                throughput_rps = EXCLUDED.throughput_rps,
                error_rate = EXCLUDED.error_rate,
                apdex = EXCLUDED.apdex,
                cpu_peak = EXCLUDED.cpu_peak,
                memory_peak = EXCLUDED.memory_peak,
                redis_latency_ms = EXCLUDED.redis_latency_ms,
                db_cpu_peak = EXCLUDED.db_cpu_peak,
                artifact_path = EXCLUDED.artifact_path,
                created_at = EXCLUDED.created_at
        """
    return f"INSERT OR REPLACE INTO run_results ({columns}) VALUES ({placeholders})"


def release_pool_reservation_sql(backend: str | None = None) -> str:
    backend = backend or DB_BACKEND
    if backend == "postgresql":
        return (
            "UPDATE load_generator_pools SET current_reservation = "
            "GREATEST(0, current_reservation - %s), updated_at = %s WHERE id = %s"
        )
    return (
        "UPDATE load_generator_pools SET current_reservation = "
        "MAX(0, current_reservation - ?), updated_at = ? WHERE id = ?"
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class DatabaseConnection:
    def __init__(self, connection: Any, backend: str):
        self._conn = connection
        self._backend = backend

    def execute(self, query: str, params: tuple = ()) -> Any:
        query = adapt_query(query, self._backend)
        if self._backend == "postgresql":
            cursor = self._conn.cursor()
            cursor.execute(query, params)
            return PgResultWrapper(cursor)
        else:
            return self._conn.execute(query, params)

    def executescript(self, script: str) -> None:
        if self._backend == "postgresql":
            cursor = self._conn.cursor()
            for statement in script.split(";"):
                statement = statement.strip()
                if statement:
                    cursor.execute(statement)
            self._conn.commit()
        else:
            self._conn.executescript(script)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


class PgResultWrapper:
    def __init__(self, cursor: Any):
        self._cursor = cursor

    def fetchone(self) -> Any:
        row = self._cursor.fetchone()
        if row is None:
            return None
        return PgRowWrapper(row, self._cursor.description)

    def fetchall(self) -> list[Any]:
        rows = self._cursor.fetchall()
        desc = self._cursor.description
        return [PgRowWrapper(row, desc) for row in rows]

    @property
    def lastrowid(self) -> int | None:
        return self._cursor.lastrowid if self._cursor.lastrowid else None


class PgRowWrapper:
    def __init__(self, row: Any, description: Any):
        self._row = row
        self._desc = description
        self._columns = {desc[0]: i for i, desc in enumerate(description)} if description else {}

    def __getitem__(self, key: str) -> Any:
        if key in self._columns:
            return self._row[self._columns[key]]
        raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        return key in self._columns

    def keys(self) -> list[str]:
        return list(self._columns.keys())

    def __iter__(self):
        return iter(self._row)

    def __len__(self):
        return len(self._row)


def connect_db() -> DatabaseConnection:
    if DB_BACKEND == "postgresql":
        return connect_postgresql()
    return connect_sqlite()


def connect_sqlite() -> DatabaseConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(DB_PATH), timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return DatabaseConnection(connection, "sqlite")


def connect_postgresql() -> DatabaseConnection:
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        raise ImportError("psycopg2 is required for PostgreSQL backend. Install with: pip install psycopg2-binary")

    host = os.environ.get("POSTGRES_HOST", "postgres")
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = os.environ.get("POSTGRES_USER", "marathonrunner")
    password = os.environ.get("POSTGRES_PASSWORD", "marathonrunner")
    dbname = os.environ.get("POSTGRES_DB", "marathonrunner")

    connection = psycopg2.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname=dbname,
    )
    connection.autocommit = False
    return DatabaseConnection(connection, "postgresql")


def to_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def from_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def rows_to_dicts(rows: list) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        if hasattr(row, 'keys'):
            result.append({key: row[key] for key in row.keys()})
        else:
            result.append(dict(row))
    return result


def table_empty(connection: DatabaseConnection, table: str) -> bool:
    result = connection.execute(f"SELECT COUNT(*) AS cnt FROM {table}").fetchone()
    return result["cnt"] == 0


SCHEMA_SQLITE = """
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
    execution_id TEXT,
    is_baseline INTEGER NOT NULL DEFAULT 0,
    baseline_approved_by TEXT,
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

CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    scenario_id INTEGER NOT NULL,
    environment_id INTEGER NOT NULL,
    target_vusers INTEGER NOT NULL,
    duration_minutes INTEGER NOT NULL,
    load_profile TEXT NOT NULL,
    cron_expression TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    next_run_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(scenario_id) REFERENCES scenarios(id),
    FOREIGN KEY(environment_id) REFERENCES environments(id)
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer',
    email TEXT,
    password_hash TEXT,
    password_salt TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    day_of_week INTEGER,
    start_hour INTEGER NOT NULL,
    end_hour INTEGER NOT NULL,
    environment_id INTEGER,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    FOREIGN KEY(environment_id) REFERENCES environments(id)
);

CREATE TABLE IF NOT EXISTS webhooks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    event TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    secret TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    team TEXT,
    environment TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    last_health_check TEXT,
    health_status TEXT DEFAULT 'unknown',
    created_at TEXT NOT NULL
);
"""

SCHEMA_POSTGRESQL = """
CREATE TABLE IF NOT EXISTS projects (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    owner TEXT NOT NULL,
    business_unit TEXT NOT NULL,
    risk_tier TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS environments (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT NOT NULL,
    classification TEXT NOT NULL,
    readiness_status TEXT NOT NULL,
    service_virtualization_enabled INTEGER NOT NULL,
    data_residency TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scenarios (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    engine TEXT NOT NULL,
    test_type TEXT NOT NULL,
    workload_mix TEXT NOT NULL,
    script_repository TEXT NOT NULL,
    target_endpoint TEXT NOT NULL,
    sla_p95_ms INTEGER NOT NULL,
    max_error_rate REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS load_generator_pools (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT NOT NULL,
    engines TEXT NOT NULL,
    max_vusers INTEGER NOT NULL,
    status TEXT NOT NULL,
    current_reservation INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policies (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    scope TEXT NOT NULL,
    rule TEXT NOT NULL,
    severity TEXT NOT NULL,
    enabled INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS test_runs (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    scenario_id INTEGER NOT NULL REFERENCES scenarios(id),
    environment_id INTEGER NOT NULL REFERENCES environments(id),
    pool_id INTEGER REFERENCES load_generator_pools(id),
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
    execution_id TEXT,
    is_baseline INTEGER NOT NULL DEFAULT 0,
    baseline_approved_by TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS run_results (
    id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL UNIQUE REFERENCES test_runs(id),
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
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_insights (
    id SERIAL PRIMARY KEY,
    run_id INTEGER REFERENCES test_runs(id),
    area TEXT NOT NULL,
    severity TEXT NOT NULL,
    insight TEXT NOT NULL,
    evidence TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES test_runs(id),
    status TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    channel TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id SERIAL PRIMARY KEY,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER,
    details TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    scenario_id INTEGER NOT NULL REFERENCES scenarios(id),
    environment_id INTEGER NOT NULL REFERENCES environments(id),
    target_vusers INTEGER NOT NULL,
    duration_minutes INTEGER NOT NULL,
    load_profile TEXT NOT NULL,
    cron_expression TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    next_run_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer',
    email TEXT,
    password_hash TEXT,
    password_salt TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_windows (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    day_of_week INTEGER,
    start_hour INTEGER NOT NULL,
    end_hour INTEGER NOT NULL,
    environment_id INTEGER REFERENCES environments(id),
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webhooks (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    event TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    secret TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    team TEXT,
    environment TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    last_health_check TEXT,
    health_status TEXT DEFAULT 'unknown',
    created_at TEXT NOT NULL
);
"""


def migrate_database(connection: DatabaseConnection) -> None:
    if DB_BACKEND == "sqlite":
        cursor = connection.execute("PRAGMA table_info(test_runs)")
        columns = {row["name"] for row in cursor.fetchall()}
        if "execution_id" not in columns:
            connection.execute("ALTER TABLE test_runs ADD COLUMN execution_id TEXT")

        cursor = connection.execute("PRAGMA table_info(users)")
        user_cols = {row["name"] for row in cursor.fetchall()}
        if "password_hash" not in user_cols:
            connection.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
        if "password_salt" not in user_cols:
            connection.execute("ALTER TABLE users ADD COLUMN password_salt TEXT")


def initialize_database() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    with connect_db() as connection:
        schema = SCHEMA_POSTGRESQL if DB_BACKEND == "postgresql" else SCHEMA_SQLITE
        connection.executescript(schema)
        migrate_database(connection)
        seed_database(connection)


def seed_database(connection: DatabaseConnection) -> None:
    now = utc_now()

    if table_empty(connection, "projects"):
        connection.execute(
            "INSERT INTO projects (name, owner, business_unit, risk_tier, created_at) VALUES (?, ?, ?, ?, ?)",
            ("Checkout Platform", "Performance Engineering", "Digital Commerce", "critical", now),
        )
        connection.execute(
            "INSERT INTO projects (name, owner, business_unit, risk_tier, created_at) VALUES (?, ?, ?, ?, ?)",
            ("Customer API", "Platform Team", "Shared Services", "high", now),
        )
        connection.execute(
            "INSERT INTO projects (name, owner, business_unit, risk_tier, created_at) VALUES (?, ?, ?, ?, ?)",
            ("Claims Portal", "QA Enablement", "Insurance", "medium", now),
        )

    if table_empty(connection, "environments"):
        connection.execute(
            "INSERT INTO environments (name, region, classification, readiness_status, service_virtualization_enabled, data_residency, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("performance", "af-south-1", "production-like", "ready", 1, "ZA", now),
        )
        connection.execute(
            "INSERT INTO environments (name, region, classification, readiness_status, service_virtualization_enabled, data_residency, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("integration", "eu-west-1", "shared", "warning", 1, "EU", now),
        )
        connection.execute(
            "INSERT INTO environments (name, region, classification, readiness_status, service_virtualization_enabled, data_residency, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("resilience-lab", "us-east-1", "isolated", "ready", 0, "US", now),
        )

    if table_empty(connection, "scenarios"):
        connection.execute(
            "INSERT INTO scenarios (project_id, name, engine, test_type, workload_mix, script_repository, target_endpoint, sla_p95_ms, max_error_rate, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "Month-End Checkout Baseline", "JMeter", "load", "70% browse, 20% cart, 10% checkout", "git@example.com:performance/checkout-jmeter.git", "http://localhost:8080", 850, 1.0, now),
        )
        connection.execute(
            "INSERT INTO scenarios (project_id, name, engine, test_type, workload_mix, script_repository, target_endpoint, sla_p95_ms, max_error_rate, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (2, "Customer API CI Performance Smoke", "k6", "smoke", "60% read, 30% search, 10% update", "git@example.com:performance/customer-api-k6.git", "http://localhost:8080", 450, 0.5, now),
        )
        connection.execute(
            "INSERT INTO scenarios (project_id, name, engine, test_type, workload_mix, script_repository, target_endpoint, sla_p95_ms, max_error_rate, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (3, "Claims Browser Journey", "Playwright", "browser", "login, claim lookup, document upload", "git@example.com:performance/claims-browser.git", "http://localhost:8080", 1500, 2.0, now),
        )

    if table_empty(connection, "load_generator_pools"):
        connection.execute(
            "INSERT INTO load_generator_pools (name, region, engines, max_vusers, status, current_reservation, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("af-south-1-general", "af-south-1", to_json(["JMeter", "k6", "Locust", "Gatling"]), 12000, "healthy", 0, now),
        )
        connection.execute(
            "INSERT INTO load_generator_pools (name, region, engines, max_vusers, status, current_reservation, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("eu-west-1-browser", "eu-west-1", to_json(["Playwright", "Gatling", "k6"]), 3000, "healthy", 0, now),
        )
        connection.execute(
            "INSERT INTO load_generator_pools (name, region, engines, max_vusers, status, current_reservation, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("us-east-1-resilience", "us-east-1", to_json(["JMeter", "Gatling", "Locust"]), 7000, "maintenance", 0, now),
        )

    if table_empty(connection, "policies"):
        connection.execute(
            "INSERT INTO policies (name, scope, rule, severity, enabled) VALUES (?, ?, ?, ?, ?)",
            ("Production-like approval", "execution", "Require approval for critical projects above 2,000 virtual users", "blocking", 1),
        )
        connection.execute(
            "INSERT INTO policies (name, scope, rule, severity, enabled) VALUES (?, ?, ?, ?, ?)",
            ("Environment readiness", "execution", "Block execution when environment readiness is not ready", "blocking", 1),
        )
        connection.execute(
            "INSERT INTO policies (name, scope, rule, severity, enabled) VALUES (?, ?, ?, ?, ?)",
            ("Generator capacity", "execution", "Target virtual users must fit an available load generator pool", "blocking", 1),
        )
        connection.execute(
            "INSERT INTO policies (name, scope, rule, severity, enabled) VALUES (?, ?, ?, ?, ?)",
            ("SLA evidence", "result", "Every completed run must produce SLA, trend, and artifact evidence", "warning", 1),
        )
        connection.execute(
            "INSERT INTO policies (name, scope, rule, severity, enabled) VALUES (?, ?, ?, ?, ?)",
            ("Sensitive data masking", "ai", "Mask credentials, tokens, account IDs, and personal data before AI analysis", "blocking", 1),
        )

    if table_empty(connection, "users"):
        from .auth import hash_password, DEFAULT_PASSWORD
        default_pw_hash, default_pw_salt = hash_password(DEFAULT_PASSWORD)
        connection.execute(
            "INSERT INTO users (username, display_name, role, email, password_hash, password_salt, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("admin", "System Administrator", "admin", "admin@marathonrunner.local", default_pw_hash, default_pw_salt, now),
        )
        connection.execute(
            "INSERT INTO users (username, display_name, role, email, password_hash, password_salt, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("lead", "Performance Lead", "performance_lead", "lead@marathonrunner.local", default_pw_hash, default_pw_salt, now),
        )
        connection.execute(
            "INSERT INTO users (username, display_name, role, email, password_hash, password_salt, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("engineer", "Test Engineer", "engineer", "engineer@marathonrunner.local", default_pw_hash, default_pw_salt, now),
        )
        connection.execute(
            "INSERT INTO users (username, display_name, role, email, password_hash, password_salt, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("viewer", "Stakeholder Viewer", "viewer", "viewer@marathonrunner.local", default_pw_hash, default_pw_salt, now),
        )

    if table_empty(connection, "test_runs"):
        import secrets as _secrets
        import random
        random.seed("marathonrunner-seed")

        runs_data = [
            (1, 1, 1, 1, "Month-End Checkout Baseline", "JMeter", "Ramp to 8000 users over 25 min", 8000, 70, "completed", "passed", 22, "Checkout performance baseline"),
            (1, 1, 1, 1, "Checkout Load Test - 500 users", "JMeter", "Ramp to 500 users over 5 min, hold 10 min", 500, 15, "completed", "passed", 18, "Standard load test for checkout"),
            (1, 1, 1, 1, "Checkout Stress Test", "JMeter", "Ramp to 2000 users over 10 min", 2000, 20, "completed", "failed", 85, "Stress test exceeding SLA"),
            (2, 2, 2, 2, "Customer API Smoke Test", "k6", "100 users for 2 minutes", 100, 2, "completed", "passed", 15, "Quick API validation"),
            (2, 2, 2, 2, "Customer API Load Test", "k6", "500 users for 15 minutes", 500, 15, "completed", "passed", 25, "API load test"),
            (2, 2, 2, 2, "Customer API Spike Test", "k6", "Spike to 1000 users for 1 minute", 1000, 5, "completed", "passed", 45, "Spike test for API"),
            (3, 3, 3, 3, "Claims Portal Smoke", "Locust", "50 users for 2 minutes", 50, 2, "completed", "passed", 10, "Claims portal smoke test"),
            (3, 3, 3, 3, "Claims Portal Load", "Locust", "200 users for 10 minutes", 200, 10, "completed", "passed", 30, "Claims portal load test"),
            (1, 1, 1, 1, "Checkout Nightly Regression", "JMeter", "Steady 1000 users for 30 min", 1000, 30, "running", "not_evaluated", 40, "Nightly regression run"),
            (2, 2, 2, 2, "API Performance Baseline", "k6", "Steady 200 users for 20 min", 200, 20, "completed", "passed", 12, "API baseline for comparison"),
            (1, 1, 1, 1, "Checkout Spike Test", "JMeter", "Spike to 5000 users", 5000, 10, "completed", "failed", 92, "Spike test failed SLA"),
            (2, 2, 2, 2, "Customer API Soak Test", "k6", "Steady 300 users for 60 min", 300, 60, "completed", "passed", 28, "Soak test for memory leaks"),
            (3, 3, 3, 3, "Claims Browser Performance", "Locust", "100 users for 15 min", 100, 15, "completed", "passed", 20, "Browser performance test"),
            (1, 1, 1, 1, "Checkout CI Smoke", "JMeter", "Quick 100 users for 1 min", 100, 1, "completed", "passed", 5, "CI pipeline smoke test"),
            (2, 2, 2, 2, "API Stress Test", "k6", "Ramp to 2000 users", 2000, 20, "completed", "failed", 78, "API stress test"),
        ]

        for i, (pid, sid, eid, pool_id, name, engine, profile, vusers, dur, status, gate, risk, summary) in enumerate(runs_data):
            corr = f"mr-{_secrets.token_hex(6)}"
            days_ago = (len(runs_data) - i) * 2
            created = f"2026-06-{28 + i:02d}T10:00:00+00:00"
            completed = created if status == "completed" else None
            started = created if status in ("completed", "running") else None

            connection.execute(
                """INSERT INTO test_runs
                (project_id, scenario_id, environment_id, pool_id, name, engine, load_profile,
                 target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id,
                 ai_summary, created_at, started_at, completed_at, is_baseline)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (pid, sid, eid, pool_id, name, engine, profile, vusers, dur,
                 status, gate, risk, corr, summary, created, started, completed,
                 1 if i == 0 and status == "completed" else 0),
            )
            run_id = i + 1

            if status == "completed":
                p95 = random.randint(100, 800)
                p50 = int(p95 * 0.6)
                p99 = int(p95 * 1.3)
                err = round(random.uniform(0.0, 1.5), 2)
                rps = round(random.uniform(5.0, 50.0), 2)
                apdex = round(max(0.5, min(0.99, 1.0 - (p95 / max(850, 1) - 0.7) * 0.25 - err * 0.04)), 2)
                cpu = round(random.uniform(55.0, 90.0), 1)
                mem = round(random.uniform(45.0, 85.0), 1)
                redis_lat = random.randint(1, 20)
                db_cpu = round(random.uniform(40.0, 85.0), 1)

                connection.execute(
                    """INSERT INTO run_results
                    (run_id, p50_ms, p95_ms, p99_ms, throughput_rps, error_rate, apdex, cpu_peak,
                     memory_peak, redis_latency_ms, db_cpu_peak, artifact_path, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (run_id, p50, p95, p99, rps, err, apdex, cpu, mem, redis_lat, db_cpu,
                     f"data/artifacts/run-{run_id}-result.json", created),
                )

    connection.commit()
