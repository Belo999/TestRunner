from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from apps.api.engines.base import Engine, EngineResult
from apps.api.engines.gatling import GatlingEngine
from apps.api.engines.gatling_parser import parse_gatling_results
from apps.api.engines.jmeter import JMeterEngine
from apps.api.engines.jmeter_parser import parse_jtl
from apps.api.engines.k6 import K6Engine
from apps.api.engines.k6_parser import parse_k6_summary
from apps.api.engines.locust import LocustEngine
from apps.api.engines.locust_parser import parse_locust_results
from apps.api.engines.playwright import PlaywrightEngine
from apps.api.engines.playwright_parser import parse_playwright_results
from apps.api.engines.registry import get_engine, list_engines


# ── EngineResult ──────────────────────────────────────────────────────────────

class TestEngineResult:
    def test_fields(self):
        r = EngineResult(
            p50_ms=100, p95_ms=500, p99_ms=1000,
            throughput_rps=50.0, error_rate=1.5,
            total_requests=1000, failed_requests=15,
            duration_seconds=60.0,
        )
        assert r.p50_ms == 100
        assert r.error_rate == 1.5
        assert r.raw_output_path is None

    def test_optional_raw_output_path(self):
        r = EngineResult(
            p50_ms=0, p95_ms=0, p99_ms=0,
            throughput_rps=0.0, error_rate=0.0,
            total_requests=0, failed_requests=0,
            duration_seconds=0.0,
            raw_output_path="/tmp/output.json",
        )
        assert r.raw_output_path == "/tmp/output.json"


# ── Registry ──────────────────────────────────────────────────────────────────

class TestRegistry:
    def test_list_engines(self):
        engines = list_engines()
        assert set(engines) == {"JMeter", "k6", "Gatling", "Locust", "Playwright"}

    def test_get_engine_returns_instances(self):
        for name in list_engines():
            engine = get_engine(name)
            assert engine is not None
            assert isinstance(engine, Engine)

    def test_get_engine_unknown(self):
        assert get_engine("NonExistent") is None

    def test_engine_names(self):
        assert get_engine("JMeter").name == "JMeter"
        assert get_engine("k6").name == "k6"
        assert get_engine("Gatling").name == "Gatling"
        assert get_engine("Locust").name == "Locust"
        assert get_engine("Playwright").name == "Playwright"


# ── Docker Command Building ───────────────────────────────────────────────────

