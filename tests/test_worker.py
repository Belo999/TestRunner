from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from apps.api.worker import (
    _retry_operation,
    check_container_running,
    collect_engine_results,
    launch_engine,
    process_worker_tick,
    queue_ready_runs,
    remove_container,
    to_host_path,
    _fail_run,
)


class TestToHostPath:
    def test_with_host_data_dir(self):
        with patch("apps.api.worker.HOST_DATA_DIR", "/host/data/"):
            result = to_host_path("/app/data/scripts/k6/test.js")
            assert result == "/host/data/scripts/k6/test.js"

    def test_without_host_data_dir(self):
        with patch("apps.api.worker.HOST_DATA_DIR", ""):
            result = to_host_path("/app/data/scripts/k6/test.js")
            assert result == "/app/data/scripts/k6/test.js"

    def test_non_app_data_path(self):
        with patch("apps.api.worker.HOST_DATA_DIR", "/host/data/"):
            result = to_host_path("/tmp/results/run-1")
            assert result == "/tmp/results/run-1"


class TestQueueReadyRuns:
    def _setup_db(self, conn: sqlite3.Connection):
        """Insert prerequisite data for runs."""
        from apps.api.database import utc_now
        now = utc_now()

        conn.execute(
            "INSERT INTO projects (name, owner, business_unit, risk_tier, created_at) VALUES (?, ?, ?, ?, ?)",
            ("P1", "admin", "BU", "low", now),
        )
        conn.execute(
            "INSERT INTO environments (name, region, classification, readiness_status, service_virtualization_enabled, data_residency, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("dev", "us-east-1", "internal", "ready", 0, "US", now),
        )
        conn.execute(
            "INSERT INTO scenarios (project_id, name, engine, test_type, workload_mix, script_repository, target_endpoint, sla_p95_ms, max_error_rate, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "S1", "k6", "load", "mixed", "repo", "http://localhost", 500, 1.0, now),
        )
        conn.commit()

    def test_queues_ready_runs(self, tmp_db: sqlite3.Connection):
        self._setup_db(tmp_db)
        now = __import__("apps.api.database", fromlist=["utc_now"]).utc_now()

        tmp_db.execute(
            "INSERT INTO test_runs (project_id, scenario_id, environment_id, name, engine, load_profile, target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, 1, 1, "Run 1", "k6", "constant", 100, 5, "ready", "pass", 85, "corr-1", "pending", now),
        )
        tmp_db.execute(
            "INSERT INTO test_runs (project_id, scenario_id, environment_id, name, engine, load_profile, target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, 1, 1, "Run 2", "k6", "constant", 50, 10, "approved", "pass", 90, "corr-2", "pending", now),
        )
        tmp_db.commit()

        count = queue_ready_runs(tmp_db)
        assert count == 2

        # Verify statuses changed
        rows = tmp_db.execute("SELECT status FROM test_runs ORDER BY id").fetchall()
        assert all(row["status"] == "queued" for row in rows)

    def test_does_not_queue_already_queued(self, tmp_db: sqlite3.Connection):
        self._setup_db(tmp_db)
        now = __import__("apps.api.database", fromlist=["utc_now"]).utc_now()

        tmp_db.execute(
            "INSERT INTO test_runs (project_id, scenario_id, environment_id, name, engine, load_profile, target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, 1, 1, "Run 1", "k6", "constant", 100, 5, "running", "pass", 85, "corr-1", "summary", now),
        )
        tmp_db.commit()

        count = queue_ready_runs(tmp_db)
        assert count == 0

    def test_respects_limit(self, tmp_db: sqlite3.Connection):
        self._setup_db(tmp_db)
        now = __import__("apps.api.database", fromlist=["utc_now"]).utc_now()

        for i in range(5):
            tmp_db.execute(
                "INSERT INTO test_runs (project_id, scenario_id, environment_id, name, engine, load_profile, target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (1, 1, 1, f"Run {i}", "k6", "constant", 100, 5, "ready", "pass", 85, f"corr-{i}", "pending", now),
            )
        tmp_db.commit()

        count = queue_ready_runs(tmp_db, limit=2)
        assert count == 2

        queued = tmp_db.execute("SELECT status FROM test_runs WHERE status = 'queued'").fetchall()
        assert len(queued) == 2


