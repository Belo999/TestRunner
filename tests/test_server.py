"""Tests for apps.api.server — REST API endpoint coverage."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Generator, Tuple
from unittest.mock import MagicMock, patch

import pytest

from apps.api.auth import generate_token

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decode(token) -> str:
    return token.decode("utf-8") if isinstance(token, bytes) else token


ADMIN = f"Bearer {_decode(generate_token(1, 'admin', 'admin', 'Admin'))}"
LEAD = f"Bearer {_decode(generate_token(2, 'lead', 'performance_lead', 'Lead'))}"
ENGINEER = f"Bearer {_decode(generate_token(3, 'engineer', 'engineer', 'Engineer'))}"
VIEWER = f"Bearer {_decode(generate_token(4, 'viewer', 'viewer', 'Viewer'))}"


def _api(method: str, url: str, token: str | None = None, data: dict | None = None) -> Tuple[int, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = token
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req)
        ct = resp.headers.get("Content-Type", "")
        if "json" in ct:
            return resp.status, json.loads(resp.read())
        return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        ct = e.headers.get("Content-Type", "")
        if "json" in ct:
            return e.code, json.loads(e.read())
        return e.code, e.read()


def _get(url, token=None):
    return _api("GET", url, token)


def _post(url, data, token=None):
    return _api("POST", url, token, data)


def _put(url, data, token=None):
    return _api("PUT", url, token, data)


def _delete(url, token=None):
    return _api("DELETE", url, token)


# ---------------------------------------------------------------------------
# Schema extraction
# ---------------------------------------------------------------------------

def _extract_schema() -> str:
    source = (Path(__file__).resolve().parents[1] / "apps" / "api" / "database.py").read_text()
    match = re.search(r'SCHEMA_SQLITE\s*=\s"""(.*?)"""', source, re.DOTALL)
    return match.group(1) if match else ""


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

def _seed_data(conn: sqlite3.Connection) -> None:
    """Insert minimal seed data for endpoint tests."""
    now = "2026-07-14T10:00:00+00:00"

    # Projects
    for name, owner, bu, risk in [
        ("Checkout Platform", "Performance Engineering", "Digital Commerce", "critical"),
        ("Customer API", "Platform Team", "Shared Services", "high"),
    ]:
        conn.execute("INSERT INTO projects (name, owner, business_unit, risk_tier, created_at) VALUES (?,?,?,?,?)",
                      (name, owner, bu, risk, now))

    # Environments
    for name, region, cls_, readiness in [
        ("performance", "af-south-1", "production-like", "ready"),
        ("integration", "eu-west-1", "shared", "warning"),
    ]:
        conn.execute("INSERT INTO environments (name, region, classification, readiness_status, service_virtualization_enabled, data_residency, updated_at) VALUES (?,?,?,?,?,?,?)",
                      (name, region, cls_, readiness, 1, "ZA", now))

    # Scenarios
    conn.execute("INSERT INTO scenarios (project_id, name, engine, test_type, workload_mix, script_repository, target_endpoint, sla_p95_ms, max_error_rate, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (1, "Month-End Checkout Baseline", "JMeter", "load", "70% browse", "git@test.git", "http://localhost:8080", 850, 1.0, now))
    conn.execute("INSERT INTO scenarios (project_id, name, engine, test_type, workload_mix, script_repository, target_endpoint, sla_p95_ms, max_error_rate, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (2, "Customer API Smoke", "k6", "smoke", "60% read", "git@test.git", "http://localhost:8080", 450, 0.5, now))

    # Pools
    conn.execute("INSERT INTO load_generator_pools (name, region, engines, max_vusers, status, current_reservation, updated_at) VALUES (?,?,?,?,?,?,?)",
                  ("af-south-1-general", "af-south-1", '["JMeter","k6"]', 12000, "healthy", 0, now))

    # Policies
    conn.execute("INSERT INTO policies (name, scope, rule, severity, enabled) VALUES (?,?,?,?,?)",
                  ("Production-like approval", "execution", "Require approval for critical projects", "blocking", 1))

    # Users (with password hashes for login tests)
    from apps.api.auth import hash_password, DEFAULT_PASSWORD
    pw_hash, pw_salt = hash_password(DEFAULT_PASSWORD)
    for uname, display, role in [
        ("admin", "System Administrator", "admin"),
        ("lead", "Performance Lead", "performance_lead"),
        ("engineer", "Test Engineer", "engineer"),
        ("viewer", "Stakeholder Viewer", "viewer"),
    ]:
        conn.execute("INSERT INTO users (username, display_name, role, email, password_hash, password_salt, created_at) VALUES (?,?,?,?,?,?,?)",
                      (uname, display, role, f"{uname}@test.local", pw_hash, pw_salt, now))

    # Webhook
    conn.execute("INSERT INTO webhooks (name, url, event, enabled, secret, created_at) VALUES (?,?,?,?,?,?)",
                  ("Test Hook", "https://hooks.test/event", "run.completed", 1, "", now))

    # Application
    conn.execute("INSERT INTO applications (name, endpoint, team, environment, status, created_at) VALUES (?,?,?,?,?,?)",
                  ("Checkout App", "https://checkout.test", "Team A", "production", "active", now))

    # Execution window
    conn.execute("INSERT INTO execution_windows (name, type, day_of_week, start_hour, end_hour, environment_id, enabled, created_at) VALUES (?,?,?,?,?,?,?,?)",
                  ("Business Hours", "window", "1-5", 8, 18, 1, 1, now))

    # Test runs: 2 completed + 1 running + 1 pending_approval
    import secrets
    for i, (pid, sid, eid, name, engine, vusers, dur, status, gate, risk) in enumerate([
        (1, 1, 1, "Checkout Baseline", "JMeter", 8000, 70, "completed", "passed", 22),
        (2, 2, 2, "API Smoke", "k6", 100, 2, "completed", "passed", 15),
        (1, 1, 1, "Checkout Nightly", "JMeter", 1000, 30, "running", "not_evaluated", 40),
        (1, 1, 1, "Checkout Draft", "JMeter", 500, 15, "pending_approval", "not_evaluated", 30),
    ], start=1):
        corr = f"mr-{secrets.token_hex(6)}"
        started = now if status in ("completed", "running") else None
        completed = now if status == "completed" else None
        conn.execute(
            """INSERT INTO test_runs
            (project_id, scenario_id, environment_id, name, engine, load_profile,
             target_vusers, duration_minutes, status, quality_gate, risk_score,
             correlation_id, ai_summary, created_at, started_at, completed_at, is_baseline)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pid, sid, eid, name, engine, "Ramp", vusers, dur, status, gate, risk,
             corr, f"Summary for {name}", now, started, completed, 1 if i == 1 else 0),
        )
        # Results for completed runs
        if status == "completed":
            conn.execute(
                """INSERT INTO run_results
                (run_id, p50_ms, p95_ms, p99_ms, throughput_rps, error_rate, apdex, cpu_peak,
                 memory_peak, redis_latency_ms, db_cpu_peak, artifact_path, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (i, 80, 300, 500, 40.0, 0.5, 0.9, 50.0, 60.0, 2, 20.0, f"artifacts/run-{i}.json", now),
            )

    # Approval record for pending run
    conn.execute("INSERT INTO approvals (run_id, status, reviewer, reason, created_at) VALUES (?,?,?,?,?)",
                  (4, "pending", "performance-lead", "Needs review", now))

    # Schedule
    conn.execute("INSERT INTO schedules (name, scenario_id, environment_id, target_vusers, duration_minutes, load_profile, cron_expression, enabled, next_run_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  ("Nightly", 1, 1, 500, 10, "steady", "0 2 * * *", 1, "2026-07-15T02:00:00+00:00", now))

    conn.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def server_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[str, None, None]:
    """Start a real ThreadingHTTPServer with an isolated DB for each test."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("MARATHONRUNNER_DB_PATH", str(db_path))
    monkeypatch.setattr("apps.api.database.DB_PATH", db_path)
    monkeypatch.setattr("apps.api.models.ARTIFACT_DIR", tmp_path / "artifacts")

    # Create schema + seed
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_extract_schema())
    # Run migrations (adds created_at to environments/pools, execution_id to test_runs, etc.)
    from apps.api.database import DatabaseConnection
    db_conn = DatabaseConnection(conn, "sqlite")
    from apps.api.database import migrate_database
    migrate_database(db_conn)
    _seed_data(conn)
    conn.close()

    # Mock externals
    with patch("apps.api.redis_cache.ping_redis", return_value=True), \
         patch("apps.api.storage.check_object_storage", return_value=True), \
         patch("subprocess.run", return_value=MagicMock(returncode=0)):
        server = ThreadingHTTPServer(("127.0.0.1", 0), __import__("apps.api.server", fromlist=["MarathonRunnerHandler"]).MarathonRunnerHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.shutdown()
            server.server_close()


# ===================================================================
# Tier 1 — Core endpoints
# ===================================================================

class TestHealthAndStatic:
    def test_health(self, server_url):
        status, body = _get(f"{server_url}/api/health")
        assert status == 200
        assert body["service"] == "marathonrunner-enterprise"
        assert "engines" in body

    def test_docs(self, server_url):
        status, body = _get(f"{server_url}/docs")
        assert status == 200

    def test_swagger(self, server_url):
        status, body = _get(f"{server_url}/swagger")
        assert status == 200

    def test_openapi_not_found(self, server_url):
        status, _ = _get(f"{server_url}/api/openapi.json")
        assert status in (200, 404)

    def test_404(self, server_url):
        status, _ = _get(f"{server_url}/api/nonexistent")
        assert status in (401, 404)

    def test_auth_required(self, server_url):
        status, _ = _get(f"{server_url}/api/dashboard")
        assert status == 401


class TestAuth:
    def test_login_success(self, server_url):
        status, body = _post(f"{server_url}/api/auth/login", {"username": "admin", "password": "marathonrunner"})
        assert status == 200
        assert "token" in body
        assert body["user"]["username"] == "admin"

    def test_login_wrong_password(self, server_url):
        status, body = _post(f"{server_url}/api/auth/login", {"username": "admin", "password": "wrong"})
        assert status == 401

    def test_login_unknown_user(self, server_url):
        status, body = _post(f"{server_url}/api/auth/login", {"username": "nobody", "password": "x"})
        assert status == 401

    def test_login_missing_username(self, server_url):
        status, body = _post(f"{server_url}/api/auth/login", {"password": "marathonrunner"})
        assert status == 400

    def test_auth_me(self, server_url):
        status, body = _get(f"{server_url}/api/auth/me", ADMIN)
        assert status == 200
        assert body["user"]["username"] == "admin"

    def test_auth_me_no_token(self, server_url):
        status, _ = _get(f"{server_url}/api/auth/me")
        assert status == 401

    def test_auth_me_bad_token(self, server_url):
        status, _ = _get(f"{server_url}/api/auth/me", "Bearer invalid.token.here")
        assert status == 401

    def test_invalid_token(self, server_url):
        status, _ = _get(f"{server_url}/api/dashboard", "Bearer garbage")
        assert status == 401


class TestDashboard:
    def test_returns_counts(self, server_url):
        status, body = _get(f"{server_url}/api/dashboard", ADMIN)
        assert status == 200
        assert "counts" in body

    def test_viewer_can_access(self, server_url):
        status, body = _get(f"{server_url}/api/dashboard", VIEWER)
        assert status == 200


class TestProjects:
    def test_list(self, server_url):
        status, body = _get(f"{server_url}/api/projects", ADMIN)
        assert status == 200
        assert len(body["projects"]) >= 2

    def test_create(self, server_url):
        status, body = _post(f"{server_url}/api/projects",
                             {"name": "New Proj", "owner": "Team", "business_unit": "BU", "risk_tier": "low"}, ADMIN)
        assert status == 201
        assert body["project"]["name"] == "New Proj"

    def test_update(self, server_url):
        status, body = _put(f"{server_url}/api/projects/1", {"name": "Updated"}, ADMIN)
        assert status == 200
        assert body["project"]["name"] == "Updated"

    def test_delete(self, server_url):
        s, b = _post(f"{server_url}/api/projects",
                      {"name": "Del", "owner": "T", "business_unit": "B", "risk_tier": "low"}, ADMIN)
        pid = b["project"]["id"]
        status, body = _delete(f"{server_url}/api/projects/{pid}", ADMIN)
        assert status == 200
        assert body["deleted"] == pid

    def test_create_missing_fields(self, server_url):
        status, body = _post(f"{server_url}/api/projects", {"name": "X"}, ADMIN)
        assert status == 400

    def test_not_found(self, server_url):
        status, _ = _get(f"{server_url}/api/projects/99999", ADMIN)
        assert status in (400, 404)

    def test_no_auth(self, server_url):
        status, _ = _get(f"{server_url}/api/projects")
        assert status == 401

    def test_viewer_can_read(self, server_url):
        status, body = _get(f"{server_url}/api/projects", VIEWER)
        assert status == 200


class TestRuns:
    def test_list(self, server_url):
        status, body = _get(f"{server_url}/api/runs", ADMIN)
        assert status == 200
        assert len(body["runs"]) >= 4

    def test_list_filter_engine(self, server_url):
        status, body = _get(f"{server_url}/api/runs?engine=JMeter", ADMIN)
        assert status == 200
        assert all(r["engine"] == "JMeter" for r in body["runs"])

    def test_list_filter_status(self, server_url):
        status, body = _get(f"{server_url}/api/runs?status=completed", ADMIN)
        assert status == 200
        assert all(r["status"] == "completed" for r in body["runs"])

    def test_get_one(self, server_url):
        status, body = _get(f"{server_url}/api/runs/1", ADMIN)
        assert status == 200
        assert body["run"]["id"] == 1

    def test_create(self, server_url):
        status, body = _post(f"{server_url}/api/runs",
                             {"scenarioId": 1, "environmentId": 1, "targetVusers": 100}, ADMIN)
        assert status == 201
        assert "run" in body

    def test_start(self, server_url):
        s, b = _post(f"{server_url}/api/runs",
                      {"scenarioId": 1, "environmentId": 1, "targetVusers": 100}, ADMIN)
        run_id = b["run"]["id"]
        conn = sqlite3.connect(str(Path(os.environ["MARATHONRUNNER_DB_PATH"])))
        conn.execute("UPDATE test_runs SET status='ready' WHERE id=?", (run_id,))
        conn.commit()
        conn.close()
        with patch("apps.api.models.set_run_state"), patch("apps.api.models.track_active_run"):
            status, body = _post(f"{server_url}/api/runs/{run_id}/start", {}, ADMIN)
        assert status == 200
        assert body["run"]["status"] == "running"

    def test_cancel(self, server_url):
        s, b = _post(f"{server_url}/api/runs",
                      {"scenarioId": 1, "environmentId": 1, "targetVusers": 100}, ADMIN)
        run_id = b["run"]["id"]
        conn = sqlite3.connect(str(Path(os.environ["MARATHONRUNNER_DB_PATH"])))
        conn.execute("UPDATE test_runs SET status='ready' WHERE id=?", (run_id,))
        conn.commit()
        conn.close()
        with patch("apps.api.models.set_run_state"), patch("apps.api.models.track_active_run"):
            _post(f"{server_url}/api/runs/{run_id}/start", {}, ADMIN)
        status, body = _post(f"{server_url}/api/runs/{run_id}/cancel", {}, ADMIN)
        assert status == 200
        assert body["run"]["status"] == "cancelled"

    def test_approve(self, server_url):
        status, body = _post(f"{server_url}/api/runs/4/approve", {"decision": "approved"}, LEAD)
        assert status == 200

    def test_approve_requires_role(self, server_url):
        status, _ = _post(f"{server_url}/api/runs/4/approve", {"decision": "approved"}, VIEWER)
        assert status == 403

    def test_logs_no_container(self, server_url):
        status, body = _get(f"{server_url}/api/runs/1/logs", ADMIN)
        assert status == 200
        assert "logs" in body

    def test_live(self, server_url):
        status, body = _get(f"{server_url}/api/runs/1/live", ADMIN)
        assert status == 200
        assert "run" in body

    def test_report(self, server_url):
        status, body = _get(f"{server_url}/api/runs/1/report", ADMIN)
        assert status == 200

    def test_baseline_set(self, server_url):
        status, body = _post(f"{server_url}/api/runs/1/baseline", {"approved_by": "lead"}, ADMIN)
        assert status == 200
        assert body["run"]["is_baseline"] == 1

    def test_baseline_unset(self, server_url):
        status, body = _delete(f"{server_url}/api/runs/1/baseline", ADMIN)
        assert status == 200

    def test_compare(self, server_url):
        status, body = _get(f"{server_url}/api/runs/compare?ids=1,2", ADMIN)
        assert status == 200
        assert "comparisons" in body

    def test_compare_missing_ids(self, server_url):
        status, _ = _get(f"{server_url}/api/runs/compare", ADMIN)
        assert status == 400

    def test_active(self, server_url):
        status, body = _get(f"{server_url}/api/runs/active", ADMIN)
        assert status == 200
        assert "runs" in body

    def test_get_not_found(self, server_url):
        status, _ = _get(f"{server_url}/api/runs/99999", ADMIN)
        assert status == 400


class TestUsers:
    def test_list(self, server_url):
        status, body = _get(f"{server_url}/api/users", ADMIN)
        assert status == 200
        assert len(body["users"]) >= 4

    def test_create(self, server_url):
        status, body = _post(f"{server_url}/api/users",
                             {"username": "newuser", "display_name": "New", "role": "viewer", "password": "pass123"}, ADMIN)
        assert status == 201
        assert body["user"]["username"] == "newuser"

    def test_create_requires_admin(self, server_url):
        status, _ = _post(f"{server_url}/api/users",
                           {"username": "x", "display_name": "X", "role": "viewer", "password": "p"}, ENGINEER)
        assert status == 403

    def test_update(self, server_url):
        status, body = _put(f"{server_url}/api/users/3", {"display_name": "Updated Eng"}, ADMIN)
        assert status == 200
        assert body["user"]["display_name"] == "Updated Eng"

    def test_delete(self, server_url):
        s, b = _post(f"{server_url}/api/users",
                      {"username": "todelete", "display_name": "Del", "role": "viewer", "password": "p"}, ADMIN)
        uid = b["user"]["id"]
        status, body = _delete(f"{server_url}/api/users/{uid}", ADMIN)
        assert status == 200

    def test_delete_requires_admin(self, server_url):
        status, _ = _delete(f"{server_url}/api/users/3", ENGINEER)
        assert status == 403

    def test_cannot_delete_admin(self, server_url):
        status, body = _delete(f"{server_url}/api/users/1", ADMIN)
        assert status == 400

    def test_create_invalid_role(self, server_url):
        status, body = _post(f"{server_url}/api/users",
                             {"username": "x", "display_name": "X", "role": "bad", "password": "p"}, ADMIN)
        assert status == 400

    def test_no_auth(self, server_url):
        status, _ = _get(f"{server_url}/api/users")
        assert status == 401


# ===================================================================
# Tier 2 — CRUD entities
# ===================================================================

class TestEnvironments:
    def test_list(self, server_url):
        status, body = _get(f"{server_url}/api/environments", ADMIN)
        assert status == 200
        assert len(body["environments"]) >= 2

    def test_create(self, server_url):
        status, body = _post(f"{server_url}/api/environments",
                             {"name": "staging", "region": "us-west-2", "classification": "dev",
                              "readiness_status": "ready", "data_residency": "US"}, ADMIN)
        assert status == 201

    def test_update(self, server_url):
        status, body = _put(f"{server_url}/api/environments/1", {"name": "perf-updated"}, ADMIN)
        assert status == 200
        assert body["environment"]["name"] == "perf-updated"

    def test_delete(self, server_url):
        s, b = _post(f"{server_url}/api/environments",
                      {"name": "del-env", "region": "us-east-1", "classification": "dev",
                       "readiness_status": "ready", "data_residency": "US"}, ADMIN)
        eid = b["environment"]["id"]
        status, body = _delete(f"{server_url}/api/environments/{eid}", ADMIN)
        assert status == 200

    def test_readiness(self, server_url):
        status, body = _get(f"{server_url}/api/environments/1/readiness", ADMIN)
        assert status == 200

    def test_no_auth(self, server_url):
        status, _ = _get(f"{server_url}/api/environments")
        assert status == 401


class TestScenarios:
    def test_list(self, server_url):
        status, body = _get(f"{server_url}/api/scenarios", ADMIN)
        assert status == 200
        assert len(body["scenarios"]) >= 2

    def test_create(self, server_url):
        status, body = _post(f"{server_url}/api/scenarios",
                             {"project_id": 1, "name": "New Scn", "engine": "k6", "test_type": "load",
                              "workload_mix": "mix", "script_repository": "git@test",
                              "target_endpoint": "http://test", "sla_p95_ms": 500, "max_error_rate": 1.0}, ADMIN)
        assert status == 201

    def test_update(self, server_url):
        status, body = _put(f"{server_url}/api/scenarios/1", {"name": "Updated Scn"}, ADMIN)
        assert status == 200

    def test_delete(self, server_url):
        s, b = _post(f"{server_url}/api/scenarios",
                      {"project_id": 1, "name": "Del", "engine": "k6", "test_type": "smoke",
                       "workload_mix": "mix", "script_repository": "git@test",
                       "target_endpoint": "http://test", "sla_p95_ms": 500, "max_error_rate": 1.0}, ADMIN)
        sid = b["scenario"]["id"]
        status, body = _delete(f"{server_url}/api/scenarios/{sid}", ADMIN)
        assert status == 200

    def test_no_auth(self, server_url):
        status, _ = _get(f"{server_url}/api/scenarios")
        assert status == 401


class TestPools:
    def test_list(self, server_url):
        status, body = _get(f"{server_url}/api/pools", ADMIN)
        assert status == 200
        assert len(body["pools"]) >= 1
        # engines should be parsed from JSON
        assert isinstance(body["pools"][0]["engines"], list)

    def test_create(self, server_url):
        status, body = _post(f"{server_url}/api/pools",
                             {"name": "new-pool", "region": "us-east-1", "engines": '["k6"]',
                              "max_vusers": 5000}, ADMIN)
        assert status == 201

    def test_update(self, server_url):
        status, body = _put(f"{server_url}/api/pools/1", {"name": "updated-pool"}, ADMIN)
        assert status == 200

    def test_delete(self, server_url):
        s, b = _post(f"{server_url}/api/pools",
                      {"name": "del-pool", "region": "us-east-1", "engines": '["k6"]',
                       "max_vusers": 1000}, ADMIN)
        pid = b["pool"]["id"]
        status, body = _delete(f"{server_url}/api/pools/{pid}", ADMIN)
        assert status == 200

    def test_no_auth(self, server_url):
        status, _ = _get(f"{server_url}/api/pools")
        assert status == 401


class TestSchedules:
    def test_list(self, server_url):
        status, body = _get(f"{server_url}/api/schedules", ADMIN)
        assert status == 200
        assert len(body["schedules"]) >= 1

    def test_create(self, server_url):
        status, body = _post(f"{server_url}/api/schedules",
                             {"name": "Weekly", "scenario_id": 1, "environment_id": 1,
                              "target_vusers": 200, "duration_minutes": 10,
                              "load_profile": "steady", "cron_expression": "0 3 * * 1"}, ADMIN)
        assert status == 201

    def test_create_invalid_cron(self, server_url):
        status, body = _post(f"{server_url}/api/schedules",
                             {"name": "Bad", "scenario_id": 1, "environment_id": 1,
                              "target_vusers": 100, "duration_minutes": 5,
                              "load_profile": "steady", "cron_expression": "bad"}, ADMIN)
        assert status == 400

    def test_update(self, server_url):
        # Get the schedule id from seed data
        status, body = _get(f"{server_url}/api/schedules", ADMIN)
        sid = body["schedules"][0]["id"]
        status, body = _put(f"{server_url}/api/schedules/{sid}", {"name": "Updated"}, ADMIN)
        assert status == 200
        assert body["schedule"]["name"] == "Updated"

    def test_delete(self, server_url):
        s, b = _post(f"{server_url}/api/schedules",
                      {"name": "Del", "scenario_id": 1, "environment_id": 1,
                       "target_vusers": 100, "duration_minutes": 5,
                       "load_profile": "steady", "cron_expression": "0 4 * * *"}, ADMIN)
        sid = b["schedule"]["id"]
        status, body = _delete(f"{server_url}/api/schedules/{sid}", ADMIN)
        assert status == 200

    def test_no_auth(self, server_url):
        status, _ = _get(f"{server_url}/api/schedules")
        assert status == 401


class TestExecutionWindows:
    def test_list(self, server_url):
        status, body = _get(f"{server_url}/api/execution-windows", ADMIN)
        assert status == 200
        assert len(body["windows"]) >= 1

    def test_create(self, server_url):
        status, body = _post(f"{server_url}/api/execution-windows",
                             {"name": "BH2", "type": "window", "start_hour": 9, "end_hour": 17}, ADMIN)
        assert status == 201

    def test_create_blackout(self, server_url):
        status, body = _post(f"{server_url}/api/execution-windows",
                             {"name": "BH-Block", "type": "blackout", "start_hour": 0, "end_hour": 24}, ADMIN)
        assert status == 201
        assert body["window"]["type"] == "blackout"

    def test_update(self, server_url):
        status, body = _get(f"{server_url}/api/execution-windows", ADMIN)
        wid = body["windows"][0]["id"]
        status, body = _put(f"{server_url}/api/execution-windows/{wid}", {"name": "Updated BH"}, ADMIN)
        assert status == 200

    def test_delete(self, server_url):
        s, b = _post(f"{server_url}/api/execution-windows",
                      {"name": "Del", "type": "window", "start_hour": 6, "end_hour": 22}, ADMIN)
        wid = b["window"]["id"]
        status, body = _delete(f"{server_url}/api/execution-windows/{wid}", ADMIN)
        assert status == 200

    def test_check_allowed(self, server_url):
        status, body = _get(f"{server_url}/api/execution-windows/check?environment_id=1", ADMIN)
        assert status == 200

    def test_no_auth(self, server_url):
        status, _ = _get(f"{server_url}/api/execution-windows")
        assert status == 401


class TestWebhooks:
    def test_list(self, server_url):
        status, body = _get(f"{server_url}/api/webhooks", ADMIN)
        assert status == 200
        assert len(body["webhooks"]) >= 1

    def test_create(self, server_url):
        status, body = _post(f"{server_url}/api/webhooks",
                             {"name": "Hook2", "url": "https://hook2.test", "event": "run.failed", "enabled": 1}, ADMIN)
        assert status == 201

    def test_update(self, server_url):
        status, body = _get(f"{server_url}/api/webhooks", ADMIN)
        wid = body["webhooks"][0]["id"]
        status, body = _put(f"{server_url}/api/webhooks/{wid}", {"name": "Updated Hook"}, ADMIN)
        assert status == 200

    def test_delete(self, server_url):
        s, b = _post(f"{server_url}/api/webhooks",
                      {"name": "Del", "url": "https://del.test", "event": "run.completed", "enabled": 1}, ADMIN)
        wid = b["webhook"]["id"]
        status, body = _delete(f"{server_url}/api/webhooks/{wid}", ADMIN)
        assert status == 200

    def test_no_auth(self, server_url):
        status, _ = _get(f"{server_url}/api/webhooks")
        assert status == 401


class TestApplications:
    def test_list(self, server_url):
        status, body = _get(f"{server_url}/api/applications", ADMIN)
        assert status == 200
        assert len(body["applications"]) >= 1

    def test_create(self, server_url):
        status, body = _post(f"{server_url}/api/applications",
                             {"name": "New App", "endpoint": "https://newapp.test", "team": "T"}, ADMIN)
        assert status == 201

    def test_update(self, server_url):
        status, body = _get(f"{server_url}/api/applications", ADMIN)
        aid = body["applications"][0]["id"]
        status, body = _put(f"{server_url}/api/applications/{aid}", {"name": "Updated App"}, ADMIN)
        assert status == 200

    def test_delete(self, server_url):
        s, b = _post(f"{server_url}/api/applications",
                      {"name": "Del App", "endpoint": "https://del.test", "team": "T"}, ADMIN)
        aid = b["application"]["id"]
        status, body = _delete(f"{server_url}/api/applications/{aid}", ADMIN)
        assert status == 200

    def test_health(self, server_url):
        status, body = _get(f"{server_url}/api/applications/1/health", ADMIN)
        assert status == 200

    def test_no_auth(self, server_url):
        status, _ = _get(f"{server_url}/api/applications")
        assert status == 401


# ===================================================================
# Tier 3 — Read-only / derived endpoints
# ===================================================================

class TestPolicies:
    def test_list(self, server_url):
        status, body = _get(f"{server_url}/api/policies", ADMIN)
        assert status == 200
        assert len(body["policies"]) >= 1

    def test_no_auth(self, server_url):
        status, _ = _get(f"{server_url}/api/policies")
        assert status == 401


class TestResults:
    def test_list(self, server_url):
        status, body = _get(f"{server_url}/api/results", ADMIN)
        assert status == 200
        assert len(body["results"]) >= 2

    def test_no_auth(self, server_url):
        status, _ = _get(f"{server_url}/api/results")
        assert status == 401


class TestExportCsv:
    def test_runs(self, server_url):
        status, body = _get(f"{server_url}/api/export/csv?table=runs", ADMIN)
        assert status == 200
        assert isinstance(body, bytes)

    def test_results(self, server_url):
        status, body = _get(f"{server_url}/api/export/csv?table=results", ADMIN)
        assert status == 200

    def test_unknown_table(self, server_url):
        status, _ = _get(f"{server_url}/api/export/csv?table=unknown", ADMIN)
        assert status == 400

    def test_no_auth(self, server_url):
        status, _ = _get(f"{server_url}/api/export/csv")
        assert status == 401


class TestAudit:
    def test_list(self, server_url):
        status, body = _get(f"{server_url}/api/audit", ADMIN)
        assert status == 200
        assert "events" in body

    def test_no_auth(self, server_url):
        status, _ = _get(f"{server_url}/api/audit")
        assert status == 401


class TestNotifications:
    def test_list(self, server_url):
        status, body = _get(f"{server_url}/api/notifications", ADMIN)
        assert status == 200
        assert "notifications" in body

    def test_no_auth(self, server_url):
        status, _ = _get(f"{server_url}/api/notifications")
        assert status == 401


class TestRoadmap:
    def test_list(self, server_url):
        status, body = _get(f"{server_url}/api/roadmap", ADMIN)
        assert status == 200

    def test_no_auth(self, server_url):
        status, _ = _get(f"{server_url}/api/roadmap")
        assert status == 401


class TestAdmin:
    def test_stats(self, server_url):
        status, body = _get(f"{server_url}/api/admin/stats", ADMIN)
        assert status == 200

    def test_requires_auth(self, server_url):
        status, _ = _get(f"{server_url}/api/admin/stats")
        assert status == 401


class TestAiRecommendations:
    def test_list(self, server_url):
        status, body = _get(f"{server_url}/api/ai/recommendations", ADMIN)
        assert status == 200

    def test_no_auth(self, server_url):
        status, _ = _get(f"{server_url}/api/ai/recommendations")
        assert status == 401


class TestTrends:
    def test_trends(self, server_url):
        status, body = _get(f"{server_url}/api/trends", ADMIN)
        assert status == 200
        assert "summary" in body

    def test_insights(self, server_url):
        status, body = _get(f"{server_url}/api/trends/insights", ADMIN)
        assert status == 200
        assert "insights" in body

    def test_no_auth(self, server_url):
        status, _ = _get(f"{server_url}/api/trends")
        assert status == 401


class TestRoles:
    def test_list(self, server_url):
        status, body = _get(f"{server_url}/api/roles", ADMIN)
        assert status == 200
        assert "roles" in body


class TestBaselines:
    def test_list(self, server_url):
        status, body = _get(f"{server_url}/api/baselines", ADMIN)
        assert status == 200
        assert "baselines" in body

    def test_no_auth(self, server_url):
        status, _ = _get(f"{server_url}/api/baselines")
        assert status == 401


class TestTemplates:
    def test_list(self, server_url):
        status, body = _get(f"{server_url}/api/templates", ADMIN)
        assert status == 200
        assert "templates" in body

    def test_no_auth(self, server_url):
        status, _ = _get(f"{server_url}/api/templates")
        assert status == 401


class TestImpact:
    def test_list(self, server_url):
        status, body = _get(f"{server_url}/api/impact", ADMIN)
        assert status == 200

    def test_no_auth(self, server_url):
        status, _ = _get(f"{server_url}/api/impact")
        assert status == 401


class TestLoadProfiles:
    def test_list(self, server_url):
        status, body = _get(f"{server_url}/api/load-profiles", ADMIN)
        assert status == 200
        assert "profiles" in body

    def test_no_auth(self, server_url):
        status, _ = _get(f"{server_url}/api/load-profiles")
        assert status == 401


class TestWorkerTick:
    def test_forbidden_when_not_worker(self, server_url):
        status, _ = _post(f"{server_url}/api/worker/tick", {}, ADMIN)
        assert status == 403

    def test_worker_mode(self, server_url, monkeypatch):
        monkeypatch.setattr("apps.api.server.SERVICE_ROLE", "worker")
        with patch("apps.api.worker.process_worker_tick", return_value={"processed": 0}):
            status, body = _post(f"{server_url}/api/worker/tick", {}, ADMIN)
        assert status == 200


class TestCorsOptions:
    def test_options_returns_204(self, server_url):
        status, _ = _api("OPTIONS", f"{server_url}/api/runs")
        assert status == 204

    def test_cors_headers(self, server_url):
        req = urllib.request.Request(f"{server_url}/api/runs", method="OPTIONS")
        resp = urllib.request.urlopen(req)
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"
        assert "GET" in resp.headers.get("Access-Control-Allow-Methods", "")