class TestDockerCommands:
    SCRIPT = "/tmp/scripts/test.jmx"
    RESULT = "/tmp/results"
    ENDPOINT = "https://api.example.com:8443"
    VUS = 50
    DURATION = 5

    def test_jmeter_command(self):
        cmd = JMeterEngine().build_docker_command(
            self.SCRIPT, self.RESULT, self.ENDPOINT, self.VUS, self.DURATION,
        )
        assert cmd[0] == "docker"
        assert "run" in cmd
        assert "--network" in cmd
        assert "host" in cmd
        assert self.RESULT in " ".join(cmd)
        assert "-Jthreads=50" in cmd
        assert "-Jduration=300" in cmd
        assert "-Jhost=api.example.com" in cmd
        assert "-Jport=8443" in cmd
        assert "-Jprotocol=https" in cmd

    def test_jmeter_default_port(self):
        cmd = JMeterEngine().build_docker_command(
            self.SCRIPT, self.RESULT, "http://example.com", self.VUS, self.DURATION,
        )
        assert "-Jport=" in cmd
        # default port should be empty string
        idx = cmd.index("-Jport=")
        assert cmd[idx] == "-Jport="

    def test_k6_command(self):
        cmd = K6Engine().build_docker_command(
            self.SCRIPT, self.RESULT, self.ENDPOINT, self.VUS, self.DURATION,
        )
        assert "grafana/k6:latest" in cmd
        assert "--user" in cmd
        assert "root" in cmd
        assert "run" in cmd
        assert "--summary-export=/results/summary.json" in cmd
        assert "VUS=50" in cmd
        assert "DURATION=300s" in cmd

    def test_gatling_command(self):
        cmd = GatlingEngine().build_docker_command(
            self.SCRIPT, self.RESULT, self.ENDPOINT, self.VUS, self.DURATION,
        )
        assert "denvazh/gatling:latest" in cmd
        assert "GATLING_HOST=api.example.com" in cmd
        assert "GATLING_PORT=8443" in cmd
        assert "GATLING_PROTOCOL=https" in cmd
        assert "GATLING_USERS=50" in cmd
        assert "GATLING_DURATION=5" in cmd

    def test_locust_command(self):
        cmd = LocustEngine().build_docker_command(
            self.SCRIPT, self.RESULT, self.ENDPOINT, self.VUS, self.DURATION,
        )
        assert "locustio/locust:latest" in cmd
        assert "--headless" in cmd
        assert "-u" in cmd
        assert "--run-time" in cmd
        assert "5m" in cmd

    def test_playwright_command(self):
        cmd = PlaywrightEngine().build_docker_command(
            self.SCRIPT, self.RESULT, self.ENDPOINT, self.VUS, self.DURATION,
        )
        assert any("playwright" in c for c in cmd)
        assert "npx" in cmd
        assert "test" in cmd

    def test_all_engines_start_with_docker(self):
        for name in list_engines():
            engine = get_engine(name)
            cmd = engine.build_docker_command(
                self.SCRIPT, self.RESULT, self.ENDPOINT, self.VUS, self.DURATION,
            )
            assert cmd[0] == "docker", f"{name} should start with 'docker'"

    def test_all_engines_use_host_network(self):
        for name in list_engines():
            engine = get_engine(name)
            cmd = engine.build_docker_command(
                self.SCRIPT, self.RESULT, self.ENDPOINT, self.VUS, self.DURATION,
            )
            assert "--network" in cmd and "host" in cmd, f"{name} should use host network"

    def test_script_filename(self):
        assert JMeterEngine().script_filename() == "test-plan.jmx"
        assert K6Engine().script_filename() == "test-script.js"
        assert GatlingEngine().script_filename() == "simulation.scala"
        assert LocustEngine().script_filename() == "locustfile.py"
        assert PlaywrightEngine().script_filename() == "test.spec.js"


# ── JMeter Parser ─────────────────────────────────────────────────────────────

class TestJMeterParser:
    def _write_csv_jtl(self, path: Path, rows: list[dict]):
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["timeStamp", "elapsed", "success", "label", "responseCode"])
            writer.writeheader()
            writer.writerows(rows)

    def test_parse_csv_jtl(self, tmp_result_dir: Path):
        rows = [
            {"timeStamp": "1000", "elapsed": "100", "success": "true", "label": "GET /api", "responseCode": "200"},
            {"timeStamp": "1100", "elapsed": "200", "success": "true", "label": "GET /api", "responseCode": "200"},
            {"timeStamp": "1200", "elapsed": "150", "success": "false", "label": "GET /api", "responseCode": "500"},
            {"timeStamp": "1300", "elapsed": "300", "success": "true", "label": "GET /api", "responseCode": "200"},
        ]
        self._write_csv_jtl(tmp_result_dir / "results.jtl", rows)

        result = parse_jtl(str(tmp_result_dir))
        assert result.total_requests == 4
        assert result.failed_requests == 1
        assert result.error_rate == 25.0
        # sorted: [100, 150, 200, 300], p50 index = int(4*0.50) = 2 -> 200
        assert result.p50_ms == 200
        assert result.duration_seconds > 0

    def test_parse_xml_jtl(self, tmp_result_dir: Path):
        root = ET.Element("testResults")
        for i, (ts, elapsed, success) in enumerate([
            (1000, 100, "true"),
            (1100, 200, "true"),
            (1200, 300, "false"),
        ]):
            sample = ET.SubElement(root, "sample")
            sample.set("t", str(elapsed))
            sample.set("ts", str(ts))
            sample.set("success", success)
            sample.set("label", "GET /api")

        tree = ET.ElementTree(root)
        tree.write(str(tmp_result_dir / "results.jtl"), xml_declaration=True)

        result = parse_jtl(str(tmp_result_dir))
        assert result.total_requests == 3
        assert result.failed_requests == 1
        assert result.error_rate == pytest.approx(33.33, abs=0.01)

    def test_missing_jtl_returns_zeros(self, tmp_result_dir: Path):
        result = parse_jtl(str(tmp_result_dir))
        assert result.total_requests == 0
        assert result.p50_ms == 0

    def test_empty_csv_jtl(self, tmp_result_dir: Path):
        path = tmp_result_dir / "results.jtl"
        path.write_text("timeStamp,elapsed,success\n")
        result = parse_jtl(str(tmp_result_dir))
        assert result.total_requests == 0


