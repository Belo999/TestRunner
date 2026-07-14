from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from apps.api.database import (
    DB_PATH,
    DatabaseConnection,
    adapt_query,
    from_json,
    release_pool_reservation_sql,
    rows_to_dicts,
    table_empty,
    to_json,
    upsert_run_result_sql,
    utc_now,
)


class TestAdaptQuery:
    def test_sqlite_keeps_question_marks(self):
        q = "SELECT * FROM users WHERE id = ?"
        assert adapt_query(q, "sqlite") == q

    def test_postgresql_replaces_question_marks(self):
        q = "SELECT * FROM users WHERE id = ? AND name = ?"
        result = adapt_query(q, "postgresql")
        assert result == "SELECT * FROM users WHERE id = %s AND name = %s"


class TestUpsertRunResultSql:
    def test_sqlite_version(self):
        sql = release_pool_reservation_sql("sqlite")
        assert "MAX(0," in sql
        assert "?" in sql

    def test_postgresql_version(self):
        sql = release_pool_reservation_sql("postgresql")
        assert "GREATEST(0," in sql
        assert "%s" in sql


class TestJsonHelpers:
    def test_to_json(self):
        result = to_json({"b": 2, "a": 1})
        assert result == '{"a":1,"b":2}'

    def test_from_json_valid(self):
        assert from_json('{"key": "value"}', {}) == {"key": "value"}

    def test_from_json_none_returns_default(self):
        assert from_json(None, {"default": True}) == {"default": True}

    def test_from_json_empty_string_returns_default(self):
        assert from_json("", []) == []

    def test_from_json_non_string_passthrough(self):
        data = [1, 2, 3]
        assert from_json(data, []) == data


class TestUtcNow:
    def test_returns_iso_format(self):
        result = utc_now()
        assert "T" in result
        assert result.endswith("+00:00")

    def test_no_microseconds(self):
        result = utc_now()
        assert "." not in result


class TestRowsToDicts:
    def test_with_sqlite_rows(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'alice')")
        conn.execute("INSERT INTO t VALUES (2, 'bob')")
        rows = conn.execute("SELECT * FROM t").fetchall()
        result = rows_to_dicts(rows)
        assert result == [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}]
        conn.close()

    def test_with_dicts(self):
        result = rows_to_dicts([{"a": 1}, {"a": 2}])
        assert result == [{"a": 1}, {"a": 2}]


