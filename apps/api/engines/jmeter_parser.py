from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from pathlib import Path

from .base import EngineResult


def _parse_csv_jtl(jtl_path: Path) -> EngineResult:
    response_times: list[int] = []
    errors = 0
    timestamps: list[int] = []

    with open(jtl_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                elapsed = int(row.get("elapsed", "0"))
                ts = int(row.get("timeStamp", "0"))
            except (ValueError, TypeError):
                continue
            success = row.get("success", "true").lower() == "true"
            response_times.append(elapsed)
            timestamps.append(ts)
            if not success:
                errors += 1

    return _build_result(response_times, errors, timestamps)


def _parse_xml_jtl(jtl_path: Path) -> EngineResult:
    tree = ET.parse(jtl_path)
    root = tree.getroot()

    response_times: list[int] = []
    errors = 0
    timestamps: list[int] = []

    for sample in root.iter("sample"):
        t = int(sample.get("t", "0"))
        ts = int(sample.get("ts", "0"))
        success = sample.get("success", "true")
        response_times.append(t)
        timestamps.append(ts)
        if success.lower() == "false":
            errors += 1

    return _build_result(response_times, errors, timestamps)


def _build_result(response_times: list[int], errors: int, timestamps: list[int]) -> EngineResult:
    if not response_times:
        return EngineResult(
            p50_ms=0, p95_ms=0, p99_ms=0,
            throughput_rps=0.0, error_rate=0.0,
            total_requests=0, failed_requests=0,
            duration_seconds=0.0,
        )

    response_times.sort()
    total = len(response_times)

    p50 = response_times[int(total * 0.50)]
    p95 = response_times[int(total * 0.95)]
    p99 = response_times[min(int(total * 0.99), total - 1)]

    duration_ms = max(1, max(timestamps) - min(timestamps))
    duration_seconds = duration_ms / 1000.0
    throughput = round(total / duration_seconds, 2) if duration_seconds > 0 else 0.0
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


def parse_jtl(result_dir: str) -> EngineResult:
    jtl_path = Path(result_dir) / "results.jtl"
    if not jtl_path.exists():
        return EngineResult(
            p50_ms=0, p95_ms=0, p99_ms=0,
            throughput_rps=0.0, error_rate=0.0,
            total_requests=0, failed_requests=0,
            duration_seconds=0.0,
        )

    with open(jtl_path, encoding="utf-8") as f:
        first_char = f.read(1)

    if first_char == "<":
        return _parse_xml_jtl(jtl_path)
    else:
        return _parse_csv_jtl(jtl_path)
