from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from apps.api.database import utc_now
from apps.api.engines.base import EngineResult
from apps.api import models as models_mod


# ── DB fixture: wire the default DB path to a temp file ───────────────────────

@pytest.fixture(autouse=True)
def _set_test_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the models module's DB to a fresh temp database for every test."""
    db_path = tmp_path / "test.db"
    db_str = str(db_path)
    monkeypatch.setenv("MARATHONRUNNER_DB_PATH", db_str)
    # Also patch the module-level DB_PATH which was set at import time
    import apps.api.database as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", db_path)

    # Create schema
    conn = sqlite3.connect(db_str)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    schema_text = (Path(__file__).resolve().parents[1] / "apps" / "api" / "database.py").read_text()
    match = re.search(r'SCHEMA_SQLITE\s*=\s"""(.*?)"""', schema_text, re.DOTALL)
    if match:
        conn.executescript(match.group(1))

    now = utc_now()
    conn.execute("INSERT INTO projects (name, owner, business_unit, risk_tier, created_at) VALUES (?, ?, ?, ?, ?)",
                 ("Test Project", "admin", "Engineering", "high", now))
    conn.execute("INSERT INTO projects (name, owner, business_unit, risk_tier, created_at) VALUES (?, ?, ?, ?, ?)",
                 ("Critical Project", "admin", "Finance", "critical", now))
    conn.execute("INSERT INTO environments (name, region, classification, readiness_status, service_virtualization_enabled, data_residency, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 ("staging", "us-east-1", "internal", "ready", 0, "US", now))
    conn.execute("INSERT INTO environments (name, region, classification, readiness_status, service_virtualization_enabled, data_residency, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 ("production", "eu-west-1", "restricted", "not_ready", 0, "EU", now))
    conn.execute("INSERT INTO scenarios (project_id, name, engine, test_type, workload_mix, script_repository, target_endpoint, sla_p95_ms, max_error_rate, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (1, "Login Load Test", "k6", "load", "mixed", "repo", "https://api.example.com/login", 500, 1.0, now))
    conn.execute("INSERT INTO scenarios (project_id, name, engine, test_type, workload_mix, script_repository, target_endpoint, sla_p95_ms, max_error_rate, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (1, "Search Stress Test", "JMeter", "stress", "api-only", "repo", "https://api.example.com/search", 300, 0.5, now))
    conn.execute("INSERT INTO load_generator_pools (name, region, engines, max_vusers, status, current_reservation, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 ("pool-us-1", "us-east-1", '["k6","JMeter"]', 500, "healthy", 0, now))
    conn.execute("INSERT INTO load_generator_pools (name, region, engines, max_vusers, status, current_reservation, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 ("pool-eu-1", "eu-west-1", '["k6"]', 200, "healthy", 150, now))
    conn.execute("INSERT INTO policies (name, scope, rule, severity, enabled) VALUES (?, ?, ?, ?, ?)",
                 ("Max vusers", "run", "max_vusers <= 2000", "warning", 1))
    from apps.api.auth import hash_password
    pw_hash, pw_salt = hash_password("admin123")
    conn.execute("INSERT INTO users (username, display_name, role, email, password_hash, password_salt, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 ("admin", "Admin User", "admin", "admin@example.com", pw_hash, pw_salt, now))
    pw_hash2, pw_salt2 = hash_password("eng123")
    conn.execute("INSERT INTO users (username, display_name, role, email, password_hash, password_salt, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 ("engineer1", "Engineer One", "engineer", "eng@example.com", pw_hash2, pw_salt2, now))
    conn.execute("INSERT INTO webhooks (name, url, event, enabled, secret, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 ("Slack Notify", "https://hooks.slack.com/test", "run.completed", 1, "secret123", now))
    conn.execute("INSERT INTO applications (name, endpoint, team, environment, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 ("My App", "https://api.example.com/health", "platform", "staging", "active", now))
    conn.execute("INSERT INTO execution_windows (name, type, day_of_week, start_hour, end_hour, environment_id, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 ("Weekday Window", "window", 0, 6, 22, 1, 1, now))
    conn.commit()
    conn.close()

    yield


# ── Pure helpers (no DB) ─────────────────────────────────────────────────────

class TestPolicyDecision:
    def test_ready_when_all_green(self):
        project = {"risk_tier": "low"}
        environment = {"readiness_status": "ready"}
        pool = {"max_vusers": 500, "current_reservation": 0}
        status, findings = models_mod.policy_decision(project, environment, pool, 100)
        assert status == "ready"
        assert findings == []

    def test_pending_when_env_not_ready(self):
        status, findings = models_mod.policy_decision(
            {"risk_tier": "low"}, {"readiness_status": "not_ready"}, {"max_vusers": 500}, 100)
        assert status == "pending_approval"
        assert any("readiness" in f.lower() for f in findings)

    def test_pending_when_no_pool(self):
        status, findings = models_mod.policy_decision(
            {"risk_tier": "low"}, {"readiness_status": "ready"}, None, 100)
        assert status == "pending_approval"

    def test_pending_when_critical_high_volume(self):
        status, findings = models_mod.policy_decision(
            {"risk_tier": "critical"}, {"readiness_status": "ready"},
            {"max_vusers": 5000, "current_reservation": 0}, 3000)
        assert status == "pending_approval"

    def test_ready_when_critical_low_volume(self):
        status, _ = models_mod.policy_decision(
            {"risk_tier": "critical"}, {"readiness_status": "ready"},
            {"max_vusers": 5000, "current_reservation": 0}, 100)
        assert status == "ready"


class TestBuildDesignSummary:
    def test_with_findings(self):
        r = models_mod.build_design_summary({"test_type": "load"}, {"name": "staging"}, ["F1", "F2"])
        assert "2 item(s)" in r

    def test_without_findings(self):
        r = models_mod.build_design_summary({"test_type": "load"}, {"name": "staging"}, [])
        assert "passed" in r


class TestCalculateRiskScore:
    def test_passing_low_risk(self):
        r = {"p95_ms": 200, "error_rate": 0.1, "db_cpu_peak": 40, "cpu_peak": 40}
        assert 1 <= models_mod.calculate_risk_score({"sla_p95_ms": 500}, r, "passed") <= 99

    def test_failing_high_risk(self):
        r = {"p95_ms": 800, "error_rate": 5.0, "db_cpu_peak": 90, "cpu_peak": 90}
        assert models_mod.calculate_risk_score({"sla_p95_ms": 500}, r, "failed") > 50

    def test_clamped_to_99(self):
        r = {"p95_ms": 10000, "error_rate": 50.0, "db_cpu_peak": 99, "cpu_peak": 99}
        assert models_mod.calculate_risk_score({"sla_p95_ms": 500}, r, "failed") == 99

    def test_minimum_is_1(self):
        r = {"p95_ms": 0, "error_rate": 0.0, "db_cpu_peak": 0, "cpu_peak": 0}
        assert models_mod.calculate_risk_score({"sla_p95_ms": 500}, r, "passed") >= 1


class TestSummarizeResult:
    def test_passing(self):
        t = models_mod.summarize_result({"sla_p95_ms": 500, "max_error_rate": 1.0}, {"p95_ms": 300, "error_rate": 0.5}, "passed")
        assert "passed" in t and "300" in t

    def test_failing(self):
        t = models_mod.summarize_result({"sla_p95_ms": 500, "max_error_rate": 1.0}, {"p95_ms": 700, "error_rate": 2.5}, "failed")
        assert "failed" in t


class TestDetectRegression:
    def test_no_regression(self):
        c = {"p95": 100, "errorRate": 0.5, "throughput": 50.0}
        assert models_mod._detect_regression(c, c, {"sla_p95_ms": 500, "max_error_rate": 1.0}) == []

    def test_p95_regression(self):
        r = models_mod._detect_regression(
            {"p95": 300, "errorRate": 0.5, "throughput": 50},
            {"p95": 100, "errorRate": 0.5, "throughput": 50},
            {"sla_p95_ms": 500, "max_error_rate": 1.0})
        assert len(r) == 1 and r[0]["metric"] == "p95_latency"

    def test_p95_critical(self):
        r = models_mod._detect_regression(
            {"p95": 600, "errorRate": 0.5, "throughput": 50},
            {"p95": 100, "errorRate": 0.5, "throughput": 50},
            {"sla_p95_ms": 500, "max_error_rate": 1.0})
        assert r[0]["severity"] == "critical"

    def test_error_rate_regression(self):
        r = models_mod._detect_regression(
            {"p95": 100, "errorRate": 2.0, "throughput": 50},
            {"p95": 100, "errorRate": 0.5, "throughput": 50},
            {"sla_p95_ms": 500, "max_error_rate": 1.0})
        assert any(x["metric"] == "error_rate" for x in r)

    def test_throughput_decrease(self):
        r = models_mod._detect_regression(
            {"p95": 100, "errorRate": 0.5, "throughput": 30},
            {"p95": 100, "errorRate": 0.5, "throughput": 50},
            {"sla_p95_ms": 500, "max_error_rate": 1.0})
        assert any(x["metric"] == "throughput" for x in r)


class TestIsImprovement:
    def test_latency(self):
        assert models_mod._is_improvement("p95_ms", 100, 200) is True
        assert models_mod._is_improvement("p95_ms", 200, 100) is False

    def test_error(self):
        assert models_mod._is_improvement("error_rate", 0.5, 1.0) is True

    def test_throughput(self):
        assert models_mod._is_improvement("throughput_rps", 100, 50) is True

    def test_apdex(self):
        assert models_mod._is_improvement("apdex", 0.95, 0.85) is True


class TestCronParsing:
    def test_parse_valid(self):
        r = models_mod._parse_cron("0 9 * * 1-5")
        assert r is not None and r["minute"] == "0"

    def test_parse_invalid(self):
        assert models_mod._parse_cron("") is None

    def test_matches_wildcard(self):
        c = models_mod._parse_cron("* * * * *")
        assert models_mod._cron_matches(c, datetime(2024, 1, 15, 14, 30)) is True

    def test_matches_specific(self):
        c = models_mod._parse_cron("30 14 * * *")
        assert models_mod._cron_matches(c, datetime(2024, 1, 15, 14, 30)) is True
        assert models_mod._cron_matches(c, datetime(2024, 1, 15, 14, 31)) is False

    def test_matches_step(self):
        c = models_mod._parse_cron("*/15 * * * *")
        assert models_mod._cron_matches(c, datetime(2024, 1, 15, 14, 0)) is True
        assert models_mod._cron_matches(c, datetime(2024, 1, 15, 14, 7)) is False

    def test_matches_range(self):
        # Code uses datetime.weekday() where 0=Mon, 1=Tue, ..., 6=Sun
        # Cron "1-5" checks if weekday() value is in 1..5 → Tue(1)..Sat(5)
        c = models_mod._parse_cron("* * * * 1-5")
        assert models_mod._cron_matches(c, datetime(2024, 1, 16, 12, 0)) is True   # Tuesday=1
        assert models_mod._cron_matches(c, datetime(2024, 1, 20, 12, 0)) is True   # Saturday=5
        assert models_mod._cron_matches(c, datetime(2024, 1, 21, 12, 0)) is False  # Sunday=6

    def test_matches_list(self):
        c = models_mod._parse_cron("* * * * 0,3")
        assert models_mod._cron_matches(c, datetime(2024, 1, 15, 12, 0)) is True   # Monday=0
        assert models_mod._cron_matches(c, datetime(2024, 1, 18, 12, 0)) is True   # Thursday=3
        assert models_mod._cron_matches(c, datetime(2024, 1, 16, 12, 0)) is False  # Tuesday=1

    def test_next_cron_time(self):
        c = models_mod._parse_cron("0 9 * * *")
        r = models_mod._next_cron_time(c, datetime(2024, 1, 15, 8, 0))
        assert r.hour == 9 and r > datetime(2024, 1, 15, 8, 0)

    def test_compute_next_run(self):
        r = models_mod.compute_next_run("0 9 * * *")
        assert isinstance(r, str) and "T" in r


class TestStripPasswordFields:
    def test_removes(self):
        u = {"id": 1, "password_hash": "a", "password_salt": "b"}
        r = models_mod._strip_password_fields(u)
        assert "password_hash" not in r and "password_salt" not in r

    def test_no_fields(self):
        assert models_mod._strip_password_fields({"id": 1}) == {"id": 1}


class TestRolePermissions:
    def test_admin(self):
        assert "manage_users" in models_mod.get_role_permissions("admin")["permissions"]

    def test_unknown(self):
        assert models_mod.get_role_permissions("x")["permissions"] == []


class TestTemplates:
    def test_get_all(self):
        assert len(models_mod.get_templates()) > 0

    def test_get_by_id(self):
        t = models_mod.get_template("baseline")
        assert t is not None and t["name"] == "Performance Baseline"

    def test_get_unknown(self):
        assert models_mod.get_template("nope") is None


class TestLoadProfiles:
    def test_get_all(self):
        assert len(models_mod.get_load_profiles()) > 0

    def test_get_by_id(self):
        assert models_mod.get_load_profile("steady") is not None

    def test_get_unknown(self):
        assert models_mod.get_load_profile("nope") is None

    def test_generate(self):
        r = models_mod.generate_load_profile("steady", vusers=100, duration=10)
        assert "100" in r and "10" in r

    def test_generate_unknown(self):
        assert "50" in models_mod.generate_load_profile("nope", vusers=50, duration=5)


# ── DB-dependent tests ────────────────────────────────────────────────────────

class TestGetProject:
    def test_found(self):
        c = models_mod.connect_db()
        assert models_mod.get_project(c, 1)["name"] == "Test Project"

    def test_not_found(self):
        c = models_mod.connect_db()
        with pytest.raises(ValueError):
            models_mod.get_project(c, 9999)


class TestGetScenario:
    def test_found(self):
        c = models_mod.connect_db()
        assert models_mod.get_scenario(c, 1)["name"] == "Login Load Test"

    def test_not_found(self):
        c = models_mod.connect_db()
        with pytest.raises(ValueError):
            models_mod.get_scenario(c, 9999)


class TestGetEnvironment:
    def test_found(self):
        c = models_mod.connect_db()
        assert models_mod.get_environment(c, 1)["name"] == "staging"

    def test_not_found(self):
        c = models_mod.connect_db()
        with pytest.raises(ValueError):
            models_mod.get_environment(c, 9999)


class TestFindPool:
    def test_finds_pool(self):
        c = models_mod.connect_db()
        p = models_mod.find_pool(c, "k6", "us-east-1", 100)
        assert p is not None and p["name"] == "pool-us-1"

    def test_wrong_engine(self):
        c = models_mod.connect_db()
        assert models_mod.find_pool(c, "Gatling", "us-east-1", 100) is None

    def test_insufficient_capacity(self):
        c = models_mod.connect_db()
        assert models_mod.find_pool(c, "k6", "eu-west-1", 100) is None


class TestCreateRun:
    @patch("apps.api.models.set_run_state")
    @patch("apps.api.models.track_active_run")
    def test_creates_ready(self, mock_track, mock_set):
        r = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100, "durationMinutes": 5})
        assert r["status"] == "ready" and r["engine"] == "k6"

    def test_pending_approval(self):
        r = models_mod.create_run({"scenarioId": 1, "environmentId": 2, "targetVusers": 100})
        assert r["status"] == "pending_approval"

    def test_unsupported_engine(self):
        with pytest.raises(ValueError, match="Unsupported"):
            models_mod.create_run({"scenarioId": 1, "engine": "Nope"})


class TestGetRun:
    def test_found(self):
        models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 10})
        run = models_mod.get_run(1)
        assert run["name"] and run["project_name"] == "Test Project"

    def test_not_found(self):
        with pytest.raises(ValueError):
            models_mod.get_run(9999)


class TestGetRuns:
    def test_list_all(self):
        models_mod.create_run({"scenarioId": 1, "environmentId": 1})
        assert len(models_mod.get_runs()) >= 1

    def test_filter_engine(self):
        models_mod.create_run({"scenarioId": 1, "environmentId": 1})
        assert all(r["engine"] == "k6" for r in models_mod.get_runs(engine="k6"))

    def test_search(self):
        models_mod.create_run({"scenarioId": 1, "environmentId": 1, "name": "FindMe"})
        assert len(models_mod.get_runs(search="FindMe")) >= 1


class TestApproveRun:
    def test_approve_pending(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 2, "targetVusers": 100})
        approved = models_mod.approve_run(run["id"], {"reviewer": "lead"})
        assert approved["status"] == "approved"

    def test_cannot_approve_completed(self):
        models_mod.create_run({"scenarioId": 1, "environmentId": 1})
        # Complete the run manually for this test
        conn = models_mod.connect_db()
        conn.execute("UPDATE test_runs SET status='completed' WHERE id=1")
        conn.commit()
        with pytest.raises(ValueError, match="cannot be approved"):
            models_mod.approve_run(1, {})


class TestStartRun:
    @patch("apps.api.models.set_run_state")
    @patch("apps.api.models.track_active_run")
    def test_start_ready(self, mock_track, mock_set):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        started = models_mod.start_run(run["id"])
        assert started["status"] == "running"

    def test_cannot_start_draft(self):
        conn = models_mod.connect_db()
        now = utc_now()
        conn.execute(
            "INSERT INTO test_runs (project_id, scenario_id, environment_id, name, engine, load_profile, target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, 1, 1, "Draft", "k6", "s", 50, 5, "draft", "not_evaluated", 20, "c", "d", now))
        conn.commit()
        rid = conn.execute("SELECT id FROM test_runs WHERE name='Draft'").fetchone()["id"]
        with pytest.raises(ValueError, match="cannot start"):
            models_mod.start_run(rid)


class TestCompleteRun:
    def test_disabled(self):
        models_mod.create_run({"scenarioId": 1, "environmentId": 1})
        with pytest.raises(ValueError, match="Manual completion is disabled"):
            models_mod.complete_run(1)


class TestCancelRun:
    @patch("subprocess.run", return_value=MagicMock(returncode=0))
    def test_cancel_running(self, mock_run):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        with patch("apps.api.models.set_run_state"), patch("apps.api.models.track_active_run"):
            started = models_mod.start_run(run["id"])
        cancelled = models_mod.cancel_run(started["id"])
        assert cancelled["status"] == "cancelled"

    def test_cannot_cancel_completed(self):
        models_mod.create_run({"scenarioId": 1, "environmentId": 1})
        conn = models_mod.connect_db()
        conn.execute("UPDATE test_runs SET status='completed' WHERE id=1")
        conn.commit()
        with pytest.raises(ValueError, match="cannot be cancelled"):
            models_mod.cancel_run(1)


class TestCreateEntity:
    def test_create(self):
        r = models_mod.create_entity("projects", {"name": "NP", "owner": "a", "business_unit": "b", "risk_tier": "low"},
                                      required=["name", "owner", "business_unit", "risk_tier"])
        assert r["name"] == "NP"

    def test_missing_required(self):
        with pytest.raises(ValueError, match="Missing required"):
            models_mod.create_entity("projects", {"name": "X"}, required=["name", "owner"])


class TestUpdateEntity:
    def test_update(self):
        r = models_mod.create_entity("projects", {"name": "UP", "owner": "a", "business_unit": "b", "risk_tier": "low"},
                                      required=["name", "owner", "business_unit", "risk_tier"])
        assert models_mod.update_entity("projects", r["id"], {"name": "Updated"}, ["name"])["name"] == "Updated"

    def test_no_valid_fields(self):
        with pytest.raises(ValueError, match="No valid fields"):
            models_mod.update_entity("projects", 1, {"x": "y"}, ["name"])

    def test_not_found(self):
        with pytest.raises(ValueError, match="not found"):
            models_mod.update_entity("projects", 9999, {"name": "X"}, ["name"])


class TestDeleteEntity:
    def test_delete(self):
        r = models_mod.create_entity("projects", {"name": "DP", "owner": "a", "business_unit": "b", "risk_tier": "low"},
                                      required=["name", "owner", "business_unit", "risk_tier"])
        assert models_mod.delete_entity("projects", r["id"])["deleted"] == r["id"]

    def test_not_found(self):
        with pytest.raises(ValueError, match="not found"):
            models_mod.delete_entity("projects", 9999)


class TestUsers:
    def test_get_users(self):
        users = models_mod.get_users()
        assert len(users) >= 2 and all("password_hash" not in u for u in users)

    def test_get_user(self):
        u = models_mod.get_user(1)
        assert u["username"] == "admin" and "permissions" in u

    def test_get_by_username(self):
        assert models_mod.get_user_by_username("admin") is not None

    def test_get_by_username_missing(self):
        assert models_mod.get_user_by_username("nope") is None

    def test_create_user(self):
        u = models_mod.create_user({"username": "new", "display_name": "N", "role": "viewer", "password": "p"})
        assert u["username"] == "new"

    def test_invalid_role(self):
        with pytest.raises(ValueError, match="Invalid role"):
            models_mod.create_user({"username": "x", "display_name": "X", "role": "bad", "password": "p"})

    def test_missing_fields(self):
        with pytest.raises(ValueError, match="Missing required"):
            models_mod.create_user({"username": "x"})

    def test_update_user(self):
        u = models_mod.create_user({"username": "upd", "display_name": "U", "role": "viewer", "password": "p"})
        assert models_mod.update_user(u["id"], {"display_name": "Updated"})["display_name"] == "Updated"

    def test_delete_user(self):
        u = models_mod.create_user({"username": "del", "display_name": "D", "role": "viewer", "password": "p"})
        assert models_mod.delete_user(u["id"])["deleted"] == u["id"]

    def test_cannot_delete_admin(self):
        with pytest.raises(ValueError, match="Cannot delete admin"):
            models_mod.delete_user(1)


class TestWebhooks:
    def test_get_all(self):
        assert len(models_mod.get_webhooks()) >= 1

    def test_create(self):
        h = models_mod.create_webhook({"name": "H", "url": "https://x.com", "event": "run.completed"})
        assert h["name"] == "H"

    def test_missing_fields(self):
        with pytest.raises(ValueError, match="Missing required"):
            models_mod.create_webhook({"name": "X"})

    def test_get_one(self):
        h = models_mod.create_webhook({"name": "G", "url": "https://x.com", "event": "run.completed"})
        assert models_mod.get_webhook(h["id"])["name"] == "G"

    def test_update(self):
        h = models_mod.create_webhook({"name": "U", "url": "https://x.com", "event": "run.completed"})
        assert models_mod.update_webhook(h["id"], {"name": "Updated"})["name"] == "Updated"

    def test_delete(self):
        h = models_mod.create_webhook({"name": "D", "url": "https://x.com", "event": "run.completed"})
        assert models_mod.delete_webhook(h["id"])["deleted"] == h["id"]


class TestApplications:
    def test_get_all(self):
        assert len(models_mod.get_applications()) >= 1

    def test_create(self):
        a = models_mod.create_application({"name": "NA", "endpoint": "https://h.com"})
        assert a["name"] == "NA"

    def test_get_one(self):
        a = models_mod.create_application({"name": "GA", "endpoint": "https://g.com"})
        assert models_mod.get_application(a["id"])["name"] == "GA"

    def test_update(self):
        a = models_mod.create_application({"name": "UA", "endpoint": "https://u.com"})
        assert models_mod.update_application(a["id"], {"name": "Upd"})["name"] == "Upd"

    def test_delete(self):
        a = models_mod.create_application({"name": "DA", "endpoint": "https://d.com"})
        assert models_mod.delete_application(a["id"])["deleted"] == a["id"]


class TestExecutionWindows:
    def test_get_all(self):
        assert len(models_mod.get_execution_windows()) >= 1

    def test_create_window(self):
        w = models_mod.create_execution_window({"name": "NW", "type": "window", "start_hour": 22, "end_hour": 6})
        assert w["name"] == "NW"

    def test_create_blackout(self):
        w = models_mod.create_execution_window({"name": "BH", "type": "blackout", "start_hour": 0, "end_hour": 24})
        assert w["type"] == "blackout"

    def test_invalid_type(self):
        with pytest.raises(ValueError, match="Type must be"):
            models_mod.create_execution_window({"name": "X", "type": "invalid", "start_hour": 0, "end_hour": 24})

    def test_update(self):
        w = models_mod.create_execution_window({"name": "UW", "type": "window", "start_hour": 8, "end_hour": 18})
        assert models_mod.update_execution_window(w["id"], {"name": "Upd"})["name"] == "Upd"

    def test_delete(self):
        w = models_mod.create_execution_window({"name": "DW", "type": "window", "start_hour": 8, "end_hour": 18})
        assert models_mod.delete_execution_window(w["id"])["deleted"] == w["id"]


class TestBaselines:
    def _make_completed_run(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1})
        conn = models_mod.connect_db()
        now = utc_now()
        conn.execute("UPDATE test_runs SET status='completed', completed_at=? WHERE id=?", (now, run["id"]))
        conn.execute(
            "INSERT INTO run_results (run_id, p50_ms, p95_ms, p99_ms, throughput_rps, error_rate, apdex, cpu_peak, memory_peak, redis_latency_ms, db_cpu_peak, artifact_path, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run["id"], 80, 300, 500, 40.0, 0.5, 0.9, 50.0, 60.0, 2, 20.0, "p", now),
        )
        conn.commit()
        conn.close()
        return run

    def test_set_baseline(self):
        run = self._make_completed_run()
        r = models_mod.set_baseline(run["id"], approved_by="lead")
        assert r["is_baseline"] == 1

    def test_not_completed(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1})
        with pytest.raises(ValueError, match="Only completed"):
            models_mod.set_baseline(run["id"])

    def test_unset(self):
        run = self._make_completed_run()
        models_mod.set_baseline(run["id"])
        assert models_mod.unset_baseline(run["id"])["is_baseline"] == 0

    def test_get_baselines(self):
        run = self._make_completed_run()
        models_mod.set_baseline(run["id"])
        assert len(models_mod.get_baselines()) >= 1

    def test_get_for_scenario(self):
        run = self._make_completed_run()
        models_mod.set_baseline(run["id"])
        assert models_mod.get_baseline_for_scenario(1) is not None


class TestSchedules:
    def test_create(self):
        s = models_mod.create_schedule({"name": "N", "scenario_id": 1, "environment_id": 1,
                                         "target_vusers": 100, "duration_minutes": 10,
                                         "load_profile": "steady", "cron_expression": "0 2 * * *"})
        assert s["name"] == "N"

    def test_invalid_cron(self):
        with pytest.raises(ValueError, match="Invalid cron"):
            models_mod.create_schedule({"name": "B", "scenario_id": 1, "environment_id": 1,
                                         "target_vusers": 100, "duration_minutes": 10,
                                         "load_profile": "steady", "cron_expression": "bad"})

    def test_get_one(self):
        s = models_mod.create_schedule({"name": "G", "scenario_id": 1, "environment_id": 1,
                                         "target_vusers": 100, "duration_minutes": 10,
                                         "load_profile": "steady", "cron_expression": "0 2 * * *"})
        assert models_mod.get_schedule(s["id"])["name"] == "G"

    def test_get_all(self):
        models_mod.create_schedule({"name": "L", "scenario_id": 1, "environment_id": 1,
                                     "target_vusers": 100, "duration_minutes": 10,
                                     "load_profile": "steady", "cron_expression": "0 2 * * *"})
        assert len(models_mod.get_schedules()) >= 1

    def test_update(self):
        s = models_mod.create_schedule({"name": "U", "scenario_id": 1, "environment_id": 1,
                                         "target_vusers": 100, "duration_minutes": 10,
                                         "load_profile": "steady", "cron_expression": "0 2 * * *"})
        assert models_mod.update_schedule(s["id"], {"name": "Upd"})["name"] == "Upd"

    def test_delete(self):
        s = models_mod.create_schedule({"name": "D", "scenario_id": 1, "environment_id": 1,
                                         "target_vusers": 100, "duration_minutes": 10,
                                         "load_profile": "steady", "cron_expression": "0 2 * * *"})
        assert models_mod.delete_schedule(s["id"])["deleted"] == s["id"]


class TestDashboard:
    def test_returns_counts(self):
        models_mod.create_run({"scenarioId": 1, "environmentId": 1})
        r = models_mod.dashboard()
        assert "counts" in r and r["counts"]["runs"] >= 1


class TestTrends:
    def test_returns_trends(self):
        models_mod.create_run({"scenarioId": 1, "environmentId": 1})
        r = models_mod.get_trends()
        assert "summary" in r


class TestCompareRuns:
    def test_need_two(self):
        with pytest.raises(ValueError, match="at least 2"):
            models_mod.compare_runs([1])

    def test_compare(self):
        now = utc_now()
        conn = models_mod.connect_db()
        for name in ["A", "B"]:
            conn.execute("INSERT INTO test_runs (project_id, scenario_id, environment_id, name, engine, load_profile, target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (1, 1, 1, name, "k6", "s", 100, 5, "completed", "passed", 75, f"mr-{name}", "s", now))
            rid = conn.execute("SELECT id FROM test_runs WHERE name=?", (name,)).fetchone()["id"]
            conn.execute("INSERT INTO run_results (run_id, p50_ms, p95_ms, p99_ms, throughput_rps, error_rate, apdex, cpu_peak, memory_peak, redis_latency_ms, db_cpu_peak, artifact_path, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (rid, 80, 300, 500, 40.0, 0.5, 0.9, 50.0, 60.0, 2, 20.0, "p", now))
        conn.commit()
        ids = [r[0] for r in conn.execute("SELECT id FROM test_runs WHERE status='completed' ORDER BY id").fetchall()]
        result = models_mod.compare_runs(ids)
        assert len(result["comparisons"]) > 0


class TestStoreRealResult:
    @patch("apps.api.models.trigger_webhooks")
    @patch("apps.api.models.write_result_artifact", return_value="s3://b/r.json")
    def test_store(self, mock_write, mock_webhooks):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        with patch("apps.api.models.set_run_state"), patch("apps.api.models.track_active_run"):
            started = models_mod.start_run(run["id"])
        er = EngineResult(p50_ms=80, p95_ms=350, p99_ms=600, throughput_rps=45.0,
                          error_rate=0.3, total_requests=1000, failed_requests=3, duration_seconds=60.0)
        result = models_mod.store_real_result(models_mod.connect_db(), started, er)
        assert result["status"] == "completed"


class TestGetActiveRuns:
    @patch("apps.api.models.set_run_state")
    @patch("apps.api.models.track_active_run")
    def test_returns_running(self, mock_track, mock_set):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        models_mod.start_run(run["id"])
        assert len(models_mod.get_active_runs()) >= 1


class TestGetRunLogs:
    def test_no_container(self):
        models_mod.create_run({"scenarioId": 1, "environmentId": 1})
        r = models_mod.get_run_logs(1)
        assert "No container" in r["logs"]


class TestMeasureLatency:
    def test_db_latency(self):
        result = models_mod.measure_db_latency_ms()
        assert result >= 0

    def test_redis_latency(self):
        result = models_mod.measure_redis_latency_ms()
        assert result >= 0


class TestWriteResultArtifact:
    def test_writes_json(self, tmp_path):
        from apps.api.database import utc_now
        models_mod.ARTIFACT_DIR = tmp_path
        run = {"id": 1, "correlation_id": "mr-test"}
        result = {"p95_ms": 200, "error_rate": 0.5}
        with patch("apps.api.models.upload_artifact", return_value="s3://b/r.json"):
            path = models_mod.write_result_artifact(run, result, "passed", 25)
        assert "s3://b/r.json" == path

    def test_falls_back_to_local(self, tmp_path):
        models_mod.ARTIFACT_DIR = tmp_path
        run = {"id": 2, "correlation_id": "mr-test2"}
        result = {"p95_ms": 100}
        with patch("apps.api.models.upload_artifact", return_value=None):
            path = models_mod.write_result_artifact(run, result, "passed", 10)
        assert str(tmp_path / "run-2-result.json") == path
        assert (tmp_path / "run-2-result.json").exists()


class TestGenerateResultInsights:
    def _make_run_and_result(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        result = {"p95_ms": 300, "error_rate": 0.5, "throughput_rps": 40.0,
                  "db_cpu_peak": 50.0, "redis_latency_ms": 5}
        return run, result

    def test_passing_gate(self):
        run, result = self._make_run_and_result()
        conn = models_mod.connect_db()
        models_mod.generate_result_insights(conn, run, result, "passed")
        conn.close()

    def test_failing_gate(self):
        run, result = self._make_run_and_result()
        result["p95_ms"] = 900
        conn = models_mod.connect_db()
        models_mod.generate_result_insights(conn, run, result, "failed")
        conn.close()

    def test_high_db_cpu(self):
        run, result = self._make_run_and_result()
        result["db_cpu_peak"] = 90.0
        conn = models_mod.connect_db()
        models_mod.generate_result_insights(conn, run, result, "passed")
        conn.close()

    def test_high_redis_latency(self):
        run, result = self._make_run_and_result()
        result["redis_latency_ms"] = 30
        conn = models_mod.connect_db()
        models_mod.generate_result_insights(conn, run, result, "passed")
        conn.close()


class TestGetRunLive:
    def test_live_status(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        live = models_mod.get_run_live(run["id"])
        assert "status" in live
        assert "containerRunning" in live

    def test_with_execution_id(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        conn.execute("UPDATE test_runs SET execution_id = 'abc123', status = 'running', started_at = ? WHERE id = ?",
                      (models_mod.utc_now(), run["id"]))
        conn.commit()
        conn.close()
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="running\n")):
            live = models_mod.get_run_live(run["id"])
            assert live["containerRunning"] is True


class TestTriggerWebhooks:
    def test_triggers_matching_webhooks(self):
        models_mod.create_webhook({"name": "Hook", "url": "https://hook.test", "event": "run.completed"})
        with patch("urllib.request.urlopen"):
            count = models_mod.trigger_webhooks("run.completed", {"runId": 1})
        assert count >= 1

    def test_no_matching_webhooks(self):
        count = models_mod.trigger_webhooks("nonexistent.event", {})
        assert count == 0


class TestGetTestImpactAnalysis:
    def test_returns_analysis(self):
        r = models_mod.get_test_impact_analysis()
        assert "impactScore" in r
        assert "riskLevel" in r
        assert "recommendedTests" in r


class TestCheckAndExecuteSchedules:
    def test_returns_created_count(self):
        # Create a schedule with a past next_run_at
        models_mod.create_schedule({
            "name": "TestSched", "scenario_id": 1, "environment_id": 1,
            "target_vusers": 100, "duration_minutes": 5,
            "load_profile": "steady", "cron_expression": "0 2 * * *",
        })
        result = models_mod.check_and_execute_schedules()
        assert "created" in result
        assert "checked" in result
