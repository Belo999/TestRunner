from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestMainEntryPoint:
    """Tests for main.py entry point."""

    def test_main_api_role(self, monkeypatch):
        monkeypatch.setenv("MARATHONRUNNER_SERVICE_ROLE", "api")
        with patch("apps.api.server.run_api") as mock_run:
            from apps.api.main import main
            main()
            mock_run.assert_called_once()

    def test_main_worker_role(self, monkeypatch):
        monkeypatch.setenv("MARATHONRUNNER_SERVICE_ROLE", "worker")
        with patch("apps.api.worker.run_worker") as mock_run:
            import apps.api.main as main_module
            monkeypatch.setattr(main_module, "SERVICE_ROLE", "worker")
            main_module.main()
            mock_run.assert_called_once()


class TestWorkerK8sPaths:
    """Tests for worker.py K8s execution paths."""

    def test_launch_engine_k8s_mode(self, tmp_path, monkeypatch):
        from apps.api.worker import launch_engine
        from apps.api.database import utc_now

        monkeypatch.setattr("apps.api.models.EXECUTION_MODE", "kubernetes")

        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "target_endpoint": "https://httpbin.org"
        }

        run = {
            "id": 100,
            "engine": "k6",
            "scenario_id": 1,
            "target_vusers": 10,
            "duration_minutes": 5,
        }

        with patch("apps.api.models.build_k8s_testrun_spec") as mock_build:
            mock_build.return_value = {"apiVersion": "batch/v1"}
            result = launch_engine(conn, run)
            assert result is not None
            assert "k8s-run-100" in result

    def test_launch_engine_k8s_exception(self, tmp_path, monkeypatch):
        from apps.api.worker import launch_engine

        monkeypatch.setattr("apps.api.models.EXECUTION_MODE", "kubernetes")

        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "target_endpoint": "https://httpbin.org"
        }

        run = {
            "id": 101,
            "engine": "k6",
            "scenario_id": 1,
            "target_vusers": 10,
            "duration_minutes": 5,
        }

        with patch("apps.api.models.build_k8s_testrun_spec", side_effect=Exception("K8s error")):
            result = launch_engine(conn, run)
            assert result is None


class TestServerK8sEndpoints:
    """Tests for server.py K8s endpoints."""

    def test_k8s_mode_endpoint(self):
        from apps.api.models import get_execution_mode
        mode = get_execution_mode()
        assert mode in ("docker", "kubernetes")

    def test_k8s_jobs_endpoint(self):
        from apps.api.models import list_k8s_jobs
        jobs = list_k8s_jobs()
        assert isinstance(jobs, list)

    def test_k8s_nodes_endpoint(self):
        from apps.api.models import get_k8s_cluster_nodes
        nodes = get_k8s_cluster_nodes()
        assert isinstance(nodes, list)

    def test_k8s_testrun_status_not_found(self):
        from apps.api.models import get_k8s_testrun_status
        status = get_k8s_testrun_status(99999)
        assert status is None

    def test_delete_k8s_testrun(self):
        from apps.api.models import delete_k8s_testrun
        result = delete_k8s_testrun(1, "k6")
        assert result is True


class TestModelExceptionHandlers:
    """Tests for models.py exception handlers."""

    def test_generate_trend_insights_empty(self):
        from apps.api.models import generate_trend_insights
        insights = generate_trend_insights()
        assert isinstance(insights, list)

    def test_check_execution_allowed_no_windows(self, tmp_path, monkeypatch):
        from apps.api.models import check_execution_allowed
        from apps.api.database import DatabaseConnection

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE execution_windows (id INTEGER PRIMARY KEY, window_type TEXT, environment_id INTEGER, start_hour INTEGER, end_hour INTEGER, days_of_week TEXT, enabled INTEGER)")
        conn.commit()

        db_conn = DatabaseConnection(conn, "sqlite")
        monkeypatch.setattr("apps.api.models.connect_db", lambda: db_conn)
        result = check_execution_allowed(1)
        assert "inAllowedWindow" in result


class TestEngineBaseK8s:
    """Tests for engine base class K8s methods."""

    def test_all_engines_build_k8s_job_spec(self):
        from apps.api.engines import list_engines, get_engine
        for engine_name in list_engines():
            engine = get_engine(engine_name)
            config = {
                "run_id": 1,
                "engine": engine_name,
                "target_endpoint": "https://example.com",
                "target_vusers": 10,
                "duration_minutes": 1,
                "script_configmap": "test-scripts",
            }
            job = engine.build_k8s_job_spec(config)
            assert job["apiVersion"] == "batch/v1"
            assert job["kind"] == "Job"
            assert "containers" in job["spec"]["template"]["spec"]
