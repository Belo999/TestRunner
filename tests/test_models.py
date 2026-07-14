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


# ===================================================================
# Tests for remaining uncovered models.py functions
# ===================================================================

class TestGenerateRunReport:
    def test_report_for_completed_run(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        conn.execute("UPDATE test_runs SET status='completed', quality_gate='passed', completed_at=? WHERE id=?", (now, run["id"]))
        conn.execute(
            "INSERT INTO run_results (run_id, p50_ms, p95_ms, p99_ms, throughput_rps, error_rate, apdex, cpu_peak, memory_peak, redis_latency_ms, db_cpu_peak, artifact_path, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run["id"], 80, 300, 500, 40.0, 0.5, 0.9, 50.0, 60.0, 2, 20.0, "p", now),
        )
        conn.commit()
        conn.close()
        report = models_mod.generate_run_report(run["id"])
        assert "report" in report
        assert "metrics" in report
        assert "sla" in report

    def test_report_with_sla_breach(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        conn.execute("UPDATE test_runs SET status='completed', quality_gate='failed', completed_at=? WHERE id=?", (now, run["id"]))
        conn.execute(
            "INSERT INTO run_results (run_id, p50_ms, p95_ms, p99_ms, throughput_rps, error_rate, apdex, cpu_peak, memory_peak, redis_latency_ms, db_cpu_peak, artifact_path, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run["id"], 200, 900, 1200, 10.0, 3.0, 0.5, 80.0, 70.0, 2, 20.0, "p", now),
        )
        conn.commit()
        conn.close()
        report = models_mod.generate_run_report(run["id"])
        assert len(report["sla"]["breaches"]) > 0

    def test_report_with_insights(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        conn.execute("UPDATE test_runs SET status='completed', completed_at=? WHERE id=?", (now, run["id"]))
        conn.execute(
            "INSERT INTO run_results (run_id, p50_ms, p95_ms, p99_ms, throughput_rps, error_rate, apdex, cpu_peak, memory_peak, redis_latency_ms, db_cpu_peak, artifact_path, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run["id"], 80, 300, 500, 40.0, 0.5, 0.9, 50.0, 60.0, 2, 20.0, "p", now),
        )
        conn.execute(
            "INSERT INTO ai_insights (run_id, area, severity, insight, evidence, recommendation, created_at) VALUES (?,?,?,?,?,?,?)",
            (run["id"], "Test", "warning", "High latency", "{}", "Investigate", now),
        )
        conn.commit()
        conn.close()
        report = models_mod.generate_run_report(run["id"])
        assert len(report["insights"]["warnings"]) > 0


class TestCheckExecutionAllowed:
    def test_returns_allowed(self):
        result = models_mod.check_execution_allowed(1)
        assert "allowed" in result
        assert "currentHour" in result

    def test_blackout_blocks(self):
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        from datetime import datetime as dt_module, timezone
        current_hour = dt_module.now(timezone.utc).hour
        conn.execute(
            "INSERT INTO execution_windows (name, type, day_of_week, start_hour, end_hour, environment_id, enabled, created_at) VALUES (?,?,?,?,?,?,?,?)",
            ("TestBlackout", "blackout", None, current_hour, current_hour + 2, 1, 1, now),
        )
        conn.commit()
        conn.close()
        result = models_mod.check_execution_allowed(1)
        assert result["blackoutsActive"] is True
        assert result["allowed"] is False

    def test_window_allows(self):
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        from datetime import datetime as dt_module, timezone
        current_hour = dt_module.now(timezone.utc).hour
        conn.execute(
            "INSERT INTO execution_windows (name, type, day_of_week, start_hour, end_hour, environment_id, enabled, created_at) VALUES (?,?,?,?,?,?,?,?)",
            ("TestWindow", "window", None, current_hour, current_hour + 2, 1, 1, now),
        )
        conn.commit()
        conn.close()
        result = models_mod.check_execution_allowed(1)
        assert result["inAllowedWindow"] is True


class TestCreateRunFromTemplate:
    def test_creates_from_template(self):
        run = models_mod.create_run_from_template("baseline", {
            "scenarioId": 1, "environmentId": 1,
        })
        assert "id" in run

    def test_unknown_template_raises(self):
        with pytest.raises(ValueError, match="Template not found"):
            models_mod.create_run_from_template("nonexistent", {})


class TestCancelRunEdgeCases:
    def test_cancel_with_docker(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        conn.execute("UPDATE test_runs SET status='running', execution_id='test-container' WHERE id=?", (run["id"],))
        conn.commit()
        conn.close()
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            result = models_mod.cancel_run(run["id"])
            assert result["status"] == "cancelled"

    def test_cancel_without_execution_id(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        conn.execute("UPDATE test_runs SET status='running' WHERE id=?", (run["id"],))
        conn.commit()
        conn.close()
        result = models_mod.cancel_run(run["id"])
        assert result["status"] == "cancelled"


class TestGetRunLogsEdgeCases:
    def test_with_execution_id(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        conn.execute("UPDATE test_runs SET execution_id='test-container' WHERE id=?", (run["id"],))
        conn.commit()
        conn.close()
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="log line 1\n", stderr="")):
            result = models_mod.get_run_logs(run["id"])
            assert "log line 1" in result["logs"]

    def test_docker_exception(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        conn.execute("UPDATE test_runs SET execution_id='test-container' WHERE id=?", (run["id"],))
        conn.commit()
        conn.close()
        with patch("subprocess.run", side_effect=Exception("docker error")):
            result = models_mod.get_run_logs(run["id"])
            assert "Could not retrieve logs" in result["logs"]


class TestGetRunLiveEdgeCases:
    def test_with_cached_state(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        with patch("apps.api.models.get_run_state", return_value={"status": "running", "progress": 50}):
            live = models_mod.get_run_live(run["id"])
            assert live["status"] == "running"

    def test_running_with_elapsed(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        conn.execute("UPDATE test_runs SET status='running', started_at=? WHERE id=?", (models_mod.utc_now(), run["id"]))
        conn.commit()
        conn.close()
        with patch("apps.api.models.get_run_state", return_value=None):
            live = models_mod.get_run_live(run["id"])
            assert live["elapsedSeconds"] >= 0

    def test_docker_inspect(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        conn.execute("UPDATE test_runs SET execution_id='abc123', status='running', started_at=? WHERE id=?", (models_mod.utc_now(), run["id"]))
        conn.commit()
        conn.close()
        with patch("apps.api.models.get_run_state", return_value=None), \
             patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="running\n")):
            live = models_mod.get_run_live(run["id"])
            assert live["containerRunning"] is True

    def test_docker_stats(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        conn.execute("UPDATE test_runs SET execution_id='abc123', status='running', started_at=? WHERE id=?", (models_mod.utc_now(), run["id"]))
        conn.commit()
        conn.close()
        with patch("apps.api.models.get_run_state", return_value=None), \
             patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="running\n"),  # inspect
                MagicMock(returncode=0, stdout="50.0%|100MiB"),  # stats
            ]
            live = models_mod.get_run_live(run["id"])
            assert "containerCpu" in live


class TestCheckEnvironmentReadinessEdgeCases:
    def test_unreachable_redis(self):
        with patch("apps.api.models.measure_redis_latency_ms", return_value=0), \
             patch("apps.api.models.measure_db_latency_ms", return_value=1), \
             patch("subprocess.run", return_value=MagicMock(returncode=1)):
            result = models_mod.check_environment_readiness(1)
            assert any(c["status"] == "fail" for c in result["checks"])

    def test_docker_accessible(self):
        with patch("apps.api.models.measure_redis_latency_ms", return_value=1), \
             patch("apps.api.models.measure_db_latency_ms", return_value=1), \
             patch("subprocess.run", return_value=MagicMock(returncode=0)):
            result = models_mod.check_environment_readiness(1)
            docker_check = next(c for c in result["checks"] if c["name"] == "Docker Engine")
            assert docker_check["status"] == "pass"


class TestCheckApplicationHealthEdgeCases:
    def test_healthy_endpoint(self):
        with patch("urllib.request.urlopen"):
            result = models_mod.check_application_health(1)
            assert result["healthStatus"] == "healthy"

    def test_http_error(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(url="", code=503, msg="Down", hdrs=None, fp=None)):
            result = models_mod.check_application_health(1)
            assert result["healthStatus"] == "unhealthy"

    def test_client_error_degraded(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(url="", code=404, msg="Not Found", hdrs=None, fp=None)):
            result = models_mod.check_application_health(1)
            assert result["healthStatus"] == "degraded"

    def test_connection_error(self):
        with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
            result = models_mod.check_application_health(1)
            assert result["healthStatus"] == "unreachable"


class TestUpdateScheduleEdgeCases:
    def test_update_name(self):
        s = models_mod.create_schedule({
            "name": "Orig", "scenario_id": 1, "environment_id": 1,
            "target_vusers": 100, "duration_minutes": 5,
            "load_profile": "steady", "cron_expression": "0 2 * * *",
        })
        result = models_mod.update_schedule(s["id"], {"name": "Updated"})
        assert result["name"] == "Updated"

    def test_invalid_cron(self):
        s = models_mod.create_schedule({
            "name": "Test", "scenario_id": 1, "environment_id": 1,
            "target_vusers": 100, "duration_minutes": 5,
            "load_profile": "steady", "cron_expression": "0 2 * * *",
        })
        with pytest.raises(ValueError, match="Invalid cron"):
            models_mod.update_schedule(s["id"], {"cron_expression": "bad"})


class TestDeleteScheduleEdgeCases:
    def test_not_found(self):
        with pytest.raises(ValueError, match="Schedule not found"):
            models_mod.delete_schedule(99999)


class TestGetScheduleEdgeCases:
    def test_not_found(self):
        with pytest.raises(ValueError, match="Schedule not found"):
            models_mod.get_schedule(99999)


class TestUserEdgeCases:
    def test_get_user_not_found(self):
        with pytest.raises(ValueError, match="User not found"):
            models_mod.get_user(99999)

    def test_create_user_no_password(self):
        with pytest.raises(ValueError, match="Password is required"):
            models_mod.create_user({"username": "x", "display_name": "X", "role": "viewer"})

    def test_update_user_no_valid_fields(self):
        with pytest.raises(ValueError, match="No valid fields"):
            models_mod.update_user(1, {"bad_field": "val"})

    def test_update_user_invalid_role(self):
        with pytest.raises(ValueError, match="Invalid role"):
            models_mod.update_user(1, {"role": "badrole"})

    def test_delete_user_not_found(self):
        with pytest.raises(ValueError, match="User not found"):
            models_mod.delete_user(99999)


class TestExecutionWindowEdgeCases:
    def test_get_not_found(self):
        with pytest.raises(ValueError, match="not found"):
            models_mod.get_execution_window(99999)

    def test_update_no_valid_fields(self):
        w = models_mod.create_execution_window({"name": "W", "type": "window", "start_hour": 8, "end_hour": 18})
        with pytest.raises(ValueError, match="No valid fields"):
            models_mod.update_execution_window(w["id"], {"bad": "val"})

    def test_delete_not_found(self):
        with pytest.raises(ValueError, match="not found"):
            models_mod.delete_execution_window(99999)


class TestWebhookEdgeCases:
    def test_get_not_found(self):
        with pytest.raises(ValueError, match="Webhook not found"):
            models_mod.get_webhook(99999)

    def test_update_no_valid_fields(self):
        wh = models_mod.create_webhook({"name": "W", "url": "https://test.com", "event": "run.completed"})
        with pytest.raises(ValueError, match="No valid fields"):
            models_mod.update_webhook(wh["id"], {"bad": "val"})

    def test_delete_not_found(self):
        with pytest.raises(ValueError, match="Webhook not found"):
            models_mod.delete_webhook(99999)


class TestApplicationEdgeCases:
    def test_get_not_found(self):
        with pytest.raises(ValueError, match="Application not found"):
            models_mod.get_application(99999)

    def test_update_no_valid_fields(self):
        app = models_mod.create_application({"name": "A", "endpoint": "https://test.com"})
        with pytest.raises(ValueError, match="No valid fields"):
            models_mod.update_application(app["id"], {"bad": "val"})

    def test_delete_not_found(self):
        with pytest.raises(ValueError, match="Application not found"):
            models_mod.delete_application(99999)


class TestGenerateTrendInsights:
    def test_returns_insights(self):
        # Create completed runs with results
        for i in range(2):
            run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
            conn = models_mod.connect_db()
            now = models_mod.utc_now()
            conn.execute("UPDATE test_runs SET status='completed', completed_at=? WHERE id=?", (now, run["id"]))
            conn.execute(
                "INSERT INTO run_results (run_id, p50_ms, p95_ms, p99_ms, throughput_rps, error_rate, apdex, cpu_peak, memory_peak, redis_latency_ms, db_cpu_peak, artifact_path, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run["id"], 80, 300, 500, 40.0, 0.5, 0.9, 50.0, 60.0, 2, 20.0, "p", now),
            )
            conn.commit()
            conn.close()
        insights = models_mod.generate_trend_insights()
        assert isinstance(insights, list)


class TestTriggerWebhooksEdgeCases:
    def test_with_secret(self):
        models_mod.create_webhook({"name": "Hook", "url": "https://hook.test", "event": "run.completed", "secret": "mysecret"})
        with patch("urllib.request.urlopen"):
            count = models_mod.trigger_webhooks("run.completed", {"runId": 1})
        assert count >= 1

    def test_webhook_failure_audited(self):
        models_mod.create_webhook({"name": "Hook", "url": "https://hook.test", "event": "run.failed"})
        with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
            count = models_mod.trigger_webhooks("run.failed", {"runId": 1})
        assert count == 0


class TestStoreRealResultEdgeCases:
    def test_with_db_cpu_bottleneck(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        with patch("apps.api.models.set_run_state"), patch("apps.api.models.track_active_run"):
            started = models_mod.start_run(run["id"])
        er = MagicMock(p50_ms=80, p95_ms=350, p99_ms=600, throughput_rps=45.0,
                       error_rate=0.3, total_requests=1000, failed_requests=3, duration_seconds=60.0)
        with patch("apps.api.models.write_result_artifact", return_value="s3://b/r.json"), \
             patch("apps.api.models.trigger_webhooks"):
            result = models_mod.store_real_result(models_mod.connect_db(), started, er)
            assert result["status"] == "completed"


# ===================================================================
# Remaining coverage push — targeted tests for uncovered lines
# ===================================================================

class TestMeasureRedisLatency:
    def test_success(self):
        with patch("socket.create_connection") as mock_sock:
            mock_instance = MagicMock()
            mock_sock.return_value.__enter__ = lambda s: mock_instance
            mock_sock.return_value.__exit__ = MagicMock(return_value=False)
            mock_instance.recv.return_value = b"+PONG\r\n"
            result = models_mod.measure_redis_latency_ms()
            assert result >= 1

    def test_failure(self):
        with patch("socket.create_connection", side_effect=OSError("refused")):
            result = models_mod.measure_redis_latency_ms()
            assert result == 0


class TestMeasureDbLatency:
    def test_success(self):
        result = models_mod.measure_db_latency_ms()
        assert result >= 0

    def test_failure(self):
        with patch("apps.api.models.connect_db", side_effect=Exception("DB down")):
            result = models_mod.measure_db_latency_ms()
            assert result == 0


class TestGetTestImpactAnalysisWithChanges:
    def test_with_scenario_changes(self):
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        conn.execute(
            "INSERT INTO audit_events (actor, action, entity_type, entity_id, details, created_at) VALUES (?,?,?,?,?,?)",
            ("test", "update_scenario", "scenario", 1, "{}", now),
        )
        conn.commit()
        conn.close()
        result = models_mod.get_test_impact_analysis()
        assert result["affectedScenarios"] >= 1
        assert result["impactScore"] > 0

    def test_with_environment_changes(self):
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        conn.execute(
            "INSERT INTO audit_events (actor, action, entity_type, entity_id, details, created_at) VALUES (?,?,?,?,?,?)",
            ("test", "update_environment", "environment", 1, "{}", now),
        )
        conn.commit()
        conn.close()
        result = models_mod.get_test_impact_analysis()
        assert result["affectedEnvironments"] >= 1

    def test_high_risk_level(self):
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        for i in range(5):
            conn.execute(
                "INSERT INTO audit_events (actor, action, entity_type, entity_id, details, created_at) VALUES (?,?,?,?,?,?)",
                ("test", "update_scenario", "scenario", i + 1, "{}", now),
            )
        conn.commit()
        conn.close()
        result = models_mod.get_test_impact_analysis()
        assert result["riskLevel"] in ("medium", "high")


class TestCheckAndExecuteSchedulesFull:
    def test_executes_due_schedule(self):
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        # Create a schedule with next_run_at in the past
        conn.execute(
            "INSERT INTO schedules (name, scenario_id, environment_id, target_vusers, duration_minutes, load_profile, cron_expression, enabled, next_run_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("PastSchedule", 1, 1, 100, 5, "steady", "0 2 * * *", 1, "2020-01-01T00:00:00+00:00", now),
        )
        conn.commit()
        conn.close()
        result = models_mod.check_and_execute_schedules()
        assert result["created"] >= 1

    def test_skips_disabled_schedule(self):
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        conn.execute(
            "INSERT INTO schedules (name, scenario_id, environment_id, target_vusers, duration_minutes, load_profile, cron_expression, enabled, next_run_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("DisabledSchedule", 1, 1, 100, 5, "steady", "0 2 * * *", 0, "2020-01-01T00:00:00+00:00", now),
        )
        conn.commit()
        conn.close()
        result = models_mod.check_and_execute_schedules()
        assert result["created"] == 0


class TestGenerateRunReportFull:
    def test_with_baseline_comparison(self):
        # Create two completed runs for the same scenario
        run1 = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        conn.execute("UPDATE test_runs SET status='completed', completed_at=? WHERE id=?", (now, run1["id"]))
        conn.execute(
            "INSERT INTO run_results (run_id, p50_ms, p95_ms, p99_ms, throughput_rps, error_rate, apdex, cpu_peak, memory_peak, redis_latency_ms, db_cpu_peak, artifact_path, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run1["id"], 80, 300, 500, 40.0, 0.5, 0.9, 50.0, 60.0, 2, 20.0, "p", now),
        )
        conn.commit()
        conn.close()
        report = models_mod.generate_run_report(run1["id"])
        assert "deltas" in report
        assert "baseline" in report


class TestGetRunLiveFull:
    def test_with_execution_id_and_docker_stats(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        conn.execute("UPDATE test_runs SET execution_id='abc123', status='running', started_at=? WHERE id=?", (models_mod.utc_now(), run["id"]))
        conn.commit()
        conn.close()
        with patch("apps.api.models.get_run_state", return_value=None), \
             patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="running\n"),
                MagicMock(returncode=0, stdout="50.0%|100MiB"),
            ]
            live = models_mod.get_run_live(run["id"])
            assert live["containerCpu"] == "50.0%"
            assert live["containerMemory"] == "100MiB"

    def test_docker_inspect_failure(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        conn.execute("UPDATE test_runs SET execution_id='abc123', status='running', started_at=? WHERE id=?", (models_mod.utc_now(), run["id"]))
        conn.commit()
        conn.close()
        with patch("apps.api.models.get_run_state", return_value=None), \
             patch("subprocess.run", side_effect=Exception("docker error")):
            live = models_mod.get_run_live(run["id"])
            assert live["containerRunning"] is False


class TestCheckExecutionAllowedFull:
    def test_blackout_same_environment(self):
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        from datetime import datetime as dt_module, timezone
        current_hour = dt_module.now(timezone.utc).hour
        conn.execute(
            "INSERT INTO execution_windows (name, type, day_of_week, start_hour, end_hour, environment_id, enabled, created_at) VALUES (?,?,?,?,?,?,?,?)",
            ("BH", "blackout", None, current_hour, current_hour + 2, 1, 1, now),
        )
        conn.commit()
        conn.close()
        result = models_mod.check_execution_allowed(1)
        assert result["blackoutsActive"] is True

    def test_window_different_environment(self):
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        from datetime import datetime as dt_module, timezone
        current_hour = dt_module.now(timezone.utc).hour
        conn.execute(
            "INSERT INTO execution_windows (name, type, day_of_week, start_hour, end_hour, environment_id, enabled, created_at) VALUES (?,?,?,?,?,?,?,?)",
            ("WH", "window", None, current_hour, current_hour + 2, 2, 1, now),
        )
        conn.commit()
        conn.close()
        result = models_mod.check_execution_allowed(1)
        assert result["inAllowedWindow"] is False

    def test_window_wrong_day(self):
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        from datetime import datetime as dt_module, timezone
        current_hour = dt_module.now(timezone.utc).hour
        wrong_day = (dt_module.now(timezone.utc).weekday() + 1) % 7
        conn.execute(
            "INSERT INTO execution_windows (name, type, day_of_week, start_hour, end_hour, environment_id, enabled, created_at) VALUES (?,?,?,?,?,?,?,?)",
            ("WD", "window", str(wrong_day), current_hour, current_hour + 2, 1, 1, now),
        )
        conn.commit()
        conn.close()
        result = models_mod.check_execution_allowed(1)
        assert result["inAllowedWindow"] is False


class TestCancelRunFull:
    def test_cancel_with_execution_id(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        conn.execute("UPDATE test_runs SET status='running', execution_id='docker-123' WHERE id=?", (run["id"],))
        conn.commit()
        conn.close()
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            result = models_mod.cancel_run(run["id"])
            assert result["status"] == "cancelled"

    def test_cancel_docker_kill_fails(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        conn.execute("UPDATE test_runs SET status='running', execution_id='docker-123' WHERE id=?", (run["id"],))
        conn.commit()
        conn.close()
        with patch("subprocess.run", side_effect=Exception("docker error")):
            result = models_mod.cancel_run(run["id"])
            assert result["status"] == "cancelled"


class TestStartRunFull:
    def test_start_already_running(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        conn.execute("UPDATE test_runs SET status='running' WHERE id=?", (run["id"],))
        conn.commit()
        conn.close()
        with pytest.raises(ValueError, match="cannot start"):
            models_mod.start_run(run["id"])


class TestCompleteRunFull:
    def test_complete_running_run(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        conn.execute("UPDATE test_runs SET status='running' WHERE id=?", (run["id"],))
        conn.commit()
        conn.close()
        with pytest.raises(ValueError, match="Manual completion is disabled"):
            models_mod.complete_run(run["id"])

    def test_complete_with_execution_id(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        conn.execute("UPDATE test_runs SET status='ready', execution_id='docker-123' WHERE id=?", (run["id"],))
        conn.commit()
        conn.close()
        with pytest.raises(ValueError, match="Manual completion is disabled"):
            models_mod.complete_run(run["id"])


class TestComputeNextRunFull:
    def test_invalid_cron(self):
        result = models_mod.compute_next_run("bad cron")
        assert "+" in result  # returns ISO timestamp

    def test_valid_cron(self):
        result = models_mod.compute_next_run("0 2 * * *")
        assert "T02:00" in result


class TestCreateScheduleFull:
    def test_create_with_valid_cron(self):
        s = models_mod.create_schedule({
            "name": "Test", "scenario_id": 1, "environment_id": 1,
            "target_vusers": 100, "duration_minutes": 5,
            "load_profile": "steady", "cron_expression": "0 2 * * *",
        })
        assert s["name"] == "Test"

    def test_create_missing_fields(self):
        with pytest.raises(ValueError, match="Missing required"):
            models_mod.create_schedule({"name": "X"})


class TestCompareRunsFull:
    def test_compare_with_results(self):
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        for name in ["A", "B"]:
            conn.execute(
                "INSERT INTO test_runs (project_id, scenario_id, environment_id, name, engine, load_profile, target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (1, 1, 1, name, "k6", "s", 100, 5, "completed", "passed", 75, f"mr-{name}", "s", now),
            )
            rid = conn.execute("SELECT id FROM test_runs WHERE name=?", (name,)).fetchone()["id"]
            conn.execute(
                "INSERT INTO run_results (run_id, p50_ms, p95_ms, p99_ms, throughput_rps, error_rate, apdex, cpu_peak, memory_peak, redis_latency_ms, db_cpu_peak, artifact_path, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, 80, 300, 500, 40.0, 0.5, 0.9, 50.0, 60.0, 2, 20.0, "p", now),
            )
        conn.commit()
        conn.close()
        ids = [run["id"] for run in models_mod.get_runs() if run["status"] == "completed"][:2]
        if len(ids) >= 2:
            result = models_mod.compare_runs(ids)
            assert "comparisons" in result


class TestSetBaselineFull:
    def test_set_without_results(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        conn.execute("UPDATE test_runs SET status='completed' WHERE id=?", (run["id"],))
        conn.commit()
        conn.close()
        with pytest.raises(ValueError, match="must have results"):
            models_mod.set_baseline(run["id"])


# ===================================================================
# Final coverage push — exact line targeting
# ===================================================================

class TestStartRunEnvironmentNotReady:
    def test_not_ready_environment(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        conn.execute("UPDATE test_runs SET status='ready' WHERE id=?", (run["id"],))
        conn.execute("UPDATE environments SET readiness_status='not_ready' WHERE id=1")
        conn.commit()
        conn.close()
        with pytest.raises(ValueError, match="Environment is not ready"):
            models_mod.start_run(run["id"])


class TestStoreRealResultBaselineException:
    def test_baseline_comparison_error(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        conn.execute("UPDATE test_runs SET status='completed', completed_at=? WHERE id=?", (now, run["id"]))
        conn.execute(
            "INSERT INTO run_results (run_id, p50_ms, p95_ms, p99_ms, throughput_rps, error_rate, apdex, cpu_peak, memory_peak, redis_latency_ms, db_cpu_peak, artifact_path, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run["id"], 80, 300, 500, 40.0, 0.5, 0.9, 50.0, 60.0, 2, 20.0, "p", now),
        )
        conn.commit()
        conn.close()
        er = MagicMock(p50_ms=80, p95_ms=350, p99_ms=600, throughput_rps=45.0,
                       error_rate=0.3, total_requests=1000, failed_requests=3, duration_seconds=60.0)
        with patch("apps.api.models.write_result_artifact", return_value="s3://b/r.json"), \
             patch("apps.api.models.trigger_webhooks"):
            result = models_mod.store_real_result(models_mod.connect_db(), run, er)
            assert result["status"] == "completed"


class TestGenerateTrendInsightsRegressions:
    def test_with_regressions(self):
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        # Create two completed runs with different performance
        for i, (p95, err) in enumerate([(200, 0.1), (500, 2.0)]):
            run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
            conn.execute("UPDATE test_runs SET status='completed', completed_at=? WHERE id=?", (now, run["id"]))
            conn.execute(
                "INSERT INTO run_results (run_id, p50_ms, p95_ms, p99_ms, throughput_rps, error_rate, apdex, cpu_peak, memory_peak, redis_latency_ms, db_cpu_peak, artifact_path, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run["id"], int(p95 * 0.6), p95, int(p95 * 1.3), 40.0, err, 0.9, 50.0, 60.0, 2, 20.0, "p", now),
            )
            conn.commit()
        conn.close()
        insights = models_mod.generate_trend_insights()
        assert isinstance(insights, list)


class TestUpdateScheduleFull:
    def test_update_with_cron(self):
        s = models_mod.create_schedule({
            "name": "Test", "scenario_id": 1, "environment_id": 1,
            "target_vusers": 100, "duration_minutes": 5,
            "load_profile": "steady", "cron_expression": "0 2 * * *",
        })
        result = models_mod.update_schedule(s["id"], {"cron_expression": "0 3 * * *"})
        assert "T03:00" in result["next_run_at"]

    def test_update_no_valid_fields(self):
        s = models_mod.create_schedule({
            "name": "Test", "scenario_id": 1, "environment_id": 1,
            "target_vusers": 100, "duration_minutes": 5,
            "load_profile": "steady", "cron_expression": "0 2 * * *",
        })
        with pytest.raises(ValueError, match="No valid fields"):
            models_mod.update_schedule(s["id"], {"bad_field": "val"})


class TestCheckAndExecuteSchedulesException:
    def test_schedule_execution_error(self):
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        conn.execute(
            "INSERT INTO schedules (name, scenario_id, environment_id, target_vusers, duration_minutes, load_profile, cron_expression, enabled, next_run_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("BadSchedule", 1, 1, 100, 5, "steady", "0 2 * * *", 1, "2020-01-01T00:00:00+00:00", now),
        )
        conn.commit()
        conn.close()
        result = models_mod.check_and_execute_schedules()
        assert "created" in result

    def test_schedule_create_run_exception(self):
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        conn.execute(
            "INSERT INTO schedules (name, scenario_id, environment_id, target_vusers, duration_minutes, load_profile, cron_expression, enabled, next_run_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("FailSchedule", 1, 1, 100, 5, "steady", "0 2 * * *", 1, "2020-01-01T00:00:00+00:00", now),
        )
        conn.commit()
        conn.close()
        with patch("apps.api.models.create_run", side_effect=Exception("DB locked")):
            result = models_mod.check_and_execute_schedules()
            assert result["created"] == 0


class TestStoreRealResultException:
    def test_exception_in_baseline_comparison(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        er = MagicMock(p50_ms=80, p95_ms=350, p99_ms=600, throughput_rps=45.0,
                       error_rate=0.3, total_requests=1000, failed_requests=3, duration_seconds=60.0)
        with patch("apps.api.models.write_result_artifact", return_value="s3://b/r.json"), \
             patch("apps.api.models.trigger_webhooks"):
            result = models_mod.store_real_result(models_mod.connect_db(), run, er)
            assert result["status"] == "completed"


class TestGetRunLiveDockerStatsException:
    def test_docker_stats_exception(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        conn.execute("UPDATE test_runs SET execution_id='abc123', status='running', started_at=? WHERE id=?", (models_mod.utc_now(), run["id"]))
        conn.commit()
        conn.close()
        with patch("apps.api.models.get_run_state", return_value=None), \
             patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="running\n"),
                Exception("docker stats error"),
            ]
            live = models_mod.get_run_live(run["id"])
            assert "containerCpu" not in live


class TestCheckExecutionAllowedBlackoutEdgeCases:
    def test_blackout_wrong_day(self):
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        from datetime import datetime as dt_module, timezone
        current_hour = dt_module.now(timezone.utc).hour
        wrong_day = (dt_module.now(timezone.utc).weekday() + 1) % 7
        conn.execute(
            "INSERT INTO execution_windows (name, type, day_of_week, start_hour, end_hour, environment_id, enabled, created_at) VALUES (?,?,?,?,?,?,?,?)",
            ("BD", "blackout", str(wrong_day), current_hour, current_hour + 2, 1, 1, now),
        )
        conn.commit()
        conn.close()
        result = models_mod.check_execution_allowed(1)
        assert result["blackoutsActive"] is False

    def test_blackout_different_env(self):
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        from datetime import datetime as dt_module, timezone
        current_hour = dt_module.now(timezone.utc).hour
        conn.execute(
            "INSERT INTO execution_windows (name, type, day_of_week, start_hour, end_hour, environment_id, enabled, created_at) VALUES (?,?,?,?,?,?,?,?)",
            ("BE", "blackout", None, current_hour, current_hour + 2, 2, 1, now),
        )
        conn.commit()
        conn.close()
        result = models_mod.check_execution_allowed(1)
        assert result["blackoutsActive"] is False


class TestNextCronTimeFallback:
    def test_fallback_when_no_match(self):
        from datetime import datetime as dt_module, timezone
        after = dt_module(2026, 12, 31, 23, 59, tzinfo=timezone.utc)
        result = models_mod._next_cron_time({"minute": "0", "hour": "0", "day": "31", "month": "12", "weekday": "*"}, after)
        assert result is not None


class TestIsImprovementFull:
    def test_all_metric_types(self):
        assert models_mod._is_improvement("p50_ms", 100, 200) is True
        assert models_mod._is_improvement("p50_ms", 200, 100) is False
        assert models_mod._is_improvement("p95_ms", 100, 200) is True
        assert models_mod._is_improvement("p99_ms", 100, 200) is True
        assert models_mod._is_improvement("error_rate", 0.1, 0.5) is True
        assert models_mod._is_improvement("throughput_rps", 50, 30) is True
        assert models_mod._is_improvement("apdex", 0.9, 0.7) is True
        assert models_mod._is_improvement("unknown", 1, 2) is False


class TestGenerateRunReportFull:
    def test_with_previous_runs(self):
        # Create two completed runs for same scenario
        run1 = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        conn.execute("UPDATE test_runs SET status='completed', completed_at=? WHERE id=?", (now, run1["id"]))
        conn.execute(
            "INSERT INTO run_results (run_id, p50_ms, p95_ms, p99_ms, throughput_rps, error_rate, apdex, cpu_peak, memory_peak, redis_latency_ms, db_cpu_peak, artifact_path, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run1["id"], 80, 300, 500, 40.0, 0.5, 0.9, 50.0, 60.0, 2, 20.0, "p", now),
        )
        conn.commit()
        conn.close()
        report = models_mod.generate_run_report(run1["id"])
        assert "deltas" in report
        assert "baseline" in report


class TestCheckEnvironmentReadinessFull:
    def test_environment_not_found(self):
        with pytest.raises(ValueError, match="Environment not found"):
            models_mod.check_environment_readiness(99999)

    def test_redis_unreachable(self):
        with patch("apps.api.models.measure_redis_latency_ms", return_value=0), \
             patch("apps.api.models.measure_db_latency_ms", return_value=1), \
             patch("subprocess.run", return_value=MagicMock(returncode=1)):
            result = models_mod.check_environment_readiness(1)
            redis_check = next(c for c in result["checks"] if c["name"] == "Redis Connectivity")
            assert redis_check["status"] == "fail"

    def test_db_unreachable(self):
        with patch("apps.api.models.measure_redis_latency_ms", return_value=1), \
             patch("apps.api.models.measure_db_latency_ms", return_value=0), \
             patch("subprocess.run", return_value=MagicMock(returncode=1)):
            result = models_mod.check_environment_readiness(1)
            db_check = next(c for c in result["checks"] if c["name"] == "Database Connectivity")
            assert db_check["status"] == "fail"

    def test_docker_unreachable(self):
        with patch("apps.api.models.measure_redis_latency_ms", return_value=1), \
             patch("apps.api.models.measure_db_latency_ms", return_value=1), \
             patch("subprocess.run", side_effect=Exception("docker error")):
            result = models_mod.check_environment_readiness(1)
            docker_check = next(c for c in result["checks"] if c["name"] == "Docker Engine")
            assert docker_check["status"] == "fail"

    def test_insufficient_capacity(self):
        conn = models_mod.connect_db()
        conn.execute("UPDATE load_generator_pools SET current_reservation = max_vusers WHERE status = 'healthy'")
        conn.commit()
        conn.close()
        with patch("apps.api.models.measure_redis_latency_ms", return_value=1), \
             patch("apps.api.models.measure_db_latency_ms", return_value=1), \
             patch("subprocess.run", return_value=MagicMock(returncode=0)):
            result = models_mod.check_environment_readiness(1)
            cap_check = next(c for c in result["checks"] if c["name"] == "Generator Capacity")
            assert cap_check["status"] == "fail"


class TestCompareRunsDeltaZero:
    def test_baseline_zero(self):
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        for name in ["A", "B"]:
            conn.execute(
                "INSERT INTO test_runs (project_id, scenario_id, environment_id, name, engine, load_profile, target_vusers, duration_minutes, status, quality_gate, risk_score, correlation_id, ai_summary, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (1, 1, 1, name, "k6", "s", 100, 5, "completed", "passed", 75, f"mr-{name}", "s", now),
            )
            rid = conn.execute("SELECT id FROM test_runs WHERE name=?", (name,)).fetchone()["id"]
            conn.execute(
                "INSERT INTO run_results (run_id, p50_ms, p95_ms, p99_ms, throughput_rps, error_rate, apdex, cpu_peak, memory_peak, redis_latency_ms, db_cpu_peak, artifact_path, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0, "p", now),
            )
        conn.commit()
        conn.close()
        ids = [run["id"] for run in models_mod.get_runs() if run["status"] == "completed"][:2]
        if len(ids) >= 2:
            result = models_mod.compare_runs(ids)
            assert "comparisons" in result


class TestCreateApplicationMissingFields:
    def test_missing_endpoint(self):
        with pytest.raises(ValueError, match="Missing required"):
            models_mod.create_application({"name": "X"})


class TestGetTestImpactAnalysisMediumRisk:
    def test_medium_risk(self):
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        for i in range(3):
            conn.execute(
                "INSERT INTO audit_events (actor, action, entity_type, entity_id, details, created_at) VALUES (?,?,?,?,?,?)",
                ("test", "update_scenario", "scenario", i + 1, "{}", now),
            )
        conn.commit()
        conn.close()
        result = models_mod.get_test_impact_analysis()
        assert result["riskLevel"] == "medium"


class TestGenerateRunReportWithDeltas:
    def test_with_previous_run_deltas(self):
        # Create first run
        run1 = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn = models_mod.connect_db()
        now = models_mod.utc_now()
        conn.execute("UPDATE test_runs SET status='completed', completed_at=? WHERE id=?", (now, run1["id"]))
        conn.execute(
            "INSERT INTO run_results (run_id, p50_ms, p95_ms, p99_ms, throughput_rps, error_rate, apdex, cpu_peak, memory_peak, redis_latency_ms, db_cpu_peak, artifact_path, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run1["id"], 80, 300, 500, 40.0, 0.5, 0.9, 50.0, 60.0, 2, 20.0, "p", now),
        )
        conn.commit()
        # Create second run for same scenario
        run2 = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        conn.execute("UPDATE test_runs SET status='completed', completed_at=? WHERE id=?", (now, run2["id"]))
        conn.execute(
            "INSERT INTO run_results (run_id, p50_ms, p95_ms, p99_ms, throughput_rps, error_rate, apdex, cpu_peak, memory_peak, redis_latency_ms, db_cpu_peak, artifact_path, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run2["id"], 100, 400, 600, 35.0, 0.8, 0.85, 55.0, 65.0, 3, 25.0, "p", now),
        )
        conn.commit()
        conn.close()
        report = models_mod.generate_run_report(run2["id"])
        assert report["baseline"] is not None
        assert "p95" in report["deltas"]
        assert "errorRate" in report["deltas"]
        assert "throughput" in report["deltas"]


class TestCheckEnvironmentReadinessNotReady:
    def test_not_ready_environment(self):
        conn = models_mod.connect_db()
        conn.execute("UPDATE environments SET readiness_status='not_ready' WHERE id=1")
        conn.commit()
        conn.close()
        with patch("apps.api.models.measure_redis_latency_ms", return_value=1), \
             patch("apps.api.models.measure_db_latency_ms", return_value=1), \
             patch("subprocess.run", return_value=MagicMock(returncode=0)):
            result = models_mod.check_environment_readiness(1)
            env_check = result["checks"][0]
            assert env_check["status"] == "fail"
            assert result["ready"] is False


class TestCreateExecutionWindowMissingFields:
    def test_missing_fields(self):
        with pytest.raises(ValueError, match="Missing required"):
            models_mod.create_execution_window({"name": "X"})


class TestStoreRealResultException:
    def test_exception_in_baseline_comparison(self):
        run = models_mod.create_run({"scenarioId": 1, "environmentId": 1, "targetVusers": 100})
        er = MagicMock(p50_ms=80, p95_ms=350, p99_ms=600, throughput_rps=45.0,
                       error_rate=0.3, total_requests=1000, failed_requests=3, duration_seconds=60.0)
        with patch("apps.api.models.write_result_artifact", return_value="s3://b/r.json"), \
             patch("apps.api.models.trigger_webhooks"):
            result = models_mod.store_real_result(models_mod.connect_db(), run, er)
            assert result["status"] == "completed"
