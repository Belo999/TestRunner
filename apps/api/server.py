from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .database import DB_PATH, ROOT_DIR, WEB_DIR, connect_db, from_json
from .engines import list_engines
from .models import (
    ROADMAP,
    ai_recommendations,
    approve_run,
    cancel_run,
    create_entity,
    create_run,
    dashboard,
    delete_entity,
    detect_anomalies,
    detect_config_drift,
    detect_trend_anomalies,
    export_test_config,
    get_anomaly_summary,
    get_correlation_by_trace,
    get_execution_mode,
    get_git_config_history,
    get_k8s_cluster_nodes,
    get_k8s_testrun_status,
    get_run,
    get_runs,
    get_run_traces,
    get_table,
    get_trace_summary,
    import_test_config,
    list_k8s_jobs,
    propagate_trace_headers,
    promote_config,
    start_run,
    update_entity,
)


SERVICE_ROLE = os.environ.get("MARATHONRUNNER_SERVICE_ROLE", "api")

PUBLIC_PATHS = {"/api/health", "/api/auth/login", "/docs", "/swagger", "/api/openapi.json"}


def check_auth(handler) -> dict | None:
    from .auth import extract_token_from_header, decode_token
    auth_header = handler.headers.get("Authorization")
    token = extract_token_from_header(auth_header)
    if not token:
        handler.send_json({"error": "Authentication required"}, HTTPStatus.UNAUTHORIZED)
        return None
    payload = decode_token(token)
    if payload is None:
        handler.send_json({"error": "Invalid or expired token"}, HTTPStatus.UNAUTHORIZED)
        return None
    return payload


def check_role(handler, *roles) -> dict | None:
    user = check_auth(handler)
    if user is None:
        return None
    if user.get("role") not in roles:
        handler.send_json({"error": f"Insufficient permissions. Required roles: {', '.join(roles)}"}, HTTPStatus.FORBIDDEN)
        return None
    return user