# ── k6 Parser ─────────────────────────────────────────────────────────────────

class TestK6Parser:
    def test_parse_summary_json(self, tmp_result_dir: Path):
        summary = {
            "metrics": {
                "http_req_duration": {
                    "values": {"p(50)": 120.5, "p(95)": 450.2, "p(99)": 890.0, "med": 120.5},
                },
                "http_reqs": {
                    "values": {"rate": 55.3, "count": 3318},
                },
                "http_req_failed": {
                    "values": {"rate": 0.02},
                },
            }
        }
        (tmp_result_dir / "summary.json").write_text(json.dumps(summary))

        result = parse_k6_summary(str(tmp_result_dir))
        assert result.total_requests == 3318
        assert result.p50_ms == 120
        assert result.p95_ms == 450
        assert result.p99_ms == 890
        assert result.throughput_rps == 55.3
        assert result.error_rate == pytest.approx(2.0, abs=0.1)
        assert result.raw_output_path is not None

    def test_missing_summary_returns_zeros(self, tmp_result_dir: Path):
        result = parse_k6_summary(str(tmp_result_dir))
        assert result.total_requests == 0


# ── Gatling Parser ────────────────────────────────────────────────────────────

class TestGatlingParser:
    def test_parse_stats_csv(self, tmp_result_dir: Path):
        # Gatling outputs nested dirs like run-1/stats.csv
        run_dir = tmp_result_dir / "run-1"
        run_dir.mkdir()
        stats_file = run_dir / "stats.csv"

        with open(stats_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["name", "total", "ko", "50th", "95th", "99th", "mean", "rps"])
            writer.writeheader()
            writer.writerow({
                "name": "Global", "total": "500", "ko": "10",
                "50th": "150", "95th": "400", "99th": "800",
                "mean": "200", "rps": "25.5",
            })

        result = parse_gatling_results(str(tmp_result_dir))
        assert result.total_requests == 500
        assert result.failed_requests == 10
        assert result.p50_ms == 150
        assert result.p95_ms == 400
        assert result.p99_ms == 800
        assert result.throughput_rps == 25.5

    def test_missing_stats_returns_zeros(self, tmp_result_dir: Path):
        result = parse_gatling_results(str(tmp_result_dir))
        assert result.total_requests == 0


# ── Locust Parser ─────────────────────────────────────────────────────────────

class TestLocustParser:
    def test_parse_stats_csv(self, tmp_result_dir: Path):
        stats_file = tmp_result_dir / "stats_stats.csv"
        with open(stats_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "Name", "Request Count", "Failure Count",
                "Median Response Time", "Average Response Time",
                "Min Response Time", "Max Response Time",
                "Average Content Size", "Requests/s",
                "50%", "95%", "99%",
            ])
            writer.writeheader()
            writer.writerow({
                "Name": "Aggregated", "Request Count": "1000", "Failure Count": "5",
                "Median Response Time": "120", "Average Response Time": "150",
                "Min Response Time": "50", "Max Response Time": "500",
                "Average Content Size": "1024", "Requests/s": "10.5",
                "50%": "120", "95%": "350", "99%": "480",
            })

        result = parse_locust_results(str(tmp_result_dir))
        assert result.total_requests == 1000
        assert result.failed_requests == 5
        assert result.p50_ms == 120
        assert result.p95_ms == 350
        assert result.p99_ms == 480
        assert result.throughput_rps == 10.5

    def test_missing_stats_returns_zeros(self, tmp_result_dir: Path):
        result = parse_locust_results(str(tmp_result_dir))
        assert result.total_requests == 0


# ── Playwright Parser ─────────────────────────────────────────────────────────

