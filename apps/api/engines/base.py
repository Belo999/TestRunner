from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class EngineResult:
    p50_ms: int
    p95_ms: int
    p99_ms: int
    throughput_rps: float
    error_rate: float
    total_requests: int
    failed_requests: int
    duration_seconds: float
    raw_output_path: str | None = None


class Engine(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def image(self) -> str:
        ...

    @abstractmethod
    def build_docker_command(
        self,
        script_path: str,
        result_dir: str,
        target_endpoint: str,
        target_vusers: int,
        duration_minutes: int,
        extra_options: dict[str, Any] | None = None,
    ) -> list[str]:
        ...

    @abstractmethod
    def parse_results(self, result_dir: str) -> EngineResult:
        ...

    @abstractmethod
    def script_filename(self) -> str:
        ...

    def ensure_result_dir(self, result_dir: str) -> Path:
        path = Path(result_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path