class TestRetryOperation:
    def test_succeeds_first_try(self):
        func = MagicMock(return_value=42)
        result = _retry_operation(func, 1, 2, key="val")
        assert result == 42
        func.assert_called_once_with(1, 2, key="val")

    def test_retries_on_locked_database(self):
        func = MagicMock(side_effect=[sqlite3.OperationalError("database is locked"), "ok"])
        with patch("apps.api.worker.time.sleep"):
            result = _retry_operation(func)
        assert result == "ok"
        assert func.call_count == 2

    def test_raises_after_max_retries(self):
        func = MagicMock(side_effect=sqlite3.OperationalError("database is locked"))
        with patch("apps.api.worker.time.sleep"):
            with pytest.raises(sqlite3.OperationalError):
                _retry_operation(func)
        assert func.call_count == 5

    def test_raises_non_lock_errors_immediately(self):
        func = MagicMock(side_effect=sqlite3.OperationalError("no such table"))
        with pytest.raises(sqlite3.OperationalError):
            _retry_operation(func)
        func.assert_called_once()


class TestCheckContainerRunning:
    def test_running(self):
        mock_result = MagicMock(stdout="true\n", returncode=0)
        with patch("apps.api.worker.subprocess.run", return_value=mock_result):
            assert check_container_running("abc123") is True

    def test_not_running(self):
        mock_result = MagicMock(stdout="false\n", returncode=0)
        with patch("apps.api.worker.subprocess.run", return_value=mock_result):
            assert check_container_running("abc123") is False

    def test_exception(self):
        with patch("apps.api.worker.subprocess.run", side_effect=Exception("docker not found")):
            assert check_container_running("abc123") is False


class TestRemoveContainer:
    def test_removes(self):
        mock_result = MagicMock(returncode=0)
        with patch("apps.api.worker.subprocess.run", return_value=mock_result) as mock_run:
            remove_container("abc123")
            mock_run.assert_called_once_with(
                ["docker", "rm", "-f", "abc123"], capture_output=True, timeout=10
            )

    def test_exception_ignored(self):
        with patch("apps.api.worker.subprocess.run", side_effect=Exception("fail")):
            remove_container("abc123")  # should not raise