def can_connect(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def platform_health() -> dict[str, Any]:
    from .database import DB_BACKEND, DB_PATH
    from .redis_cache import ping_redis
    from .storage import check_object_storage

    postgres_host = os.environ.get("POSTGRES_HOST", "postgres")
    redis_host = os.environ.get("REDIS_HOST", "redis")
    minio_endpoint = os.environ.get("OBJECT_STORAGE_ENDPOINT", "http://minio:9000")
    postgres_ok = can_connect(postgres_host, 5432)
    redis_ok = ping_redis()
    minio_ok = check_object_storage()

    if DB_BACKEND == "postgresql":
        control_store = {
            "status": "ok" if postgres_ok else "waiting",
            "backend": "postgresql",
            "host": postgres_host,
        }
    else:
        control_store = {"status": "ok", "backend": "sqlite", "path": str(DB_PATH)}

    return {
        "service": "marathonrunner-enterprise",
        "role": SERVICE_ROLE,
        "status": "ok",
        "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).replace(microsecond=0).isoformat(),
        "engines": list_engines(),
        "dependencies": {
            "controlStore": control_store,
            "postgresMetadataTarget": {"status": "reachable" if postgres_ok else "waiting", "host": postgres_host},
            "redisRuntimeData": {"status": "reachable" if redis_ok else "waiting", "host": redis_host},
            "objectStorage": {"status": "reachable" if minio_ok else "waiting", "endpoint": minio_endpoint},
        },
    }


def path_id(path: str, prefix: str, suffix: str = "") -> int | None:
    if not path.startswith(prefix):
        return None
    tail = path[len(prefix):]
    if suffix:
        if not tail.endswith(suffix):
            return None
        tail = tail[:-len(suffix)]
    tail = tail.strip("/")
    if not tail.isdigit():
        return None
    return int(tail)


def path_string(path: str, prefix: str, suffix: str = "") -> str | None:
    if not path.startswith(prefix):
        return None
    tail = path[len(prefix):]
    if suffix:
        if not tail.endswith(suffix):
            return None
        tail = tail[:-len(suffix)]
    tail = tail.strip("/")
    if not tail:
        return None
    return tail


class MarathonRunnerHandler(BaseHTTPRequestHandler):
    server_version = "MarathonRunnerEnterprise/2.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path not in PUBLIC_PATHS and not path.startswith("/api/"):
                pass  # static files, no auth needed
            elif path.startswith("/api/") and path not in PUBLIC_PATHS:
                if check_auth(self) is None:
                    return
            if path == "/api/health":
                self.send_json(platform_health())
                return
            if path == "/api/dashboard":
                self.send_json(dashboard())
                return
            if path == "/api/projects":
                self.send_json({"projects": get_table("projects")})
                return
            if path == "/api/environments":
                self.send_json({"environments": get_table("environments")})
                return
            if path == "/api/scenarios":
                self.send_json({"scenarios": get_table("scenarios")})
                return
            if path == "/api/pools":
                pools = get_table("load_generator_pools")
                for pool in pools:
                    pool["engines"] = from_json(pool["engines"], [])
                self.send_json({"pools": pools})
                return
            if path == "/api/policies":
                self.send_json({"policies": get_table("policies")})
                return
            if path == "/api/runs":
                from urllib.parse import parse_qs
                params = parse_qs(parsed.query)
                engine = params.get("engine", [None])[0]
                status = params.get("status", [None])[0]
                search = params.get("search", [None])[0]
                self.send_json({"runs": get_runs(engine=engine, status=status, search=search)})
                return
            run_id = path_id(path, "/api/runs/")
            if run_id is not None:
                self.send_json({"run": get_run(run_id)})
                return
            if path == "/api/results":
                self.send_json({"results": get_table("run_results", "id DESC")})
                return
            if path == "/api/export/csv":
                self.export_csv()
                return
            if path == "/api/audit":
                self.send_json({"events": get_table("audit_events", "id DESC")[:80]})
                return
            if path == "/api/notifications":
                self.send_json({"notifications": get_table("notifications", "id DESC")[:30]})
                return
            if path == "/api/roadmap":
                self.send_json(ROADMAP)
                return
            if path == "/api/openapi.json":
                openapi_path = Path(__file__).parent / "openapi.json"
                if openapi_path.exists():
                    content = openapi_path.read_text()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(content.encode())
                else:
                    self.send_json({"error": "OpenAPI spec not found"}, HTTPStatus.NOT_FOUND)
                return
            if path == "/docs" or path == "/swagger":
                self.serve_swagger_ui()
                return
            if path == "/api/admin/stats":
                from .models import admin_stats
                self.send_json(admin_stats())
                return
            if path == "/api/ai/recommendations":
                self.send_json(ai_recommendations())
                return
            if path == "/api/trends":
                from .models import get_trends
                self.send_json(get_trends())
                return
            if path == "/api/trends/insights":
                from .models import generate_trend_insights
                self.send_json({"insights": generate_trend_insights()})
                return
            if path == "/api/schedules":
                from .models import get_schedules
                self.send_json({"schedules": get_schedules()})
                return
            if path == "/api/runs/active":
                from .models import get_active_runs
                self.send_json({"runs": get_active_runs()})
                return
            if path == "/api/users":
                from .models import get_users
                self.send_json({"users": get_users()})
                return
            if path == "/api/roles":
                from .models import ROLES
                self.send_json({"roles": ROLES})
                return
            if path == "/api/execution-windows":
                from .models import get_execution_windows
                self.send_json({"windows": get_execution_windows()})
                return
            if path == "/api/execution-windows/check":
                from urllib.parse import parse_qs
                params = parse_qs(parsed.query)
                env_id = params.get("environment_id", [None])[0]
                env_id = int(env_id) if env_id else None
                from .models import check_execution_allowed
                self.send_json(check_execution_allowed(env_id))
                return
            if path == "/api/baselines":
                from .models import get_baselines
                self.send_json({"baselines": get_baselines()})
                return
            if path == "/api/webhooks":
                from .models import get_webhooks
                self.send_json({"webhooks": get_webhooks()})
                return
            if path == "/api/templates":
                from .models import get_templates
                self.send_json({"templates": get_templates()})
                return
            if path == "/api/impact":
                from .models import get_test_impact_analysis
                self.send_json(get_test_impact_analysis())
                return
            if path == "/api/applications":
                from .models import get_applications
                self.send_json({"applications": get_applications()})
                return
            app_id = path_id(path, "/api/applications/", "/health")
            if app_id is not None:
                from .models import check_application_health
                self.send_json(check_application_health(app_id))
                return
            if path == "/api/load-profiles":
                from .models import get_load_profiles
                self.send_json({"profiles": get_load_profiles()})
                return
            if path == "/api/auth/me":
                from .auth import extract_token_from_header, decode_token
                auth_header = self.headers.get("Authorization")
                token = extract_token_from_header(auth_header)
                if not token:
                    self.send_json({"error": "Not authenticated"}, HTTPStatus.UNAUTHORIZED)
                    return
                payload = decode_token(token)
                if payload is None:
                    self.send_json({"error": "Invalid token"}, HTTPStatus.UNAUTHORIZED)
                    return
                self.send_json({"user": payload})
                return
            if path == "/api/runs/compare":
                from urllib.parse import parse_qs
                params = parse_qs(parsed.query)
                ids_str = params.get("ids", [""])[0]
                if not ids_str:
                    self.send_json({"error": "Missing ids parameter"}, HTTPStatus.BAD_REQUEST)
                    return
                ids = [int(x) for x in ids_str.split(",") if x.strip().isdigit()]
                from .models import compare_runs
                self.send_json(compare_runs(ids))
                return
            logs_id = path_id(path, "/api/runs/", "/logs")
            if logs_id is not None:
                self.send_json(self.get_run_logs(logs_id))
                return
            live_id = path_id(path, "/api/runs/", "/live")
            if live_id is not None:
                from .models import get_run_live
                self.send_json({"run": get_run_live(live_id)})
                return
            report_id = path_id(path, "/api/runs/", "/report")
            if report_id is not None:
                from .models import generate_run_report
                report = generate_run_report(report_id)
                self.send_json(report)
                return
            env_id = path_id(path, "/api/environments/", "/readiness")
            if env_id is not None:
                from .models import check_environment_readiness
                self.send_json(check_environment_readiness(env_id))
                return
            if path == "/api/k8s/mode":
                self.send_json({"mode": get_execution_mode()})
                return
            if path == "/api/k8s/nodes":
                self.send_json({"nodes": get_k8s_cluster_nodes()})
                return
            if path == "/api/k8s/jobs":
                self.send_json({"jobs": list_k8s_jobs()})
                return
            k8s_run_id = path_id(path, "/api/runs/", "/k8s-status")
            if k8s_run_id is not None:
                status = get_k8s_testrun_status(k8s_run_id)
                if status:
                    self.send_json({"status": status})
                else:
                    self.send_json({"error": "TestRun CR not found"}, HTTPStatus.NOT_FOUND)
                return
            if path == "/api/anomalies/summary":
                self.send_json(get_anomaly_summary())
                return
            if path == "/api/anomalies/trends":
                from urllib.parse import parse_qs
                params = parse_qs(parsed.query)
                project_id = params.get("project_id", [None])[0]
                project_id = int(project_id) if project_id else None
                self.send_json(detect_trend_anomalies(project_id))
                return
            anomaly_run_id = path_id(path, "/api/runs/", "/anomalies")
            if anomaly_run_id is not None:
                self.send_json(detect_anomalies(anomaly_run_id))
                return
            if path == "/api/git/history":
                from urllib.parse import parse_qs
                params = parse_qs(parsed.query)
                project_id = params.get("project_id", [None])[0]
                project_id = int(project_id) if project_id else None
                self.send_json({"history": get_git_config_history(project_id)})
                return
            export_id = path_id(path, "/api/scenarios/", "/export")
            if export_id is not None:
                self.send_json(export_test_config(export_id))
                return
            drift_id = path_id(path, "/api/scenarios/", "/drift")
            if drift_id is not None:
                self.send_json(detect_config_drift(drift_id, {}))
                return
            if path == "/api/otel/summary":
                self.send_json(get_trace_summary())
                return
            otel_run_id = path_id(path, "/api/runs/", "/traces")
            if otel_run_id is not None:
                self.send_json(get_run_traces(otel_run_id))
                return
            otel_trace_id = path_string(path, "/api/traces/", "/runs")
            if otel_trace_id is not None:
                self.send_json({"runs": get_correlation_by_trace(otel_trace_id)})
                return
            otel_propagate_id = path_id(path, "/api/runs/", "/trace-headers")
            if otel_propagate_id is not None:
                self.send_json(propagate_trace_headers(otel_propagate_id))
                return
            self.serve_static(path)
        except (ValueError, Exception) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            payload = self.read_json()
            if path == "/api/auth/login":
                from .models import get_user_by_username
                from .auth import generate_token, verify_password
                username = payload.get("username", "")
                password = payload.get("password", "")
                if not username:
                    self.send_json({"error": "Username is required"}, HTTPStatus.BAD_REQUEST)
                    return
                user = get_user_by_username(username)
                if user is None:
                    self.send_json({"error": "Invalid credentials"}, HTTPStatus.UNAUTHORIZED)
                    return
                stored_hash = user.get("password_hash")
                stored_salt = user.get("password_salt")
                if stored_hash and stored_salt:
                    if not password:
                        self.send_json({"error": "Password is required"}, HTTPStatus.BAD_REQUEST)
                        return
                    if not verify_password(password, stored_hash, stored_salt):
                        self.send_json({"error": "Invalid credentials"}, HTTPStatus.UNAUTHORIZED)
                        return
                else:
                    self.send_json({"error": "Invalid credentials"}, HTTPStatus.UNAUTHORIZED)
                    return
                token = generate_token(user["id"], user["username"], user["role"], user["display_name"])
                if isinstance(token, bytes):
                    token = token.decode("utf-8")
                self.send_json({
                    "token": token,
                    "user": {
                        "id": user["id"],
                        "username": user["username"],
                        "display_name": user["display_name"],
                        "role": user["role"],
                        "roleLabel": user["roleLabel"],
                        "permissions": user["permissions"],
                    }
                })
                return
            if path.startswith("/api/") and path != "/api/auth/login":
                if check_auth(self) is None:
                    return
            if path == "/api/runs":
                self.send_json({"run": create_run(payload)}, HTTPStatus.CREATED)
                return
            approve_id = path_id(path, "/api/runs/", "/approve")
            if approve_id is not None:
                if check_role(self, "admin", "performance_lead") is None:
                    return
                self.send_json({"run": approve_run(approve_id, payload)})
                return
            start_id = path_id(path, "/api/runs/", "/start")
            if start_id is not None:
                self.send_json({"run": start_run(start_id)})
                return
            complete_id = path_id(path, "/api/runs/", "/complete")
            if complete_id is not None:
                from .models import complete_run
                self.send_json({"run": complete_run(complete_id)})
                return
            cancel_id = path_id(path, "/api/runs/", "/cancel")
            if cancel_id is not None:
                self.send_json({"run": cancel_run(cancel_id)})
                return
            if path == "/api/worker/tick":
                if SERVICE_ROLE != "worker":
                    self.send_json({"error": "Worker tick is only available in worker mode. Use the marathonrunner-worker container."}, HTTPStatus.FORBIDDEN)
                    return
                from .worker import process_worker_tick
                self.send_json({"worker": process_worker_tick()})
                return
            if path.startswith("/api/projects"):
                self.send_json({"project": create_entity("projects", payload, ["name", "owner", "business_unit", "risk_tier"])}, HTTPStatus.CREATED)
                return
            if path.startswith("/api/environments"):
                self.send_json({"environment": create_entity("environments", payload, ["name", "region", "classification", "readiness_status", "data_residency"], {"service_virtualization_enabled": 0})}, HTTPStatus.CREATED)
                return
            if path.startswith("/api/scenarios"):
                self.send_json({"scenario": create_entity("scenarios", payload, ["project_id", "name", "engine", "test_type", "workload_mix", "script_repository", "target_endpoint", "sla_p95_ms", "max_error_rate"])}, HTTPStatus.CREATED)
                return
            if path.startswith("/api/pools"):
                self.send_json({"pool": create_entity("load_generator_pools", payload, ["name", "region", "engines", "max_vusers"], {"status": "healthy", "current_reservation": 0})}, HTTPStatus.CREATED)
                return
            if path == "/api/schedules":
                from .models import create_schedule
                self.send_json({"schedule": create_schedule(payload)}, HTTPStatus.CREATED)
                return
            if path == "/api/users":
                if check_role(self, "admin") is None:
                    return
                from .models import create_user
                self.send_json({"user": create_user(payload)}, HTTPStatus.CREATED)
                return
            if path == "/api/execution-windows":
                from .models import create_execution_window
                self.send_json({"window": create_execution_window(payload)}, HTTPStatus.CREATED)
                return
            if path == "/api/applications":
                from .models import create_application
                self.send_json({"application": create_application(payload)}, HTTPStatus.CREATED)
                return
            if path == "/api/webhooks":
                from .models import create_webhook
                self.send_json({"webhook": create_webhook(payload)}, HTTPStatus.CREATED)
                return
            set_baseline_id = path_id(path, "/api/runs/", "/baseline")
            if set_baseline_id is not None:
                from .models import set_baseline
                approved_by = payload.get("approved_by", "performance-lead")
                self.send_json({"run": set_baseline(set_baseline_id, approved_by)})
                return
            template_id = path_string(path, "/api/templates/", "/run")
            if template_id is not None:
                from .models import create_run_from_template
                self.send_json({"run": create_run_from_template(template_id, payload)}, HTTPStatus.CREATED)
                return
            k8s_launch_id = path_id(path, "/api/runs/", "/k8s-launch")
            if k8s_launch_id is not None:
                from .models import create_k8s_testrun, build_k8s_testrun_spec
                from .engines import get_engine
                run_data = get_run(k8s_launch_id)
                engine_adapter = get_engine(run_data["engine"])
                if engine_adapter is None:
                    self.send_json({"error": f"Unknown engine: {run_data['engine']}"}, HTTPStatus.BAD_REQUEST)
                    return
                spec = build_k8s_testrun_spec(run_data, engine_adapter)
                self.send_json({"testrun": spec}, HTTPStatus.CREATED)
                return
            if path == "/api/git/import":
                project_id = payload.get("project_id")
                result = import_test_config(payload, project_id)
                self.send_json({"result": result}, HTTPStatus.CREATED)
                return
            promote_id = path_id(path, "/api/scenarios/", "/promote")
            if promote_id is not None:
                from_env = payload.get("from_env", "dev")
                to_env = payload.get("to_env", "staging")
                result = promote_config(promote_id, from_env, to_env)
                self.send_json({"result": result})
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError, Exception) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            payload = self.read_json()
            if path.startswith("/api/"):
                if check_auth(self) is None:
                    return
            table_map = {
                "/api/projects": ("projects", ["name", "owner", "business_unit", "risk_tier"]),
                "/api/environments": ("environments", ["name", "region", "classification", "readiness_status", "service_virtualization_enabled", "data_residency"]),
                "/api/scenarios": ("scenarios", ["project_id", "name", "engine", "test_type", "workload_mix", "script_repository", "target_endpoint", "sla_p95_ms", "max_error_rate"]),
                "/api/pools": ("load_generator_pools", ["name", "region", "engines", "max_vusers", "status"]),
            }
            for prefix, (table, allowed) in table_map.items():
                entity_id = path_id(path, f"{prefix}/")
                if entity_id is not None:
                    self.send_json({table.rstrip("s").replace("load_generator_pool", "pool"): update_entity(table, entity_id, payload, allowed)})
                    return
            schedule_id = path_id(path, "/api/schedules/")
            if schedule_id is not None:
                from .models import update_schedule
                self.send_json({"schedule": update_schedule(schedule_id, payload)})
                return
            user_id = path_id(path, "/api/users/")
            if user_id is not None:
                from .models import update_user
                self.send_json({"user": update_user(user_id, payload)})
                return
            window_id = path_id(path, "/api/execution-windows/")
            if window_id is not None:
                from .models import update_execution_window
                self.send_json({"window": update_execution_window(window_id, payload)})
                return
            webhook_id = path_id(path, "/api/webhooks/")
            if webhook_id is not None:
                from .models import update_webhook
                self.send_json({"webhook": update_webhook(webhook_id, payload)})
                return
            app_id = path_id(path, "/api/applications/")
            if app_id is not None:
                from .models import update_application
                self.send_json({"application": update_application(app_id, payload)})
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError, Exception) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path.startswith("/api/"):
                if check_auth(self) is None:
                    return
            table_map = {
                "/api/projects": "projects",
                "/api/environments": "environments",
                "/api/scenarios": "scenarios",
                "/api/pools": "load_generator_pools",
            }
            for prefix, table in table_map.items():
                entity_id = path_id(path, f"{prefix}/")
                if entity_id is not None:
                    self.send_json(delete_entity(table, entity_id))
                    return
            schedule_id = path_id(path, "/api/schedules/")
            if schedule_id is not None:
                from .models import delete_schedule
                self.send_json(delete_schedule(schedule_id))
                return
            user_id = path_id(path, "/api/users/")
            if user_id is not None:
                if check_role(self, "admin") is None:
                    return
                from .models import delete_user
                self.send_json(delete_user(user_id))
                return
            window_id = path_id(path, "/api/execution-windows/")
            if window_id is not None:
                from .models import delete_execution_window
                self.send_json(delete_execution_window(window_id))
                return
            webhook_id = path_id(path, "/api/webhooks/")
            if webhook_id is not None:
                from .models import delete_webhook
                self.send_json(delete_webhook(webhook_id))
                return
            app_id = path_id(path, "/api/applications/")
            if app_id is not None:
                from .models import delete_application
                self.send_json(delete_application(app_id))
                return
            unset_baseline_id = path_id(path, "/api/runs/", "/baseline")
            if unset_baseline_id is not None:
                from .models import unset_baseline
                self.send_json({"run": unset_baseline(unset_baseline_id)})
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, Exception) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def get_run_logs(self, run_id: int) -> dict[str, Any]:
        run = get_run(run_id)
        execution_id = run.get("execution_id")
        if not execution_id:
            return {"logs": "No container running for this run.", "containerId": None}
        try:
            result = subprocess.run(
                ["docker", "logs", "--tail", "200", execution_id],
                capture_output=True,
                text=True,
                timeout=10,
            )
            logs = result.stdout + result.stderr
            return {"logs": logs[-5000:] if len(logs) > 5000 else logs, "containerId": execution_id}
        except Exception as exc:
            return {"logs": f"Could not retrieve logs: {exc}", "containerId": execution_id}

    def read_json(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length == 0:
            return {}
        raw_body = self.rfile.read(content_length)
        return json.loads(raw_body.decode("utf-8"))

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def export_csv(self) -> None:
        import csv
        import io
        from urllib.parse import parse_qs
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        table = params.get("table", ["runs"])[0]

        with connect_db() as connection:
            if table == "runs":
                rows = connection.execute(
                    """SELECT tr.id, tr.name, tr.engine, tr.status, tr.quality_gate, tr.risk_score,
                       tr.target_vusers, tr.duration_minutes, tr.created_at, tr.completed_at,
                       p.name as project_name, s.name as scenario_name, e.name as environment_name,
                       rr.p50_ms, rr.p95_ms, rr.p99_ms, rr.throughput_rps, rr.error_rate, rr.apdex
                    FROM test_runs tr
                    LEFT JOIN projects p ON p.id = tr.project_id
                    LEFT JOIN scenarios s ON s.id = tr.scenario_id
                    LEFT JOIN environments e ON e.id = tr.environment_id
                    LEFT JOIN run_results rr ON rr.run_id = tr.id
                    ORDER BY tr.id DESC"""
                ).fetchall()
            elif table == "results":
                rows = connection.execute("SELECT * FROM run_results ORDER BY id DESC").fetchall()
            elif table == "audit":
                rows = connection.execute("SELECT * FROM audit_events ORDER BY id DESC LIMIT 500").fetchall()
            else:
                self.send_json({"error": f"Unknown table: {table}"}, HTTPStatus.BAD_REQUEST)
                return

            if not rows:
                self.send_json({"error": "No data to export"}, HTTPStatus.NOT_FOUND)
                return

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([desc[0] for desc in rows[0].keys()] if hasattr(rows[0], 'keys') else range(len(rows[0])))
            for row in rows:
                if hasattr(row, 'keys'):
                    writer.writerow([row[key] for key in row.keys()])
                else:
                    writer.writerow(list(row))

            csv_content = output.getvalue()
            body = csv_content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f"attachment; filename=marathonrunner-{table}-export.csv")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def serve_swagger_ui(self) -> None:
        swagger_html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>MarathonRunner API Documentation</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5.10.5/swagger-ui.css">
  <style>
    body { margin: 0; padding: 0; }
    .swagger-ui .topbar { display: none; }
    .swagger-ui .info { margin: 20px 0; }
    .auth-box { background: #f8f9fa; padding: 16px; margin: 16px 0; border: 1px solid #dee2e6; border-radius: 8px; }
    .auth-box h3 { margin: 0 0 12px; font-size: 16px; }
    .auth-box input { padding: 8px; margin-right: 8px; border: 1px solid #ccc; border-radius: 4px; width: 300px; }
    .auth-box button { padding: 8px 16px; background: #00a3e0; color: white; border: none; border-radius: 4px; cursor: pointer; }
    .auth-box button:hover { background: #0088c7; }
    .auth-status { margin-top: 8px; font-size: 14px; }
    .auth-status.success { color: #28a745; }
    .auth-status.error { color: #dc3545; }
  </style>
</head>
<body>
  <div style="padding: 20px; background: #1e3a5f; color: white;">
    <h1 style="margin: 0;">MarathonRunner Enterprise API</h1>
    <p style="margin: 4px 0 0; opacity: 0.8;">Interactive API Documentation</p>
  </div>
  
  <div class="auth-box" style="margin: 20px; max-width: 600px;">
    <h3>Authentication</h3>
    <p style="margin: 0 0 8px; font-size: 14px; color: #666;">Login to get a JWT token for authenticated requests:</p>
    <div>
      <input type="text" id="authUsername" placeholder="Username" value="admin">
      <input type="password" id="authPassword" placeholder="Password" value="marathonrunner">
      <button onclick="loginUser()">Login</button>
      <button onclick="clearAuth()" style="background: #6c757d;">Clear</button>
    </div>
    <div id="authStatus" class="auth-status"></div>
    <div id="authToken" style="display: none; margin-top: 12px;">
      <p style="margin: 0; font-size: 12px; color: #666;">Token (auto-added to Swagger Authorize):</p>
      <code id="authTokenValue" style="word-break: break-all; font-size: 11px;"></code>
    </div>
  </div>

  <div id="swagger-ui"></div>
  
  <script src="https://unpkg.com/swagger-ui-dist@5.10.5/swagger-ui-bundle.js"></script>
  <script>
    let currentToken = null;
    
    const ui = SwaggerUIBundle({
      url: '/api/openapi.json',
      dom_id: '#swagger-ui',
      presets: [
        SwaggerUIBundle.presets.apis,
        SwaggerUIBundle.SwaggerUIStandalonePreset
      ],
      layout: "BaseLayout",
      deepLinking: true,
      filter: true,
      showExtensions: true,
      showCommonExtensions: true,
      requestInterceptor: function(request) {
        if (currentToken) {
          request.headers['Authorization'] = 'Bearer ' + currentToken;
        }
        return request;
      }
    });

    async function loginUser() {
      const username = document.getElementById('authUsername').value;
      const password = document.getElementById('authPassword').value;
      const statusEl = document.getElementById('authStatus');
      const tokenEl = document.getElementById('authToken');
      const tokenValueEl = document.getElementById('authTokenValue');

      try {
        const response = await fetch('/api/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: username, password: password })
        });
        const data = await response.json();
        
        if (response.ok) {
          currentToken = data.token;
          statusEl.className = 'auth-status success';
          statusEl.textContent = 'Logged in as ' + data.user.display_name + ' (' + data.user.role + ')';
          tokenEl.style.display = 'block';
          tokenValueEl.textContent = data.token.substring(0, 50) + '...';
          
          // Auto-authorize in Swagger
          ui.authActions.authorize({
            BearerAuth: {
              name: 'BearerAuth',
              schema: {
                type: 'apiKey',
                in: 'header',
                name: 'Authorization',
                description: 'JWT Token. Paste: Bearer <token>'
              }
            }
          });
        } else {
          statusEl.className = 'auth-status error';
          statusEl.textContent = data.error || 'Login failed';
        }
      } catch (e) {
        statusEl.className = 'auth-status error';
        statusEl.textContent = 'Connection error: ' + e.message;
      }
    }

    function clearAuth() {
      currentToken = null;
      document.getElementById('authStatus').textContent = '';
      document.getElementById('authStatus').className = 'auth-status';
      document.getElementById('authToken').style.display = 'none';
      ui.authActions.logout();
    }
  </script>
</body>
</html>"""
        body = swagger_html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, path: str) -> None:
        web_dir = ROOT_DIR / "apps" / "web"
        file_path = web_dir / "index.html" if path == "/" else (web_dir / path.lstrip("/")).resolve()
        if not str(file_path).startswith(str(web_dir.resolve())) or not file_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", self.content_type(file_path))
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def content_type(self, file_path: Path) -> str:
        if file_path.suffix == ".html":
            return "text/html; charset=utf-8"
        if file_path.suffix == ".css":
            return "text/css; charset=utf-8"
        if file_path.suffix == ".js":
            return "application/javascript; charset=utf-8"
        if file_path.suffix == ".json":
            return "application/json; charset=utf-8"
        return "application/octet-stream"

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def run_api() -> None:
    from .database import initialize_database
    initialize_database()
    host = os.environ.get("MARATHONRUNNER_HOST", "127.0.0.1")
    port = int(os.environ.get("MARATHONRUNNER_PORT", "8080"))
    server = ThreadingHTTPServer((host, port), MarathonRunnerHandler)
    print(f"MarathonRunner Enterprise API running at http://{host}:{port}")
    print(f"Registered engines: {', '.join(list_engines())}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping MarathonRunner Enterprise API.")
    finally:
        server.server_close()
