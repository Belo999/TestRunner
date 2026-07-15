from __future__ import annotations

from typing import Any

from .base import Engine, EngineResult
from .gatling_parser import parse_gatling_results


class GatlingEngine(Engine):
    @property
    def name(self) -> str:
        return "Gatling"

    def image(self) -> str:
        return "denvazh/gatling:latest"

    def script_filename(self) -> str:
        return "simulation.scala"

    def build_docker_command(
        self,
        script_path: str,
        result_dir: str,
        target_endpoint: str,
        target_vusers: int,
        duration_minutes: int,
        extra_options: dict[str, Any] | None = None,
    ) -> list[str]:
        from urllib.parse import urlparse
        parsed = urlparse(target_endpoint)
        host = parsed.hostname or "localhost"
        port = str(parsed.port or 8080)
        protocol = parsed.scheme or "http"

        return [
            "docker", "run", "-d",
            "--network", "host",
            "--name", f"mr-gatling-{id(script_path) % 100000}",
            "-v", f"{result_dir}:/results",
            "-v", f"{script_path}:/scripts:ro",
            "-e", f"GATLING_HOST={host}",
            "-e", f"GATLING_PORT={port}",
            "-e", f"GATLING_PROTOCOL={protocol}",
            "-e", f"GATLING_USERS={target_vusers}",
            "-e", f"GATLING_DURATION={duration_minutes}",
            self.image(),
            "-s", "simulation.BasicSimulation",
            "-Dyield=false",
            "-Dsimulation.host=${GATLING_HOST}",
            "-Dsimulation.port=${GATLING_PORT}",
            "-Dsimulation.protocol=${GATLING_PROTOCOL}",
            "-Dsimulation.users=${GATLING_USERS}",
            "-Dsimulation.duration=${GATLING_DURATION}",
            "-rf", "/results",
        ]

    def parse_results(self, result_dir: str) -> EngineResult:
        return parse_gatling_results(result_dir)

    def build_k8s_job_spec(self, run_config: dict[str, Any]) -> dict[str, Any]:
        job = super().build_k8s_job_spec(run_config)
        container = job["spec"]["template"]["spec"]["containers"][0]
        from urllib.parse import urlparse
        parsed = urlparse(run_config["target_endpoint"])
        host = parsed.hostname or "localhost"
        port = str(parsed.port or 8080)
        protocol = parsed.scheme or "http"
        container["command"] = [
            "gatling", "-nr", "-s", "simulation.BasicSimulation",
            "-Dyield=false",
            f"-Dsimulation.host={host}",
            f"-Dsimulation.port={port}",
            f"-Dsimulation.protocol={protocol}",
            f"-Dsimulation.users={run_config['target_vusers']}",
            f"-Dsimulation.duration={run_config['duration_minutes']}",
            "-rf", "/results",
        ]
        return job
