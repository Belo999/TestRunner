from __future__ import annotations

import csv
from pathlib import Path

from .base import EngineResult


def parse_gatling_results(result_dir: str) -> EngineResult:
    results_dir = Path(result_dir)

    stats_files = list(results_dir.glob("**/stats.csv"))
    if not stats_files:
        stats_files = list(results_dir.glob("**/*_stats.csv"))

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
    total_duration = 0.0
    p50 = 0
    p95 = 0
    p99 = 0
    max_throughput = 0.0

    for row in rows:
        name = row.get("name", "")
        if name == "Global":
            total_requests = int(row.get("total", row.get("count", "0")))
            failed_requests = int(row.get("ko", "0"))
            p50 = int(float(row.get("50th", row.get("p50", "0"))))
            p95 = int(float(row.get("95th", row.get("p95", "0"))))
            p99 = int(float(row.get("99th", row.get("p99", "0"))))
            total_duration = float(row.get("mean", "0"))
            max_throughput = float(row.get("rps", row.get("req/s", "0")))
            break

    if total_requests == 0 and rows:
        row = rows[0]
        total_requests = int(row.get("total", row.get("count", "0")))
        failed_requests = int(row.get("ko", "0"))
        p50 = int(float(row.get("50th", row.get("p50", "0"))))
        p95 = int(float(row.get("95th", row.get("p95", "0"))))
        p99 = int(float(row.get("99th", row.get("p99", "0"))))
        max_throughput = float(row.get("rps", row.get("req/s", "0")))

    duration_seconds = 60.0
    if total_requests > 0 and max_throughput > 0:
        duration_seconds = round(total_requests / max_throughput, 2)

    throughput = round(max_throughput, 2) if max_throughput > 0 else round(total_requests / max(duration_seconds, 1), 2)
    error_rate = round((failed_requests / total_requests) * 100, 2) if total_requests > 0 else 0.0

    return EngineResult(
        p50_ms=p50,
        p95_ms=p95,
        p99_ms=p99,
        throughput_rps=throughput,
        error_rate=error_rate,
        total_requests=total_requests,
        failed_requests=failed_requests,
        duration_seconds=duration_seconds,
    )