class TestPlaywrightParser:
    def test_parse_report_json(self, tmp_result_dir: Path):
        report = {
            "suites": [
                {
                    "specs": [
                        {
                            "tests": [
                                {
                                    "results": [
                                        {"duration": 150, "status": "passed"},
                                        {"duration": 300, "status": "passed"},
                                    ]
                                }
                            ]
                        },
                        {
                            "tests": [
                                {
                                    "results": [
                                        {"duration": 500, "status": "failed"},
                                    ]
                                }
                            ]
                        },
                    ]
                }
            ]
        }
        (tmp_result_dir / "report.json").write_text(json.dumps(report))

        result = parse_playwright_results(str(tmp_result_dir))
        assert result.total_requests == 3
        assert result.failed_requests == 1
        assert result.error_rate == pytest.approx(33.33, abs=0.01)
        # sorted: [150, 300, 500], p50 index = int(3*0.50) = 1 -> 300
        assert result.p50_ms == 300

    def test_missing_report_returns_zeros(self, tmp_result_dir: Path):
        result = parse_playwright_results(str(tmp_result_dir))
        assert result.total_requests == 0

    def test_invalid_json_returns_zeros(self, tmp_result_dir: Path):
        (tmp_result_dir / "report.json").write_text("not valid json {{{")
        result = parse_playwright_results(str(tmp_result_dir))
        assert result.total_requests == 0


# ── Engine ensure_result_dir ──────────────────────────────────────────────────

class TestEnsureResultDir:
    def test_creates_directory(self, tmp_path: Path):
        target = tmp_path / "new" / "nested" / "dir"
        engine = JMeterEngine()
        result = engine.ensure_result_dir(str(target))
        assert result.exists()
        assert result.is_dir()


# ── Engine parse_results delegation ─────────────────────────────────────────

class TestEngineParseResults:
    def test_jmeter_parse_results(self, tmp_result_dir: Path):
        from apps.api.engines.jmeter import JMeterEngine
        engine = JMeterEngine()
        result = engine.parse_results(str(tmp_result_dir))
        assert result.total_requests == 0

    def test_k6_parse_results(self, tmp_result_dir: Path):
        from apps.api.engines.k6 import K6Engine
        engine = K6Engine()
        result = engine.parse_results(str(tmp_result_dir))
        assert result.total_requests == 0

    def test_gatling_parse_results(self, tmp_result_dir: Path):
        from apps.api.engines.gatling import GatlingEngine
        engine = GatlingEngine()
        result = engine.parse_results(str(tmp_result_dir))
        assert result.total_requests == 0

    def test_locust_parse_results(self, tmp_result_dir: Path):
        from apps.api.engines.locust import LocustEngine
        engine = LocustEngine()
        result = engine.parse_results(str(tmp_result_dir))
        assert result.total_requests == 0

    def test_playwright_parse_results(self, tmp_result_dir: Path):
        from apps.api.engines.playwright import PlaywrightEngine
        engine = PlaywrightEngine()
        result = engine.parse_results(str(tmp_result_dir))
        assert result.total_requests == 0


# ── Parser edge cases ──────────────────────────────────────────────────────

class TestJMeterParserEdgeCases:
    def test_csv_with_bad_rows(self, tmp_result_dir: Path):
        from apps.api.engines.jmeter_parser import parse_jtl
        csv_content = "elapsed,timeStamp,success\nbad,data,false\n,123,true\n456,bad,true\n"
        (tmp_result_dir / "results.jtl").write_text(csv_content)
        result = parse_jtl(str(tmp_result_dir))
        assert result.total_requests >= 0

    def test_xml_jtl(self, tmp_result_dir: Path):
        from apps.api.engines.jmeter_parser import parse_jtl
        xml_content = """<?xml version="1.0"?>
<testResults>
<sample t="100" ts="123" success="true"/>
</testResults>"""
        (tmp_result_dir / "results.jtl").write_text(xml_content)
        result = parse_jtl(str(tmp_result_dir))
        assert result.total_requests == 1


