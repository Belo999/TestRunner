from __future__ import annotations

from typing import Any

from .base import Engine, EngineResult
from .playwright_parser import parse_playwright_results


class PlaywrightEngine(Engine):
    @property
    def name(self) -> str:
        return "Playwright"

    def image(self) -> str:
        return "mcr.microsoft.com/playwright:v1.44.0-jammy"

    def script_filename(self) -> str:
        return "test.spec.js"

    def build_docker_command(
        self,
        script_path: str,
        result_dir: str,
        target_endpoint: str,
        target_vusers: int,
        duration_minutes: int,
        extra_options: dict[str, Any] | None = None,
    ) -> list[str]:
        return [
            "docker", "run", "-d",
            "--network", "host",
            "--name", f"mr-playwright-{id(script_path) % 100000}",
            "-v", f"{result_dir}:/results",
            "-v", f"{script_path}:/scripts:ro",
            "-e", f"TARGET_ENDPOINT={target_endpoint}",
            "-e", f"VUS={target_vusers}",
            "-e", f"DURATION={duration_minutes}",
            self.image(),
            "npx", "playwright", "test",
            "--config=/scripts/playwright.config.js",
        ]

    def parse_results(self, result_dir: str) -> EngineResult:
        return parse_playwright_results(result_dir)
