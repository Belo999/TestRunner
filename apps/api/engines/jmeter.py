from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .base import Engine, EngineResult
from .jmeter_parser import parse_jtl


class JMeterEngine(Engine):
    @property
    def name(self) -> str:
        return "JMeter"

    def image(self) -> str:
        return "justb4/jmeter:latest"

    def script_filename(self) -> str:
        return "test-plan.jmx"

    def build_docker_command(
        self,
        script_path: str,
        result_dir: str,
        target_endpoint: str,
        target_vusers: int,
        duration_minutes: int,
        extra_options: dict[str, Any] | None = None,
    ) -> list[str]:
        duration_seconds = duration_minutes * 60
        parsed = urlparse(target_endpoint)
        host = parsed.hostname or target_endpoint
        port = str(parsed.port) if parsed.port else ""
        protocol = parsed.scheme or "http"

        return [
            "docker", "run", "-d",
            "--network", "host",
            "--name", f"mr-jmeter-{id(script_path) % 100000}",
            "-v", f"{result_dir}:/results",
            "-v", f"{script_path}:/test/scripts:ro",
            "-e", f"TARGET_ENDPOINT={target_endpoint}",
            "-e", f"THREAD_COUNT={target_vusers}",
            "-e", f"DURATION={duration_seconds}",
            self.image(),
            "-n",
            "-t", "/test/scripts/sample-test.jmx",
            "-Jthreads=" + str(target_vusers),
            "-Jduration=" + str(duration_seconds),
            "-Jhost=" + host,
            "-Jport=" + port,
            "-Jprotocol=" + protocol,
            "-l", "/results/results.jtl",
            "-e", "-o", "/results/report",
        ]

    def parse_results(self, result_dir: str) -> EngineResult:
        return parse_jtl(result_dir)

    def build_k8s_job_spec(self, run_config: dict[str, Any]) -> dict[str, Any]:
        job = super().build_k8s_job_spec(run_config)
        container = job["spec"]["template"]["spec"]["containers"][0]
        duration_seconds = run_config["duration_minutes"] * 60
        target_endpoint = run_config["target_endpoint"]
        parsed = urlparse(target_endpoint)
        host = parsed.hostname or target_endpoint
        port = str(parsed.port) if parsed.port else ""
        protocol = parsed.scheme or "http"
        container["command"] = [
            "jmeter", "-n",
            "-t", "/scripts/test-plan.jmx",
            "-Jthreads", str(run_config["target_vusers"]),
            "-Jduration", str(duration_seconds),
            "-Jhost", host,
            "-Jport", port,
            "-Jprotocol", protocol,
            "-l", "/results/results.jtl",
            "-e", "-o", "/results/report",
        ]
        return job