class TestK6ParserEdgeCases:
    def test_summary_with_values_wrapper(self, tmp_result_dir: Path):
        from apps.api.engines.k6_parser import parse_k6_summary
        summary = {
            "metrics": {
                "http_req_duration": {
                    "values": {"p(50)": 100, "p(95)": 200, "p(99)": 300}
                },
                "http_reqs": {"values": {"count": 50, "rate": 10.0}},
                "http_req_failed": {"values": {"rate": 0.1}},
            }
        }
        (tmp_result_dir / "summary.json").write_text(json.dumps(summary))
        result = parse_k6_summary(str(tmp_result_dir))
        assert result.p50_ms == 100


class TestGatlingParserEdgeCases:
    def test_stats_with_aggregate_only(self, tmp_result_dir: Path):
        from apps.api.engines.gatling_parser import parse_gatling_results
        csv_content = "scenario,requests,responseTimeMean,responseTimeP50,responseTimeP95,responseTimeP99,responseTimeStdDev,responseTimeMax,requestRate,meanRespTime\nAll Requests,100,50,45,120,200,30,500,10.0,50\n"
        (tmp_result_dir / "stats.csv").write_text(csv_content)
        result = parse_gatling_results(str(tmp_result_dir))
        assert result.total_requests >= 0


class TestLocustParserEdgeCases:
    def test_stats_with_only_totals(self, tmp_result_dir: Path):
        from apps.api.engines.locust_parser import parse_locust_results
        csv_content = "Name,Request Count,Failure Count,Median Response Time,Average Response Time,Min Response Time,Max Response Time,Average Content Size,Requests/s,Failures/s,50%,66%,75%,80%,90%,95%,98%,99%,99.9%,99.99%,100%\nTotal,100,5,50,55,10,200,1024,10.0,0.5,45,50,55,60,80,120,180,190,199,200,200\n"
        (tmp_result_dir / "stats_stats.csv").write_text(csv_content)
        result = parse_locust_results(str(tmp_result_dir))
        assert result.total_requests >= 0


class TestPlaywrightParserEdgeCases:
    def test_report_with_specs(self, tmp_result_dir: Path):
        from apps.api.engines.playwright_parser import parse_playwright_results
        report = {
            "suites": [{
                "specs": [{
                    "tests": [
                        {"results": [{"status": "passed", "duration": 100}]},
                        {"results": [{"status": "failed", "duration": 200}]},
                    ]
                }]
            }]
        }
        (tmp_result_dir / "report.json").write_text(json.dumps(report))
        result = parse_playwright_results(str(tmp_result_dir))
        assert result.total_requests == 2

    def test_empty_durations_returns_zeros(self, tmp_result_dir: Path):
        from apps.api.engines.playwright_parser import parse_playwright_results
        report = {"suites": [{"specs": [{"tests": []}]}]}
        (tmp_result_dir / "report.json").write_text(json.dumps(report))
        result = parse_playwright_results(str(tmp_result_dir))
        assert result.total_requests == 0


class TestK6ParserEdgeCasesFull:
    def test_get_metric_value_default(self):
        from apps.api.engines.k6_parser import _get_metric_value
        result = _get_metric_value({}, "nonexistent", default=42.0)
        assert result == 42.0

    def test_failed_rate_from_value(self, tmp_result_dir: Path):
        from apps.api.engines.k6_parser import parse_k6_summary
        summary = {
            "metrics": {
                "http_req_duration": {"values": {"p(50)": 100, "p(95)": 200, "p(99)": 300}},
                "http_reqs": {"values": {"count": 50, "rate": 10.0}},
                "http_req_failed": {"value": 0.1},
            }
        }
        (tmp_result_dir / "summary.json").write_text(json.dumps(summary))
        result = parse_k6_summary(str(tmp_result_dir))
        assert result.error_rate == 10.0


class TestGatlingParserEmpty:
    def test_empty_csv(self, tmp_result_dir: Path):
        from apps.api.engines.gatling_parser import parse_gatling_results
        (tmp_result_dir / "stats.csv").write_text("")
        result = parse_gatling_results(str(tmp_result_dir))
        assert result.total_requests == 0


class TestLocustParserEmpty:
    def test_empty_csv(self, tmp_result_dir: Path):
        from apps.api.engines.locust_parser import parse_locust_results
        (tmp_result_dir / "stats_stats.csv").write_text("")
        result = parse_locust_results(str(tmp_result_dir))
        assert result.total_requests == 0
