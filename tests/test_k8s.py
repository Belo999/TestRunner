from __future__ import annotations

import pytest

from apps.api.engines import get_engine
from apps.api.engines.base import Engine


class TestEngineK8sJobSpec:
    """Tests for engine build_k8s_job_spec methods."""

    def _base_config(self) -> dict:
        return {
            "run_id": 42,
            "engine": "k6",
            "target_endpoint": "https://api.example.com",
            "target_vusers": 100,
            "duration_minutes": 5,
            "script_configmap": "run-42-scripts",
            "namespace": "marathonrunner-execution",
        }

    def test_base_engine_k8s_job_spec_structure(self):
        engine = get_engine("k6")
        config = self._base_config()
        job = engine.build_k8s_job_spec(config)
        assert job["apiVersion"] == "batch/v1"
        assert job["kind"] == "Job"
        assert job["metadata"]["namespace"] == "marathonrunner-execution"
        assert "marathonrunner.io/run-id" in job["metadata"]["labels"]
        assert job["metadata"]["labels"]["marathonrunner.io/run-id"] == "42"

    def test_k6_engine_k8s_job_spec(self):
        engine = get_engine("k6")
        config = self._base_config()
        job = engine.build_k8s_job_spec(config)
        container = job["spec"]["template"]["spec"]["containers"][0]
        assert container["image"] == "grafana/k6:latest"
        assert container["command"] == ["k6", "run", "--summary-export=/results/summary.json", "/scripts/test.js"]
        env_names = [e["name"] for e in container["env"]]
        assert "TARGET_ENDPOINT" in env_names
        assert "VUS" in env_names
        assert "DURATION" in env_names

    def test_jmeter_engine_k8s_job_spec(self):
        engine = get_engine("JMeter")
        config = self._base_config()
        config["engine"] = "JMeter"
        job = engine.build_k8s_job_spec(config)
        container = job["spec"]["template"]["spec"]["containers"][0]
        assert container["image"] == "justb4/jmeter:latest"
        assert "jmeter" in container["command"]

    def test_gatling_engine_k8s_job_spec(self):
        engine = get_engine("Gatling")
        config = self._base_config()
        config["engine"] = "Gatling"
        job = engine.build_k8s_job_spec(config)
        container = job["spec"]["template"]["spec"]["containers"][0]
        assert container["image"] == "denvazh/gatling:latest"
        assert "gatling" in container["command"]

    def test_locust_engine_k8s_job_spec(self):
        engine = get_engine("Locust")
        config = self._base_config()
        config["engine"] = "Locust"
        job = engine.build_k8s_job_spec(config)
        container = job["spec"]["template"]["spec"]["containers"][0]
        assert container["image"] == "locustio/locust:latest"
        assert "locust" in container["command"]

    def test_playwright_engine_k8s_job_spec(self):
        engine = get_engine("Playwright")
        config = self._base_config()
        config["engine"] = "Playwright"
        job = engine.build_k8s_job_spec(config)
        container = job["spec"]["template"]["spec"]["containers"][0]
        assert container["image"] == "mcr.microsoft.com/playwright:v1.44.0-jammy"
        assert "playwright" in " ".join(container["command"])

    def test_k8s_job_spec_resources_default(self):
        engine = get_engine("k6")
        config = self._base_config()
        job = engine.build_k8s_job_spec(config)
        container = job["spec"]["template"]["spec"]["containers"][0]
        assert "requests" in container["resources"]
        assert "limits" in container["resources"]
        assert "cpu" in container["resources"]["requests"]
        assert "memory" in container["resources"]["requests"]

    def test_k8s_job_spec_resources_custom(self):
        engine = get_engine("k6")
        config = self._base_config()
        config["resources"] = {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "500m", "memory": "512Mi"},
        }
        job = engine.build_k8s_job_spec(config)
        container = job["spec"]["template"]["spec"]["containers"][0]
        assert container["resources"]["requests"]["cpu"] == "100m"

    def test_k8s_job_spec_custom_labels(self):
        engine = get_engine("k6")
        config = self._base_config()
        config["labels"] = {"project": "my-project", "team": "platform"}
        job = engine.build_k8s_job_spec(config)
        assert job["metadata"]["labels"]["project"] == "my-project"
        assert job["metadata"]["labels"]["team"] == "platform"

    def test_k8s_job_spec_volumes(self):
        engine = get_engine("k6")
        config = self._base_config()
        job = engine.build_k8s_job_spec(config)
        volumes = job["spec"]["template"]["spec"]["volumes"]
        vol_names = [v["name"] for v in volumes]
        assert "scripts" in vol_names
        assert "results" in vol_names

    def test_k8s_job_spec_volume_mounts(self):
        engine = get_engine("k6")
        config = self._base_config()
        job = engine.build_k8s_job_spec(config)
        container = job["spec"]["template"]["spec"]["containers"][0]
        mount_names = [m["name"] for m in container["volumeMounts"]]
        assert "scripts" in mount_names
        assert "results" in mount_names

    def test_k8s_job_spec_backoff_limit(self):
        engine = get_engine("k6")
        config = self._base_config()
        job = engine.build_k8s_job_spec(config)
        assert job["spec"]["backoffLimit"] == 0

    def test_k8s_job_spec_restart_policy(self):
        engine = get_engine("k6")
        config = self._base_config()
        job = engine.build_k8s_job_spec(config)
        pod_spec = job["spec"]["template"]["spec"]
        assert pod_spec["restartPolicy"] == "Never"

    def test_all_engines_have_k8s_job_spec(self):
        from apps.api.engines import list_engines
        for engine_name in list_engines():
            engine = get_engine(engine_name)
            config = self._base_config()
            config["engine"] = engine_name
            job = engine.build_k8s_job_spec(config)
            assert job["apiVersion"] == "batch/v1"
            assert job["kind"] == "Job"


