from __future__ import annotations

from typing import Any

from .base import Engine, EngineResult
from .k6_parser import parse_k6_summary


class K6Engine(Engine):
    @property
    def name(self) -> str:
        return "k6"

    def image(self) -> str:
        return "grafana/k6:latest"

    def script_filename(self) -> str:
        return "test-script.js"

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
        return [
            "docker", "run", "-d",
            "--network", "host",
            "--user", "root",
            "--name", f"mr-k6-{id(script_path) % 100000}",
            "-v", f"{result_dir}:/results",
            "-v", f"{script_path}:/scripts:ro",
            "-e", f"VUS={target_vusers}",
            "-e", f"DURATION={duration_seconds}s",
            "-e", f"TARGET_ENDPOINT={target_endpoint}",
            self.image(),
            "run",
            "--summary-export=/results/summary.json",
            "/scripts/sample-test.js",
        ]

    def parse_results(self, result_dir: str) -> EngineResult:
        return parse_k6_summary(result_dir)
