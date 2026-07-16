from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def otel_db(tmp_path):
    """Create a test database for OpenTelemetry tests."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

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
        CREATE TABLE audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id INTEGER,
            details TEXT,
            created_at TEXT NOT NULL
        );
    """)

    conn.execute(
        "INSERT INTO projects (name, owner, business_unit, risk_tier, created_at) VALUES (?, ?, ?, ?, ?)",
        ("Test Project", "admin", "BU", "low", "2025-01-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO test_runs (project_id, scenario_id, environment_id, name, engine, load_profile, target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (1, 1, 1, "Test Run", "k6", "constant", 100, 5, "completed", "pass", 50, "corr-1", "summary", "2025-01-01T00:00:00Z"),
    )

    conn.commit()
    yield conn
    conn.close()


class TestTraceContext:
    """Tests for trace context generation."""

    def test_generate_trace_context(self, otel_db, monkeypatch):
        from apps.api.models import generate_trace_context
        ctx = generate_trace_context(1)
        assert "traceId" in ctx
        assert "spanId" in ctx
        assert ctx["runId"] == 1
        assert ctx["service"] == "marathonrunner"

    def test_trace_context_format(self, otel_db, monkeypatch):
        from apps.api.models import generate_trace_context
        ctx = generate_trace_context(1)
        assert len(ctx["traceId"]) == 32  # UUID hex
        assert len(ctx["spanId"]) == 16  # First 16 chars


class TestCorrelationRecord:
    """Tests for correlation record creation."""

    def test_create_correlation_record(self, otel_db, monkeypatch):
        from apps.api.models import create_correlation_record, generate_trace_context
        monkeypatch.setattr("apps.api.models.connect_db", lambda: otel_db)
        ctx = generate_trace_context(1)
        result = create_correlation_record(1, ctx)
        assert result["runId"] == 1
        assert result["traceId"] == ctx["traceId"]
        assert result["status"] == "active"


class TestTraceLookup:
    """Tests for trace lookup functions."""

    def test_get_run_traces(self, otel_db, monkeypatch):
        from apps.api.models import get_run_traces, propagate_trace_headers
        monkeypatch.setattr("apps.api.models.connect_db", lambda: otel_db)
        # Create a trace first
        propagate_trace_headers(1)
        result = get_run_traces(1)
        assert result["runId"] == 1
        assert result["traceCount"] >= 1

    def test_get_correlation_by_trace(self, otel_db, monkeypatch):
        from apps.api.models import get_correlation_by_trace, propagate_trace_headers
        monkeypatch.setattr("apps.api.models.connect_db", lambda: otel_db)
        # Create a trace
        headers = propagate_trace_headers(1)
        trace_id = headers["x-correlation-id"]
        runs = get_correlation_by_trace(trace_id)
        assert len(runs) >= 1
        assert runs[0]["runId"] == 1


class TestTracePropagation:
    """Tests for W3C trace context propagation."""

    def test_propagate_trace_headers(self, otel_db, monkeypatch):
        from apps.api.models import propagate_trace_headers
        monkeypatch.setattr("apps.api.models.connect_db", lambda: otel_db)
        headers = propagate_trace_headers(1)
        assert "traceparent" in headers
        assert "tracestate" in headers
        assert "x-correlation-id" in headers
        assert "x-run-id" in headers

    def test_traceparent_format(self, otel_db, monkeypatch):
        from apps.api.models import propagate_trace_headers
        monkeypatch.setattr("apps.api.models.connect_db", lambda: otel_db)
        headers = propagate_trace_headers(1)
        parts = headers["traceparent"].split("-")
        assert len(parts) == 4  # version-traceId-spanId-traceFlags
        assert parts[0] == "00"  # version


class TestTraceSummary:
    """Tests for trace summary."""

    def test_get_trace_summary(self, otel_db, monkeypatch):
        from apps.api.models import get_trace_summary, propagate_trace_headers
        monkeypatch.setattr("apps.api.models.connect_db", lambda: otel_db)
        # Create some traces
        propagate_trace_headers(1)
        propagate_trace_headers(1)
        summary = get_trace_summary()
        assert "tracedRuns" in summary
        assert "totalCompletedRuns" in summary
        assert "coveragePercent" in summary
        assert summary["tracedRuns"] >= 1
