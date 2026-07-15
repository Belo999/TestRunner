from __future__ import annotations

from typing import Any

from .base import Engine, EngineResult
from .locust_parser import parse_locust_results


class LocustEngine(Engine):
    @property
    def name(self) -> str:
        return "Locust"

    def image(self) -> str:
        return "locustio/locust:latest"

    def script_filename(self) -> str:
        return "locustfile.py"

    def build_docker_command(
        self,
        script_path: str,
        result_dir: str,
        target_endpoint: str,
        target_vusers: int,
        duration_minutes: int,
        extra_options: dict[str, Any] | None = None,
    ) -> list[str]:
        run_time = f"{duration_minutes}m"
        return [
            "docker", "run", "-d",
            "--network", "host",
            "--name", f"mr-locust-{id(script_path) % 100000}",
            "-v", f"{result_dir}:/results",
            "-v", f"{script_path}:/scripts:ro",
            "-e", f"LOCUST_HOST={target_endpoint}",
            "-e", f"LOCUST_USERS={target_vusers}",
            "-e", f"LOCUST_RUN_TIME={run_time}",
            self.image(),
            "-f", "/scripts/locustfile.py",
            "--headless",
            "-u", str(target_vusers),
            "-r", str(min(10, target_vusers)),
            "--run-time", run_time,
            "--host", target_endpoint,
            "--csv", "/results/stats",
            "--html", "/results/report.html",
        ]

    def parse_results(self, result_dir: str) -> EngineResult:
        return parse_locust_results(result_dir)

    def build_k8s_job_spec(self, run_config: dict[str, Any]) -> dict[str, Any]:
        job = super().build_k8s_job_spec(run_config)
        container = job["spec"]["template"]["spec"]["containers"][0]
        run_time = f"{run_config['duration_minutes']}m"
        container["command"] = [
            "locust", "-f", "/scripts/locustfile.py",
            "--headless",
            "-u", str(run_config["target_vusers"]),
            "-r", str(min(10, run_config["target_vusers"])),
            "--run-time", run_time,
            "--host", run_config["target_endpoint"],
            "--csv", "/results/stats",
            "--html", "/results/report.html",
        ]
        return job