class TestK8sModels:
    """Tests for K8s-related model functions."""

    def test_get_execution_mode_default(self):
        from apps.api.models import get_execution_mode
        mode = get_execution_mode()
        assert mode in ("docker", "kubernetes")

    def test_build_k8s_testrun_spec(self):
        from apps.api.models import build_k8s_testrun_spec
        engine = get_engine("k6")
        run = {
            "id": 42,
            "engine": "k6",
            "project_id": 1,
            "target_endpoint": "https://api.example.com",
            "target_vusers": 100,
            "duration_minutes": 5,
            "load_profile": "constant",
        }
        spec = build_k8s_testrun_spec(run, engine)
        assert spec["apiVersion"] == "marathonrunner.io/v1alpha1"
        assert spec["kind"] == "TestRun"
        assert spec["spec"]["runId"] == 42
        assert spec["spec"]["engine"] == "k6"
        assert spec["spec"]["targetVusers"] == 100
        assert spec["spec"]["durationMinutes"] == 5

    def test_create_k8s_testrun(self):
        from apps.api.models import create_k8s_testrun
        engine = get_engine("k6")
        run = {
            "id": 42,
            "engine": "k6",
            "project_id": 1,
            "target_endpoint": "https://api.example.com",
            "target_vusers": 100,
            "duration_minutes": 5,
        }
        spec = create_k8s_testrun(run, engine)
        assert spec["kind"] == "TestRun"
        assert spec["spec"]["runId"] == 42

    def test_delete_k8s_testrun(self):
        from apps.api.models import delete_k8s_testrun
        result = delete_k8s_testrun(42, "k6")
        assert result is True

    def test_list_k8s_jobs(self):
        from apps.api.models import list_k8s_jobs
        jobs = list_k8s_jobs()
        assert isinstance(jobs, list)

    def test_get_k8s_cluster_nodes(self):
        from apps.api.models import get_k8s_cluster_nodes
        nodes = get_k8s_cluster_nodes()
        assert isinstance(nodes, list)
