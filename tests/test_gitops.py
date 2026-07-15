from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def gitops_db(tmp_path):
    """Create a test database with sample data for GitOps tests."""
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
        "INSERT INTO environments (name, region, classification, readiness_status, service_virtualization_enabled, data_residency, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("dev", "us-east-1", "internal", "ready", 0, "US", "2025-01-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO environments (name, region, classification, readiness_status, service_virtualization_enabled, data_residency, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("staging", "us-east-1", "internal", "ready", 0, "US", "2025-01-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO scenarios (project_id, name, engine, test_type, workload_mix, script_repository, target_endpoint, sla_p95_ms, max_error_rate, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (1, "API Load Test", "k6", "load", "constant", "repo", "http://localhost", 500, 0.05, "2025-01-01T00:00:00Z"),
    )

    conn.commit()
    yield conn
    conn.close()


class TestGitOpsExport:
    """Tests for GitOps test configuration export."""

    def test_export_test_config(self, gitops_db, monkeypatch):
        from apps.api.models import export_test_config
        monkeypatch.setattr("apps.api.models.connect_db", lambda: gitops_db)
        config = export_test_config(1)
        assert config["apiVersion"] == "marathonrunner.io/v1alpha1"
        assert config["kind"] == "TestDefinition"
        assert config["spec"]["engine"] == "k6"
        assert "annotations" in config["metadata"]

    def test_export_test_config_not_found(self, gitops_db, monkeypatch):
        from apps.api.models import export_test_config
        monkeypatch.setattr("apps.api.models.connect_db", lambda: gitops_db)
        with pytest.raises(ValueError, match="Scenario 999 not found"):
            export_test_config(999)

    def test_export_config_has_hash(self, gitops_db, monkeypatch):
        from apps.api.models import export_test_config
        monkeypatch.setattr("apps.api.models.connect_db", lambda: gitops_db)
        config = export_test_config(1)
        assert "marathonrunner.io/config-hash" in config["metadata"]["annotations"]


class TestGitOpsImport:
    """Tests for GitOps test configuration import."""

    def test_import_test_config_new(self, gitops_db, monkeypatch):
        from apps.api.models import import_test_config
        monkeypatch.setattr("apps.api.models.connect_db", lambda: gitops_db)
        config = {
            "apiVersion": "marathonrunner.io/v1alpha1",
            "kind": "TestDefinition",
            "metadata": {"name": "new-test"},
            "spec": {
                "engine": "k6",
                "testType": "load",
                "workloadMix": "constant",
                "scriptRepository": "repo",
                "targetEndpoint": "http://example.com",
                "slaP95Ms": 300,
                "maxErrorRate": 0.01,
            },
        }
        result = import_test_config(config, project_id=1)
        assert result["action"] == "created"
        assert "scenario_id" in result

    def test_import_test_config_update(self, gitops_db, monkeypatch):
        from apps.api.models import import_test_config
        monkeypatch.setattr("apps.api.models.connect_db", lambda: gitops_db)
        # The import function converts names to title case, so we need to match
        # what the existing scenario name will become after conversion
        config = {
            "apiVersion": "marathonrunner.io/v1alpha1",
            "kind": "TestDefinition",
            "metadata": {"name": "api-load-test"},
            "spec": {
                "engine": "JMeter",
                "testType": "stress",
                "workloadMix": "ramping",
                "scriptRepository": "repo",
                "targetEndpoint": "http://localhost",
                "slaP95Ms": 400,
                "maxErrorRate": 0.03,
            },
        }
        # First, update the existing scenario name to match what import will create
        gitops_db.execute("UPDATE scenarios SET name = 'Api Load Test' WHERE id = 1")
        gitops_db.commit()
        result = import_test_config(config, project_id=1)
        assert result["action"] == "updated"
        assert result["scenario_id"] == 1

    def test_import_invalid_engine(self, gitops_db, monkeypatch):
        from apps.api.models import import_test_config
        monkeypatch.setattr("apps.api.models.connect_db", lambda: gitops_db)
        config = {
            "metadata": {"name": "test"},
            "spec": {"engine": "InvalidEngine"},
        }
        with pytest.raises(ValueError, match="Invalid engine"):
            import_test_config(config)


class TestGitOpsDriftDetection:
    """Tests for GitOps configuration drift detection."""

    def test_detect_no_drift(self, gitops_db, monkeypatch):
        from apps.api.models import detect_config_drift
        monkeypatch.setattr("apps.api.models.connect_db", lambda: gitops_db)
        git_config = {
            "spec": {
                "engine": "k6",
                "testType": "load",
                "targetEndpoint": "http://localhost",
            }
        }
        result = detect_config_drift(1, git_config)
        assert result["has_drift"] is False
        assert result["drift_count"] == 0

    def test_detect_drift(self, gitops_db, monkeypatch):
        from apps.api.models import detect_config_drift
        monkeypatch.setattr("apps.api.models.connect_db", lambda: gitops_db)
        git_config = {
            "spec": {
                "engine": "JMeter",
                "testType": "stress",
                "targetEndpoint": "http://different.com",
            }
        }
        result = detect_config_drift(1, git_config)
        assert result["has_drift"] is True
        assert result["drift_count"] > 0

    def test_detect_drift_severity(self, gitops_db, monkeypatch):
        from apps.api.models import detect_config_drift
        monkeypatch.setattr("apps.api.models.connect_db", lambda: gitops_db)
        git_config = {
            "spec": {
                "engine": "JMeter",
                "targetEndpoint": "http://different.com",
            }
        }
        result = detect_config_drift(1, git_config)
        severities = [d["severity"] for d in result["drifts"]]
        assert "critical" in severities


class TestGitOpsHistory:
    """Tests for GitOps configuration history."""

    def test_get_git_config_history(self, gitops_db, monkeypatch):
        from apps.api.models import get_git_config_history
        monkeypatch.setattr("apps.api.models.connect_db", lambda: gitops_db)
        history = get_git_config_history()
        assert isinstance(history, list)

    def test_get_git_config_history_with_project(self, gitops_db, monkeypatch):
        from apps.api.models import get_git_config_history
        monkeypatch.setattr("apps.api.models.connect_db", lambda: gitops_db)
        history = get_git_config_history(project_id=1)
        assert isinstance(history, list)


class TestGitOpsPromotion:
    """Tests for GitOps configuration promotion."""

    def test_promote_config(self, gitops_db, monkeypatch):
        from apps.api.models import promote_config
        monkeypatch.setattr("apps.api.models.connect_db", lambda: gitops_db)
        result = promote_config(1, "dev", "staging")
        assert result["action"] == "promoted"
        assert result["from_env"] == "dev"
        assert result["to_env"] == "staging"

    def test_promote_config_invalid_scenario(self, gitops_db, monkeypatch):
        from apps.api.models import promote_config
        monkeypatch.setattr("apps.api.models.connect_db", lambda: gitops_db)
        with pytest.raises(ValueError, match="Scenario 999 not found"):
            promote_config(999, "dev", "staging")

    def test_promote_config_invalid_env(self, gitops_db, monkeypatch):
        from apps.api.models import promote_config
        monkeypatch.setattr("apps.api.models.connect_db", lambda: gitops_db)
        with pytest.raises(ValueError, match="Environment nonexistent not found"):
            promote_config(1, "dev", "nonexistent")