class TestSchemaCreation:
    def test_creates_all_tables(self, tmp_db: sqlite3.Connection):
        tables = [
            row[0] for row in tmp_db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        expected = [
            "approvals", "ai_insights", "applications", "audit_events",
            "environments", "execution_windows", "load_generator_pools",
            "notifications", "policies", "projects", "run_results",
            "schedules", "scenarios", "test_runs", "users", "webhooks",
        ]
        for table in expected:
            assert table in tables, f"Table {table} not found"

    def test_insert_and_query_project(self, tmp_db: sqlite3.Connection):
        tmp_db.execute(
            "INSERT INTO projects (name, owner, business_unit, risk_tier, created_at) VALUES (?, ?, ?, ?, ?)",
            ("Test Project", "admin", "Engineering", "high", utc_now()),
        )
        tmp_db.commit()
        row = tmp_db.execute("SELECT * FROM projects WHERE name = 'Test Project'").fetchone()
        assert row is not None
        assert row["owner"] == "admin"

    def test_foreign_key_constraint(self, tmp_db: sqlite3.Connection):
        # Insert a project first
        tmp_db.execute(
            "INSERT INTO projects (name, owner, business_unit, risk_tier, created_at) VALUES (?, ?, ?, ?, ?)",
            ("P1", "admin", "BU", "low", utc_now()),
        )
        tmp_db.commit()

        # Insert a scenario referencing the project
        tmp_db.execute(
            "INSERT INTO scenarios (project_id, name, engine, test_type, workload_mix, script_repository, target_endpoint, sla_p95_ms, max_error_rate, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "S1", "k6", "load", "mixed", "repo", "http://localhost", 500, 1.0, utc_now()),
        )
        tmp_db.commit()
        row = tmp_db.execute("SELECT * FROM scenarios WHERE name = 'S1'").fetchone()
        assert row is not None

    def test_insert_run_and_result(self, tmp_db: sqlite3.Connection):
        # Setup prerequisite data
        tmp_db.execute(
            "INSERT INTO projects (name, owner, business_unit, risk_tier, created_at) VALUES (?, ?, ?, ?, ?)",
            ("P1", "admin", "BU", "low", utc_now()),
        )
        tmp_db.execute(
            "INSERT INTO environments (name, region, classification, readiness_status, service_virtualization_enabled, data_residency, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("dev", "us-east-1", "internal", "ready", 0, "US", utc_now()),
        )
        tmp_db.execute(
            "INSERT INTO scenarios (project_id, name, engine, test_type, workload_mix, script_repository, target_endpoint, sla_p95_ms, max_error_rate, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "S1", "k6", "load", "mixed", "repo", "http://localhost", 500, 1.0, utc_now()),
        )
        tmp_db.commit()

        # Insert a run
        tmp_db.execute(
            "INSERT INTO test_runs (project_id, scenario_id, environment_id, name, engine, load_profile, target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, 1, 1, "Run 1", "k6", "constant", 100, 5, "completed", "pass", 85, "corr-123", "summary", utc_now()),
        )
        tmp_db.commit()

        run_id = tmp_db.execute("SELECT id FROM test_runs WHERE name = 'Run 1'").fetchone()["id"]

        # Insert result
        tmp_db.execute(
            "INSERT INTO run_results (run_id, p50_ms, p95_ms, p99_ms, throughput_rps, error_rate, apdex, cpu_peak, memory_peak, redis_latency_ms, db_cpu_peak, artifact_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, 100, 300, 500, 25.0, 0.5, 0.95, 60.0, 70.0, 2, 30.0, "s3://bucket/path", utc_now()),
        )
        tmp_db.commit()

        result = tmp_db.execute("SELECT * FROM run_results WHERE run_id = ?", (run_id,)).fetchone()
        assert result is not None
        assert result["p50_ms"] == 100
        assert result["apdex"] == 0.95


class TestUpsertRunResultSql:
    def test_sqlite_version(self):
        sql = upsert_run_result_sql("sqlite")
        assert "INSERT OR REPLACE" in sql
        assert "?" in sql

    def test_postgresql_version(self):
        sql = upsert_run_result_sql("postgresql")
        assert "ON CONFLICT" in sql
        assert "%s" in sql

    def test_default_backend(self):
        sql = upsert_run_result_sql()
        assert "INSERT OR REPLACE" in sql


class TestDatabaseConnection:
    def test_context_manager(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        db = DatabaseConnection(conn, "sqlite")
        with db as ctx:
            ctx.execute("CREATE TABLE t (id INTEGER)")
            ctx.execute("INSERT INTO t VALUES (1)")
            ctx.commit()
        # After context exit, the wrapper is closed (but raw sqlite allows some ops)
        # Verify the wrapper's close was called by checking we can't use the wrapper
        with pytest.raises(Exception):
            db.execute("SELECT 1")

    def test_execute_sqlite(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        db = DatabaseConnection(conn, "sqlite")
        db.execute("CREATE TABLE t (id INTEGER, name TEXT)")
        db.execute("INSERT INTO t VALUES (1, 'alice')")
        db.commit()
        row = db.execute("SELECT * FROM t WHERE id = 1").fetchone()
        assert row["name"] == "alice"
        conn.close()

    def test_executescript(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        db = DatabaseConnection(conn, "sqlite")
        db.executescript("CREATE TABLE t (id INTEGER); INSERT INTO t VALUES (1); INSERT INTO t VALUES (2);")
        rows = db.execute("SELECT * FROM t").fetchall()
        assert len(rows) == 2
        conn.close()

    def test_table_empty_true(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.commit()
        db = DatabaseConnection(conn, "sqlite")
        assert table_empty(db, "t") is True
        conn.close()

    def test_table_empty_false(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        db = DatabaseConnection(conn, "sqlite")
        assert table_empty(db, "t") is False
        conn.close()


class TestPgResultWrapper:
    def _make_wrapper(self, rows, description):
        cursor = MagicMock()
        cursor.fetchone.side_effect = rows
        cursor.fetchall.return_value = rows
        cursor.description = description
        return cursor

    def test_fetchone(self):
        from apps.api.database import PgResultWrapper
        desc = [("id",), ("name",)]
        cursor = self._make_wrapper([(1, "alice")], desc)
        wrapper = PgResultWrapper(cursor)
        row = wrapper.fetchone()
        assert row["id"] == 1
        assert row["name"] == "alice"

    def test_fetchone_none(self):
        from apps.api.database import PgResultWrapper
        cursor = self._make_wrapper([None], [("id",)])
        wrapper = PgResultWrapper(cursor)
        assert wrapper.fetchone() is None

    def test_fetchall(self):
        from apps.api.database import PgResultWrapper
        desc = [("id",), ("name",)]
        cursor = self._make_wrapper([(1, "alice"), (2, "bob")], desc)
        wrapper = PgResultWrapper(cursor)
        rows = wrapper.fetchall()
        assert len(rows) == 2
        assert rows[0]["id"] == 1

    def test_lastrowid(self):
        from apps.api.database import PgResultWrapper
        cursor = MagicMock()
        cursor.lastrowid = 42
        cursor.description = None
        wrapper = PgResultWrapper(cursor)
        assert wrapper.lastrowid == 42

    def test_lastrowid_none(self):
        from apps.api.database import PgResultWrapper
        cursor = MagicMock()
        cursor.lastrowid = None
        cursor.description = None
        wrapper = PgResultWrapper(cursor)
        assert wrapper.lastrowid is None


class TestPgRowWrapper:
    def test_getitem(self):
        from apps.api.database import PgRowWrapper
        desc = [("id",), ("name",)]
        row = PgRowWrapper((1, "alice"), desc)
        assert row["id"] == 1
        assert row["name"] == "alice"

    def test_getitem_missing_key(self):
        from apps.api.database import PgRowWrapper
        desc = [("id",)]
        row = PgRowWrapper((1,), desc)
        with pytest.raises(KeyError):
            _ = row["nonexistent"]

    def test_contains(self):
        from apps.api.database import PgRowWrapper
        desc = [("id",), ("name",)]
        row = PgRowWrapper((1, "alice"), desc)
        assert "id" in row
        assert "nonexistent" not in row

    def test_keys(self):
        from apps.api.database import PgRowWrapper
        desc = [("id",), ("name",)]
        row = PgRowWrapper((1, "alice"), desc)
        assert row.keys() == ["id", "name"]

    def test_iter(self):
        from apps.api.database import PgRowWrapper
        desc = [("id",), ("name",)]
        row = PgRowWrapper((1, "alice"), desc)
        assert list(row) == [1, "alice"]

    def test_len(self):
        from apps.api.database import PgRowWrapper
        desc = [("id",), ("name",)]
        row = PgRowWrapper((1, "alice"), desc)
        assert len(row) == 2

    def test_empty_description(self):
        from apps.api.database import PgRowWrapper
        row = PgRowWrapper((1,), None)
        assert row.keys() == []


class TestInitializeDatabase:
    def test_creates_schema_and_seeds(self, tmp_path, monkeypatch):
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("MARATHONRUNNER_DB_PATH", str(db_path))
        monkeypatch.setattr("apps.api.database.DB_PATH", db_path)
        monkeypatch.setattr("apps.api.database.ARTIFACT_DIR", tmp_path / "artifacts")
        monkeypatch.setattr("apps.api.database.SCRIPT_DIR", tmp_path / "scripts")

        from apps.api.database import initialize_database
        initialize_database()

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # Verify tables exist
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "projects" in tables
        assert "users" in tables

        # Verify seed data
        projects = conn.execute("SELECT COUNT(*) as cnt FROM projects").fetchone()["cnt"]
        assert projects >= 3
        users = conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()["cnt"]
        assert users >= 4
        runs = conn.execute("SELECT COUNT(*) as cnt FROM test_runs").fetchone()["cnt"]
        assert runs >= 15
        conn.close()

    def test_idempotent(self, tmp_path, monkeypatch):
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("MARATHONRUNNER_DB_PATH", str(db_path))
        monkeypatch.setattr("apps.api.database.DB_PATH", db_path)
        monkeypatch.setattr("apps.api.database.ARTIFACT_DIR", tmp_path / "artifacts")
        monkeypatch.setattr("apps.api.database.SCRIPT_DIR", tmp_path / "scripts")

        from apps.api.database import initialize_database
        initialize_database()
        initialize_database()  # Should not fail

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        projects = conn.execute("SELECT COUNT(*) as cnt FROM projects").fetchone()["cnt"]
        assert projects == 3  # Not duplicated
        conn.close()


class TestConnectSqlite:
    def test_creates_directory(self, tmp_path, monkeypatch):
        db_path = tmp_path / "subdir" / "test.db"
        monkeypatch.setattr("apps.api.database.DB_PATH", db_path)
        from apps.api.database import connect_sqlite
        db = connect_sqlite()
        db.execute("CREATE TABLE t (id INTEGER)")
        db.commit()
        db.close()
        assert db_path.exists()


class TestConnectPostgresql:
    def test_import_error(self, monkeypatch):
        import sys
        # Temporarily make psycopg2 unavailable
        old = sys.modules.pop("psycopg2", None)
        sys.modules["psycopg2"] = None
        try:
            from apps.api.database import connect_postgresql
            with pytest.raises(ImportError, match="psycopg2"):
                connect_postgresql()
        finally:
            if old is not None:
                sys.modules["psycopg2"] = old
            else:
                sys.modules.pop("psycopg2", None)


class TestPgDatabaseConnection:
    def test_execute_postgresql(self):
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_cursor.fetchone.return_value = None
        mock_cursor.description = None
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        db = DatabaseConnection(mock_conn, "postgresql")
        db.execute("SELECT * FROM t WHERE id = %s", (1,))
        mock_cursor.execute.assert_called_once()

    def test_executescript_postgresql(self):
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        db = DatabaseConnection(mock_conn, "postgresql")
        db.executescript("CREATE TABLE t (id INTEGER); INSERT INTO t VALUES (1);")
        assert mock_cursor.execute.call_count == 2
        mock_conn.commit.assert_called_once()

    def test_lastrowid_none(self):
        from apps.api.database import PgResultWrapper
        cursor = MagicMock()
        cursor.lastrowid = None
        cursor.description = None
        wrapper = PgResultWrapper(cursor)
        assert wrapper.lastrowid is None


class TestMigrateDatabasePostgresql:
    def test_skips_on_postgresql(self):
        from apps.api.database import migrate_database, DatabaseConnection
        mock_conn = MagicMock()
        db = DatabaseConnection(mock_conn, "postgresql")
        migrate_database(db)  # Should not raise, just skip PRAGMA checks
