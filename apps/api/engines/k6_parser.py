from __future__ import annotations

import json
from pathlib import Path

from .base import EngineResult


def _get_metric_value(metric: dict, *keys: str, default: float = 0.0) -> float:
    source = metric.get("values", metric)
    for key in keys:
        if key in source:
            return source[key]
    return default


def parse_k6_summary(result_dir: str) -> EngineResult:
    summary_path = Path(result_dir) / "summary.json"
    if not summary_path.exists():
        return EngineResult(
            p50_ms=0, p95_ms=0, p99_ms=0,
            throughput_rps=0.0, error_rate=0.0,
            total_requests=0, failed_requests=0,
            duration_seconds=0.0,
        )

    with open(summary_path, encoding="utf-8") as f:
        data = json.load(f)

    metrics = data.get("metrics", {})

    duration_metric = metrics.get("http_req_duration", {})
    p50 = int(_get_metric_value(duration_metric, "p(50)", "med"))
    p95 = int(_get_metric_value(duration_metric, "p(95)"))
    p99 = int(_get_metric_value(duration_metric, "p(99)", "max"))

    reqs_metric = metrics.get("http_reqs", {})
    rate = round(_get_metric_value(reqs_metric, "rate"), 2)
    count = int(_get_metric_value(reqs_metric, "count"))

    failed_metric = metrics.get("http_req_failed", {})
    failed_rate_val = _get_metric_value(failed_metric, "rate", 0.0)
    if failed_rate_val == 0.0 and "value" in failed_metric:
        failed_rate_val = failed_metric["value"]
    failed_rate = round(failed_rate_val * 100, 2)
    failed_count = int(count * failed_rate_val)

    duration_seconds = 0.0
    if rate > 0 and count > 0:
        duration_seconds = round(count / rate, 2)

    return EngineResult(
        p50_ms=p50,
        p95_ms=p95,
        p99_ms=p99,
        throughput_rps=rate,
        error_rate=failed_rate,
        total_requests=count,
        failed_requests=failed_count,
        duration_seconds=duration_seconds,
        raw_output_path=str(summary_path),
    )
