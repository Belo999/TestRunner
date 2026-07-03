from __future__ import annotations

import csv
from pathlib import Path

from .base import EngineResult


def parse_locust_results(result_dir: str) -> EngineResult:
    results_dir = Path(result_dir)

    stats_files = list(results_dir.glob("stats_stats.csv"))
    if not stats_files:
        stats_files = list(results_dir.glob("*_stats.csv"))

    if not stats_files:
        return EngineResult(
            p50_ms=0, p95_ms=0, p99_ms=0,
            throughput_rps=0.0, error_rate=0.0,
            total_requests=0, failed_requests=0,
            duration_seconds=0.0,
        )

    stats_file = stats_files[0]
    return _parse_stats_csv(stats_file)


def _parse_stats_csv(stats_file: Path) -> EngineResult:
    with open(stats_file, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        return EngineResult(
            p50_ms=0, p95_ms=0, p99_ms=0,
            throughput_rps=0.0, error_rate=0.0,
            total_requests=0, failed_requests=0,
            duration_seconds=0.0,
        )

    total_requests = 0
    failed_requests = 0
    p50 = 0
    p95 = 0
    p99 = 0
    rps = 0.0

    for row in rows:
        name = row.get("Name", "")
        if name == "Aggregated" or name == "":
            total_requests = int(row.get("Request Count", row.get("num_requests", "0")))
            failed_requests = int(row.get("Failure Count", row.get("num_failures", "0")))
            p50 = int(float(row.get("50%", row.get("50_percentage", "0"))))
            p95 = int(float(row.get("95%", row.get("95_percentage", "0"))))
            p99 = int(float(row.get("99%", row.get("99_percentage", "0"))))
            rps = float(row.get("Requests/s", row.get("rps", "0")))
            break

    if total_requests == 0 and rows:
        row = rows[-1]
        total_requests = int(row.get("Request Count", row.get("num_requests", "0")))
        failed_requests = int(row.get("Failure Count", row.get("num_failures", "0")))
        p50 = int(float(row.get("50%", row.get("50_percentage", "0"))))
        p95 = int(float(row.get("95%", row.get("95_percentage", "0"))))
        p99 = int(float(row.get("99%", row.get("99_percentage", "0"))))
        rps = float(row.get("Requests/s", row.get("rps", "0")))

    error_rate = round((failed_requests / total_requests) * 100, 2) if total_requests > 0 else 0.0

    return EngineResult(
        p50_ms=p50,
        p95_ms=p95,
        p99_ms=p99,
        throughput_rps=round(rps, 2),
        error_rate=error_rate,
        total_requests=total_requests,
        failed_requests=failed_requests,
        duration_seconds=0.0,
    )
