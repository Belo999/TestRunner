from __future__ import annotations

import json
from pathlib import Path

from .base import EngineResult


def parse_playwright_results(result_dir: str) -> EngineResult:
    results_dir = Path(result_dir)

    report_files = list(results_dir.glob("**/report.json"))
    if not report_files:
        report_files = list(results_dir.glob("**/results.json"))

    if not report_files:
        return EngineResult(
            p50_ms=0, p95_ms=0, p99_ms=0,
            throughput_rps=0.0, error_rate=0.0,
            total_requests=0, failed_requests=0,
            duration_seconds=0.0,
        )

    report_file = report_files[0]
    return _parse_report_json(report_file)


def _parse_report_json(report_file: Path) -> EngineResult:
    try:
        with open(report_file, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return EngineResult(
            p50_ms=0, p95_ms=0, p99_ms=0,
            throughput_rps=0.0, error_rate=0.0,
            total_requests=0, failed_requests=0,
            duration_seconds=0.0,
        )

    durations = []
    errors = 0
    total = 0

    suites = data.get("suites", [])
    for suite in suites:
        specs = suite.get("specs", [])
        for spec in specs:
            tests = spec.get("tests", [])
            for test in tests:
                results = test.get("results", [])
                for result in results:
                    total += 1
                    duration = result.get("duration", 0)
                    durations.append(duration)
                    status = result.get("status", "")
                    if status in ("failed", "timedOut", "interrupted"):
                        errors += 1

    if not durations:
        return EngineResult(
            p50_ms=0, p95_ms=0, p99_ms=0,
            throughput_rps=0.0, error_rate=0.0,
            total_requests=0, failed_requests=0,
            duration_seconds=0.0,
        )

    durations.sort()
    p50 = int(durations[int(len(durations) * 0.50)])
    p95 = int(durations[int(len(durations) * 0.95)])
    p99 = int(durations[min(int(len(durations) * 0.99), len(durations) - 1)])

    duration_seconds = sum(durations) / 1000.0 if durations else 0.0
    throughput = round(total / max(duration_seconds, 1.0), 2)
    error_rate = round((errors / total) * 100, 2) if total > 0 else 0.0

    return EngineResult(
        p50_ms=p50,
        p95_ms=p95,
        p99_ms=p99,
        throughput_rps=throughput,
        error_rate=error_rate,
        total_requests=total,
        failed_requests=errors,
        duration_seconds=round(duration_seconds, 2),
    )