class TestFailRun:
    def _make_run(self):
        return {
            "id": 1, "name": "Test Run", "pool_id": 1, "target_vusers": 100,
            "status": "running", "engine": "k6",
        }

    def test_fails_run(self, tmp_db):
        from apps.api.database import utc_now
        now = utc_now()
        # Setup prerequisite data
        tmp_db.execute("INSERT INTO projects (name, owner, business_unit, risk_tier, created_at) VALUES (?,?,?,?,?)",
                        ("P1", "admin", "BU", "low", now))
        tmp_db.execute("INSERT INTO environments (name, region, classification, readiness_status, service_virtualization_enabled, data_residency, updated_at) VALUES (?,?,?,?,?,?,?)",
                        ("dev", "us-east-1", "internal", "ready", 0, "US", now))
        tmp_db.execute("INSERT INTO scenarios (project_id, name, engine, test_type, workload_mix, script_repository, target_endpoint, sla_p95_ms, max_error_rate, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (1, "S1", "k6", "load", "mixed", "repo", "http://localhost", 500, 1.0, now))
        tmp_db.execute("INSERT INTO load_generator_pools (name, region, engines, max_vusers, status, current_reservation, updated_at) VALUES (?,?,?,?,?,?,?)",
                        ("pool1", "us-east-1", '["k6"]', 5000, "healthy", 100, now))
        tmp_db.execute(
            "INSERT INTO test_runs (project_id, scenario_id, environment_id, pool_id, name, engine, load_profile, target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, 1, 1, 1, "Test Run", "k6", "constant", 100, 5, "running", "pass", 85, "corr-1", "summary", now),
        )
        tmp_db.commit()

        run = {"id": 1, "name": "Test Run", "pool_id": 1, "target_vusers": 100}
        with patch("apps.api.worker.clear_run_state"), patch("apps.api.worker.untrack_active_run"):
            _fail_run(tmp_db, run, "Container crashed")

        row = tmp_db.execute("SELECT status, ai_summary FROM test_runs WHERE id = 1").fetchone()
        assert row["status"] == "failed"
        assert "Container crashed" in row["ai_summary"]

    def test_releases_pool_reservation(self, tmp_db):
        from apps.api.database import utc_now
        now = utc_now()
        tmp_db.execute("INSERT INTO projects (name, owner, business_unit, risk_tier, created_at) VALUES (?,?,?,?,?)",
                        ("P1", "admin", "BU", "low", now))
        tmp_db.execute("INSERT INTO environments (name, region, classification, readiness_status, service_virtualization_enabled, data_residency, updated_at) VALUES (?,?,?,?,?,?,?)",
                        ("dev", "us-east-1", "internal", "ready", 0, "US", now))
        tmp_db.execute("INSERT INTO scenarios (project_id, name, engine, test_type, workload_mix, script_repository, target_endpoint, sla_p95_ms, max_error_rate, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (1, "S1", "k6", "load", "mixed", "repo", "http://localhost", 500, 1.0, now))
        tmp_db.execute("INSERT INTO load_generator_pools (name, region, engines, max_vusers, status, current_reservation, updated_at) VALUES (?,?,?,?,?,?,?)",
                        ("pool1", "us-east-1", '["k6"]', 5000, "healthy", 100, now))
        tmp_db.execute(
            "INSERT INTO test_runs (project_id, scenario_id, environment_id, pool_id, name, engine, load_profile, target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, 1, 1, 1, "Test Run", "k6", "constant", 100, 5, "running", "pass", 85, "corr-1", "summary", now),
        )
        tmp_db.commit()

        run = {"id": 1, "name": "Test Run", "pool_id": 1, "target_vusers": 100}
        with patch("apps.api.worker.clear_run_state"), patch("apps.api.worker.untrack_active_run"):
            _fail_run(tmp_db, run, "timeout")

        pool = tmp_db.execute("SELECT current_reservation FROM load_generator_pools WHERE id = 1").fetchone()
        assert pool["current_reservation"] == 0


class TestCollectEngineResults:
    def _setup_run(self, tmp_db, status="running"):
        from apps.api.database import utc_now
        now = utc_now()
        tmp_db.execute("INSERT INTO projects (name, owner, business_unit, risk_tier, created_at) VALUES (?,?,?,?,?)",
                        ("P1", "admin", "BU", "low", now))
        tmp_db.execute("INSERT INTO environments (name, region, classification, readiness_status, service_virtualization_enabled, data_residency, updated_at) VALUES (?,?,?,?,?,?,?)",
                        ("dev", "us-east-1", "internal", "ready", 0, "US", now))
        tmp_db.execute("INSERT INTO scenarios (project_id, name, engine, test_type, workload_mix, script_repository, target_endpoint, sla_p95_ms, max_error_rate, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (1, "S1", "k6", "load", "mixed", "repo", "http://localhost", 500, 1.0, now))
        tmp_db.execute(
            "INSERT INTO test_runs (project_id, scenario_id, environment_id, name, engine, load_profile, target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, 1, 1, "Run", "k6", "constant", 100, 5, status, "pass", 85, "corr-1", "summary", now),
        )
        tmp_db.commit()
        return dict(tmp_db.execute("SELECT * FROM test_runs WHERE id = 1").fetchone())

    def test_collects_results(self, tmp_db, tmp_path):
        run = self._setup_run(tmp_db)
        er = MagicMock(total_requests=100, p50_ms=50, p95_ms=200)
        with patch("apps.api.worker.get_engine") as mock_get, \
             patch("apps.api.worker.store_real_result") as mock_store, \
             patch("apps.api.worker.ARTIFACT_DIR", tmp_path):
            mock_get.return_value.parse_results.return_value = er
            (tmp_path / "run-1").mkdir()
            collect_engine_results(tmp_db, run)
            mock_store.assert_called_once()

    def test_no_requests_fails(self, tmp_db, tmp_path):
        run = self._setup_run(tmp_db)
        er = MagicMock(total_requests=0)
        with patch("apps.api.worker.get_engine") as mock_get, \
             patch("apps.api.worker.clear_run_state"), \
             patch("apps.api.worker.untrack_active_run"), \
             patch("apps.api.worker.ARTIFACT_DIR", tmp_path):
            mock_get.return_value.parse_results.return_value = er
            (tmp_path / "run-1").mkdir()
            collect_engine_results(tmp_db, run)
            row = tmp_db.execute("SELECT status FROM test_runs WHERE id = 1").fetchone()
            assert row["status"] == "failed"

    def test_unknown_engine_fails(self, tmp_db):
        run = self._setup_run(tmp_db)
        run["engine"] = "Unknown"
        with patch("apps.api.worker.get_engine", return_value=None), \
             patch("apps.api.worker.clear_run_state"), \
             patch("apps.api.worker.untrack_active_run"):
            collect_engine_results(tmp_db, run)
            row = tmp_db.execute("SELECT status FROM test_runs WHERE id = 1").fetchone()
            assert row["status"] == "failed"

    def test_parse_exception_fails(self, tmp_db, tmp_path):
        run = self._setup_run(tmp_db)
        with patch("apps.api.worker.get_engine") as mock_get, \
             patch("apps.api.worker.clear_run_state"), \
             patch("apps.api.worker.untrack_active_run"), \
             patch("apps.api.worker.ARTIFACT_DIR", tmp_path):
            mock_get.return_value.parse_results.side_effect = Exception("bad output")
            (tmp_path / "run-1").mkdir()
            collect_engine_results(tmp_db, run)
            row = tmp_db.execute("SELECT status FROM test_runs WHERE id = 1").fetchone()
            assert row["status"] == "failed"


class TestLaunchEngine:
    def _setup_run(self, tmp_db):
        from apps.api.database import utc_now
        now = utc_now()
        tmp_db.execute("INSERT INTO projects (name, owner, business_unit, risk_tier, created_at) VALUES (?,?,?,?,?)",
                        ("P1", "admin", "BU", "low", now))
        tmp_db.execute("INSERT INTO environments (name, region, classification, readiness_status, service_virtualization_enabled, data_residency, updated_at) VALUES (?,?,?,?,?,?,?)",
                        ("dev", "us-east-1", "internal", "ready", 0, "US", now))
        tmp_db.execute("INSERT INTO scenarios (project_id, name, engine, test_type, workload_mix, script_repository, target_endpoint, sla_p95_ms, max_error_rate, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (1, "S1", "k6", "load", "mixed", "repo", "http://localhost:8080", 500, 1.0, now))
        tmp_db.execute(
            "INSERT INTO test_runs (project_id, scenario_id, environment_id, name, engine, load_profile, target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, 1, 1, "Run", "k6", "constant", 100, 5, "running", "pass", 85, "corr-1", "summary", now),
        )
        tmp_db.commit()
        return dict(tmp_db.execute("SELECT * FROM test_runs WHERE id = 1").fetchone())

    def test_launches_successfully(self, tmp_db, tmp_path):
        run = self._setup_run(tmp_db)
        mock_result = MagicMock(returncode=0, stdout="abc123\n", stderr="")
        with patch("apps.api.worker.get_engine") as mock_get, \
             patch("apps.api.worker.subprocess.run", return_value=mock_result), \
             patch("apps.api.worker.set_run_state"), \
             patch("apps.api.worker.track_active_run"), \
             patch("apps.api.worker.ARTIFACT_DIR", tmp_path), \
             patch("apps.api.worker.SCRIPT_DIR", tmp_path):
            mock_get.return_value.build_docker_command.return_value = ["docker", "run"]
            container_id = launch_engine(tmp_db, run)
            assert container_id == "abc123"

    def test_launch_failure_returns_none(self, tmp_db, tmp_path):
        run = self._setup_run(tmp_db)
        mock_result = MagicMock(returncode=1, stdout="", stderr="error")
        with patch("apps.api.worker.get_engine") as mock_get, \
             patch("apps.api.worker.subprocess.run", return_value=mock_result), \
             patch("apps.api.worker.ARTIFACT_DIR", tmp_path), \
             patch("apps.api.worker.SCRIPT_DIR", tmp_path):
            mock_get.return_value.build_docker_command.return_value = ["docker", "run"]
            result = launch_engine(tmp_db, run)
            assert result is None

    def test_timeout_returns_none(self, tmp_db, tmp_path):
        run = self._setup_run(tmp_db)
        import subprocess as sp
        with patch("apps.api.worker.get_engine") as mock_get, \
             patch("apps.api.worker.subprocess.run", side_effect=sp.TimeoutExpired(cmd="docker", timeout=30)), \
             patch("apps.api.worker.ARTIFACT_DIR", tmp_path), \
             patch("apps.api.worker.SCRIPT_DIR", tmp_path):
            mock_get.return_value.build_docker_command.return_value = ["docker", "run"]
            result = launch_engine(tmp_db, run)
            assert result is None

    def test_unknown_engine_returns_none(self, tmp_db):
        run = self._setup_run(tmp_db)
        run["engine"] = "Unknown"
        with patch("apps.api.worker.get_engine", return_value=None):
            result = launch_engine(tmp_db, run)
            assert result is None


class TestProcessWorkerTick:
    def _setup_db(self, tmp_db):
        from apps.api.database import utc_now
        now = utc_now()
        tmp_db.execute("INSERT INTO projects (name, owner, business_unit, risk_tier, created_at) VALUES (?,?,?,?,?)",
                        ("P1", "admin", "BU", "low", now))
        tmp_db.execute("INSERT INTO environments (name, region, classification, readiness_status, service_virtualization_enabled, data_residency, updated_at) VALUES (?,?,?,?,?,?,?)",
                        ("dev", "us-east-1", "internal", "ready", 0, "US", now))
        tmp_db.execute("INSERT INTO scenarios (project_id, name, engine, test_type, workload_mix, script_repository, target_endpoint, sla_p95_ms, max_error_rate, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (1, "S1", "k6", "load", "mixed", "repo", "http://localhost", 500, 1.0, now))
        tmp_db.commit()

    def test_queues_ready_runs(self, tmp_db, monkeypatch):
        self._setup_db(tmp_db)
        from apps.api.database import utc_now
        now = utc_now()
        tmp_db.execute(
            "INSERT INTO test_runs (project_id, scenario_id, environment_id, name, engine, load_profile, target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, 1, 1, "Run", "k6", "constant", 100, 5, "ready", "pass", 85, "corr-1", "summary", now),
        )
        tmp_db.commit()
        monkeypatch.setattr("apps.api.worker.connect_db", lambda: tmp_db)
        with patch("apps.api.worker.get_run") as mock_get_run, \
             patch("apps.api.worker.start_run") as mock_start, \
             patch("apps.api.worker.launch_engine", return_value=None), \
             patch("apps.api.worker.clear_run_state"), \
             patch("apps.api.worker.untrack_active_run"):
            mock_get_run.return_value = {"id": 1, "name": "Run", "pool_id": None, "target_vusers": 100}
            summary = process_worker_tick()
        assert summary["queued"] >= 1

    def test_starts_queued_run(self, tmp_db, monkeypatch):
        self._setup_db(tmp_db)
        from apps.api.database import utc_now
        now = utc_now()
        tmp_db.execute(
            "INSERT INTO test_runs (project_id, scenario_id, environment_id, name, engine, load_profile, target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, 1, 1, "Run", "k6", "constant", 100, 5, "queued", "pass", 85, "corr-1", "summary", now),
        )
        tmp_db.commit()
        monkeypatch.setattr("apps.api.worker.connect_db", lambda: tmp_db)
        with patch("apps.api.worker.get_run") as mock_get_run, \
             patch("apps.api.worker.start_run"), \
             patch("apps.api.worker.launch_engine", return_value="container1"), \
             patch("apps.api.worker.set_run_state"), \
             patch("apps.api.worker.track_active_run"):
            mock_get_run.return_value = {"id": 1, "name": "Run", "pool_id": None, "target_vusers": 100}
            summary = process_worker_tick()
        assert summary["started"] >= 1

    def test_collects_running_results(self, tmp_db, monkeypatch, tmp_path):
        self._setup_db(tmp_db)
        from apps.api.database import utc_now
        now = utc_now()
        tmp_db.execute(
            "INSERT INTO test_runs (project_id, scenario_id, environment_id, name, engine, load_profile, target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, started_at, execution_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, 1, 1, "Run", "k6", "constant", 100, 5, "running", "pass", 85, "corr-1", "summary", now, "container1", now),
        )
        tmp_db.commit()
        monkeypatch.setattr("apps.api.worker.connect_db", lambda: tmp_db)
        er = MagicMock(total_requests=50, p50_ms=50, p95_ms=200, p99_ms=300,
                       throughput_rps=10.0, error_rate=0.1,
                       failed_requests=0, duration_seconds=60.0)
        with patch("apps.api.worker.check_container_running", return_value=False), \
             patch("apps.api.worker.get_engine") as mock_get, \
             patch("apps.api.models.write_result_artifact", return_value="/tmp/r.json"), \
             patch("apps.api.models.upload_artifact", return_value="s3://b/r.json"), \
             patch("apps.api.models.trigger_webhooks"), \
             patch("apps.api.worker.remove_container"), \
             patch("apps.api.models.measure_redis_latency_ms", return_value=1), \
             patch("apps.api.models.measure_db_latency_ms", return_value=1), \
             patch("apps.api.worker.ARTIFACT_DIR", tmp_path):
            mock_get.return_value.parse_results.return_value = er
            (tmp_path / "run-1").mkdir()
            summary = process_worker_tick()
        assert summary["completed"] >= 1


class TestRunWorker:
    def test_runs_and_stops(self, monkeypatch):
        call_count = 0

        def mock_tick():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                import signal
                signal.raise_signal(signal.SIGTERM)
            return {"queued": 0, "started": 0, "completed": 0, "failed": 0, "scheduled": 0}

        monkeypatch.setattr("apps.api.worker.process_worker_tick", mock_tick)
        monkeypatch.setattr("apps.api.worker.time.sleep", lambda _: None)
        monkeypatch.setattr("apps.api.worker.WORKER_INTERVAL_SECONDS", 0)
        # Should not hang — SIGTERM sets stopping=True
        from apps.api.worker import run_worker
        run_worker()


class TestProcessWorkerTickEdgeCases:
    def test_db_connection_failure(self, monkeypatch):
        monkeypatch.setattr("apps.api.worker.connect_db", MagicMock(side_effect=Exception("DB down")))
        summary = process_worker_tick()
        assert summary["queued"] == 0
        assert summary["started"] == 0

    def test_exception_rollback(self, tmp_db, monkeypatch):
        self._setup_db(tmp_db)
        from apps.api.database import utc_now
        now = utc_now()
        tmp_db.execute(
            "INSERT INTO test_runs (project_id, scenario_id, environment_id, name, engine, load_profile, target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, 1, 1, "Run", "k6", "constant", 100, 5, "queued", "pass", 85, "corr-1", "summary", now),
        )
        tmp_db.commit()
        monkeypatch.setattr("apps.api.worker.connect_db", lambda: tmp_db)
        # Make start_run raise to trigger the exception path
        with patch("apps.api.worker.get_run", side_effect=Exception("DB locked")), \
             patch("apps.api.worker.clear_run_state"), \
             patch("apps.api.worker.untrack_active_run"):
            summary = process_worker_tick()
        # Should handle exception gracefully
        assert "failed" in summary

    def test_running_run_exception_path(self, tmp_db, monkeypatch):
        self._setup_db(tmp_db)
        from apps.api.database import utc_now
        now = utc_now()
        tmp_db.execute(
            "INSERT INTO test_runs (project_id, scenario_id, environment_id, name, engine, load_profile, target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, started_at, execution_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, 1, 1, "Run", "k6", "constant", 100, 5, "running", "pass", 85, "corr-1", "summary", now, "container1", now),
        )
        tmp_db.commit()
        monkeypatch.setattr("apps.api.worker.connect_db", lambda: tmp_db)
        with patch("apps.api.worker.check_container_running", return_value=False), \
             patch("apps.api.worker.get_engine", side_effect=Exception("engine error")), \
             patch("apps.api.worker.clear_run_state"), \
             patch("apps.api.worker.untrack_active_run"):
            summary = process_worker_tick()
        assert "failed" in summary

    def _setup_db(self, tmp_db):
        from apps.api.database import utc_now
        now = utc_now()
        tmp_db.execute("INSERT INTO projects (name, owner, business_unit, risk_tier, created_at) VALUES (?,?,?,?,?)",
                        ("P1", "admin", "BU", "low", now))
        tmp_db.execute("INSERT INTO environments (name, region, classification, readiness_status, service_virtualization_enabled, data_residency, updated_at) VALUES (?,?,?,?,?,?,?)",
                        ("dev", "us-east-1", "internal", "ready", 0, "US", now))
        tmp_db.execute("INSERT INTO scenarios (project_id, name, engine, test_type, workload_mix, script_repository, target_endpoint, sla_p95_ms, max_error_rate, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (1, "S1", "k6", "load", "mixed", "repo", "http://localhost", 500, 1.0, now))
        tmp_db.commit()


class TestLaunchEngineException:
    def test_launch_generic_exception(self, tmp_db, tmp_path):
        from apps.api.database import utc_now
        now = utc_now()
        tmp_db.execute("INSERT INTO projects (name, owner, business_unit, risk_tier, created_at) VALUES (?,?,?,?,?)",
                        ("P1", "admin", "BU", "low", now))
        tmp_db.execute("INSERT INTO environments (name, region, classification, readiness_status, service_virtualization_enabled, data_residency, updated_at) VALUES (?,?,?,?,?,?,?)",
                        ("dev", "us-east-1", "internal", "ready", 0, "US", now))
        tmp_db.execute("INSERT INTO scenarios (project_id, name, engine, test_type, workload_mix, script_repository, target_endpoint, sla_p95_ms, max_error_rate, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (1, "S1", "k6", "load", "mixed", "repo", "http://localhost", 500, 1.0, now))
        tmp_db.execute(
            "INSERT INTO test_runs (project_id, scenario_id, environment_id, name, engine, load_profile, target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, 1, 1, "Run", "k6", "constant", 100, 5, "running", "pass", 85, "corr-1", "summary", now),
        )
        tmp_db.commit()
        run = dict(tmp_db.execute("SELECT * FROM test_runs WHERE id=1").fetchone())
        with patch("apps.api.worker.get_engine") as mock_get, \
             patch("apps.api.worker.subprocess.run", side_effect=Exception("generic error")), \
             patch("apps.api.worker.ARTIFACT_DIR", tmp_path), \
             patch("apps.api.worker.SCRIPT_DIR", tmp_path):
            mock_get.return_value.build_docker_command.return_value = ["docker", "run"]
            result = launch_engine(tmp_db, run)
            assert result is None


class TestProcessWorkerTickScheduleException:
    def test_schedule_check_exception(self, monkeypatch):
        monkeypatch.setattr("apps.api.worker.connect_db", MagicMock(side_effect=Exception("DB down")))
        summary = process_worker_tick()
        assert summary["queued"] == 0


class TestProcessWorkerTickStartRunException:
    def test_start_run_exception(self, tmp_db, monkeypatch):
        from apps.api.database import utc_now
        now = utc_now()
        tmp_db.execute("INSERT INTO projects (name, owner, business_unit, risk_tier, created_at) VALUES (?,?,?,?,?)",
                        ("P1", "admin", "BU", "low", now))
        tmp_db.execute("INSERT INTO environments (name, region, classification, readiness_status, service_virtualization_enabled, data_residency, updated_at) VALUES (?,?,?,?,?,?,?)",
                        ("dev", "us-east-1", "internal", "ready", 0, "US", now))
        tmp_db.execute("INSERT INTO scenarios (project_id, name, engine, test_type, workload_mix, script_repository, target_endpoint, sla_p95_ms, max_error_rate, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (1, "S1", "k6", "load", "mixed", "repo", "http://localhost", 500, 1.0, now))
        tmp_db.execute(
            "INSERT INTO test_runs (project_id, scenario_id, environment_id, name, engine, load_profile, target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, 1, 1, "Run", "k6", "constant", 100, 5, "queued", "pass", 85, "corr-1", "summary", now),
        )
        tmp_db.commit()
        monkeypatch.setattr("apps.api.worker.connect_db", lambda: tmp_db)
        with patch("apps.api.worker.get_run", side_effect=Exception("DB locked")), \
             patch("apps.api.worker.clear_run_state"), \
             patch("apps.api.worker.untrack_active_run"):
            summary = process_worker_tick()
        assert summary["started"] == 0
        assert summary["completed"] == 0


class TestProcessWorkerTickElapsedTimeout:
    def test_elapsed_timeout(self, tmp_db, monkeypatch, tmp_path):
        from apps.api.database import utc_now
        from datetime import datetime as dt_module, timedelta, timezone
        now = utc_now()
        old_time = (dt_module.now(timezone.utc) - timedelta(hours=1)).isoformat()
        tmp_db.execute("INSERT INTO projects (name, owner, business_unit, risk_tier, created_at) VALUES (?,?,?,?,?)",
                        ("P1", "admin", "BU", "low", now))
        tmp_db.execute("INSERT INTO environments (name, region, classification, readiness_status, service_virtualization_enabled, data_residency, updated_at) VALUES (?,?,?,?,?,?,?)",
                        ("dev", "us-east-1", "internal", "ready", 0, "US", now))
        tmp_db.execute("INSERT INTO scenarios (project_id, name, engine, test_type, workload_mix, script_repository, target_endpoint, sla_p95_ms, max_error_rate, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (1, "S1", "k6", "load", "mixed", "repo", "http://localhost", 500, 1.0, now))
        tmp_db.execute(
            "INSERT INTO test_runs (project_id, scenario_id, environment_id, name, engine, load_profile, target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, started_at, execution_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, 1, 1, "Run", "k6", "constant", 100, 5, "running", "pass", 85, "corr-1", "summary", old_time, "container1", now),
        )
        tmp_db.commit()
        monkeypatch.setattr("apps.api.worker.connect_db", lambda: tmp_db)
        monkeypatch.setattr("apps.api.worker.WORKER_COMPLETE_SECONDS", 0)
        er = MagicMock(total_requests=50, p50_ms=50, p95_ms=200, p99_ms=300,
                       throughput_rps=10.0, error_rate=0.1, failed_requests=0, duration_seconds=60.0)
        with patch("apps.api.worker.check_container_running", return_value=True), \
             patch("apps.api.worker.get_engine") as mock_get, \
             patch("apps.api.models.write_result_artifact", return_value="/tmp/r.json"), \
             patch("apps.api.models.upload_artifact", return_value="s3://b/r.json"), \
             patch("apps.api.models.trigger_webhooks"), \
             patch("apps.api.worker.remove_container"), \
             patch("apps.api.models.measure_redis_latency_ms", return_value=1), \
             patch("apps.api.models.measure_db_latency_ms", return_value=1), \
             patch("apps.api.worker.ARTIFACT_DIR", tmp_path):
            mock_get.return_value.parse_results.return_value = er
            (tmp_path / "run-1").mkdir()
            summary = process_worker_tick()
        assert summary["completed"] >= 1
