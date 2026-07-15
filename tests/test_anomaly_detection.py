from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def anomaly_db(tmp_path):
    """Create a test database with sample data for anomaly detection."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Create tables
    conn.executescript("""
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            owner TEXT NOT NULL,
            business_unit TEXT NOT NULL,
            risk_tier TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE environments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            region TEXT NOT NULL,
            classification TEXT NOT NULL,
            readiness_status TEXT NOT NULL,
            service_virtualization_enabled INTEGER NOT NULL,
            data_residency TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE scenarios (
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
            created_at TEXT NOT NULL
        );
        CREATE TABLE load_generator_pools (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            region TEXT NOT NULL,
            engines TEXT NOT NULL,
            max_vusers INTEGER NOT NULL,
            status TEXT NOT NULL,
            current_reservation INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE test_runs (
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
            completed_at TEXT
        );
        CREATE TABLE run_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL UNIQUE,
            p50_ms INTEGER NOT NULL,
            p95_ms INTEGER NOT NULL,
            p99_ms INTEGER NOT NULL,
            throughput_rps REAL NOT NULL,
            error_rate REAL NOT NULL,
            apdex REAL NOT NULL DEFAULT 1.0,
            cpu_peak REAL NOT NULL DEFAULT 0.0,
            memory_peak REAL NOT NULL DEFAULT 0.0,
            redis_latency_ms INTEGER NOT NULL DEFAULT 0,
            db_cpu_peak REAL NOT NULL DEFAULT 0.0,
            artifact_path TEXT,
            created_at TEXT NOT NULL
        );
    """)

    # Insert test data
    conn.execute(
        "INSERT INTO projects (name, owner, business_unit, risk_tier, created_at) VALUES (?, ?, ?, ?, ?)",
        ("Test Project", "admin", "BU", "low", "2025-01-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO environments (name, region, classification, readiness_status, service_virtualization_enabled, data_residency, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("dev", "us-east-1", "internal", "ready", 0, "US", "2025-01-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO scenarios (project_id, name, engine, test_type, workload_mix, script_repository, target_endpoint, sla_p95_ms, max_error_rate, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (1, "Test Scenario", "k6", "load", "mixed", "repo", "http://localhost", 500, 0.05, "2025-01-01T00:00:00Z"),
    )

    # Insert runs with results
    for i in range(5):
        conn.execute(
            """INSERT INTO test_runs (project_id, scenario_id, environment_id, name, engine, load_profile,
               target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (1, 1, 1, f"Run {i}", "k6", "constant", 100, 5, "completed", "pass", 50, f"corr-{i}", "summary", f"2025-01-0{i+1}T00:00:00Z"),
        )
        # Normal results for first 3 runs
        if i < 3:
            conn.execute(
                "INSERT INTO run_results (run_id, p50_ms, p95_ms, p99_ms, throughput_rps, error_rate, apdex, cpu_peak, memory_peak, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (i + 1, 100, 200, 300, 1000.0, 0.01, 0.95, 45.0, 60.0, f"2025-01-0{i+1}T00:00:00Z"),
            )
        # Anomalous results for last 2 runs
        else:
            conn.execute(
                "INSERT INTO run_results (run_id, p50_ms, p95_ms, p99_ms, throughput_rps, error_rate, apdex, cpu_peak, memory_peak, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (i + 1, 500, 2000, 5000, 200.0, 0.15, 0.6, 85.0, 90.0, f"2025-01-0{i+1}T00:00:00Z"),
            )

    conn.commit()
    yield conn
    conn.close()


class TestAnomalyDetection:
    """Tests for AI-Assisted Anomaly Detection."""

    def test_detect_anomalies_normal_run(self, anomaly_db, monkeypatch):
        from apps.api.models import detect_anomalies
        monkeypatch.setattr("apps.api.models.connect_db", lambda: anomaly_db)
        result = detect_anomalies(1)
        assert result["run_id"] == 1
        assert "health_score" in result
        assert "anomalies" in result
        assert isinstance(result["anomalies"], list)

    def test_detect_anomalies_anomalous_run(self, anomaly_db, monkeypatch):
        from apps.api.models import detect_anomalies
        monkeypatch.setattr("apps.api.models.connect_db", lambda: anomaly_db)
        result = detect_anomalies(4)
        assert result["run_id"] == 4
        assert result["anomaly_count"] > 0
        assert len(result["anomalies"]) > 0

    def test_detect_anomalies_run_not_found(self, anomaly_db, monkeypatch):
        from apps.api.models import detect_anomalies
        monkeypatch.setattr("apps.api.models.connect_db", lambda: anomaly_db)
        with pytest.raises(ValueError, match="Run not found"):
            detect_anomalies(999)

    def test_detect_anomalies_no_results(self, anomaly_db, monkeypatch):
        from apps.api.models import detect_anomalies
        # Run 5 has no results in our fixture
        anomaly_db.execute(
            """INSERT INTO test_runs (project_id, scenario_id, environment_id, name, engine, load_profile,
               target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (1, 1, 1, "No Results", "k6", "constant", 100, 5, "completed", "pass", 50, "corr-no", "summary", "2025-01-01T00:00:00Z"),
        )
        anomaly_db.commit()
        monkeypatch.setattr("apps.api.models.connect_db", lambda: anomaly_db)
        result = detect_anomalies(6)
        assert "error" in result

    def test_detect_trend_anomalies(self, anomaly_db, monkeypatch):
        from apps.api.models import detect_trend_anomalies
        monkeypatch.setattr("apps.api.models.connect_db", lambda: anomaly_db)
        result = detect_trend_anomalies()
        assert "trends" in result
        assert isinstance(result["trends"], list)

    def test_detect_trend_anomalies_with_project(self, anomaly_db, monkeypatch):
        from apps.api.models import detect_trend_anomalies
        monkeypatch.setattr("apps.api.models.connect_db", lambda: anomaly_db)
        result = detect_trend_anomalies(project_id=1)
        assert "trends" in result

    def test_get_anomaly_summary(self, anomaly_db, monkeypatch):
        from apps.api.models import get_anomaly_summary
        # Each call to detect_anomalies closes its connection, so we need fresh connections
        db_path = str(anomaly_db.execute("PRAGMA database_list").fetchone()[2])

        def fresh_connect():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn

        monkeypatch.setattr("apps.api.models.connect_db", fresh_connect)
        result = get_anomaly_summary()
        assert "total_runs_analyzed" in result
        assert "total_anomalies" in result
        assert "average_health_score" in result
        assert "overall_status" in result

    def test_anomaly_types_detected(self, anomaly_db, monkeypatch):
        from apps.api.models import detect_anomalies
        monkeypatch.setattr("apps.api.models.connect_db", lambda: anomaly_db)
        result = detect_anomalies(4)
        anomaly_types = [a["type"] for a in result["anomalies"]]
        # Should detect high error rate and SLA breach
        assert "high_error_rate" in anomaly_types or "sla_breach" in anomaly_types

    def test_health_score_calculation(self, anomaly_db, monkeypatch):
        from apps.api.models import detect_anomalies
        # Each call to detect_anomalies closes its connection, so we need fresh connections
        db_path = str(anomaly_db.execute("PRAGMA database_list").fetchone()[2])

        def fresh_connect():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn

        monkeypatch.setattr("apps.api.models.connect_db", fresh_connect)
        # Normal run should have high health score
        result_normal = detect_anomalies(1)
        assert result_normal["health_score"] >= 80

        # Anomalous run should have lower health score
        result_anomalous = detect_anomalies(4)
        assert result_anomalous["health_score"] < 80
