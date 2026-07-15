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

    def build_k8s_job_spec(self, run_config: dict[str, Any]) -> dict[str, Any]:
        """Build a Kubernetes Job spec for this engine.

        Args:
            run_config: Dictionary containing:
                - run_id: Database run ID
                - engine: Engine name
                - target_endpoint: URL of the system under test
                - target_vusers: Number of virtual users
                - duration_minutes: Test duration in minutes
                - script_configmap: ConfigMap name with test scripts
                - namespace: Target namespace (default: marathonrunner-execution)
                - labels: Optional custom labels
                - resources: Optional resource requirements
        """
        namespace = run_config.get("namespace", "marathonrunner-execution")
        run_id = run_config["run_id"]
        labels = {
            "marathonrunner.io/run-id": str(run_id),
            "marathonrunner.io/engine": run_config["engine"],
            "marathonrunner.io/managed": "true",
        }
        labels.update(run_config.get("labels", {}))

        resources = run_config.get("resources", {
            "requests": {"cpu": "500m", "memory": "512Mi"},
            "limits": {"cpu": "2", "memory": "2Gi"},
        })

        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": f"mr-run-{run_id}-{run_config['engine'].lower()}",
                "namespace": namespace,
                "labels": labels,
            },
            "spec": {
                "backoffLimit": 0,
                "ttlSecondsAfterFinished": 3600,
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "restartPolicy": "Never",
                        "containers": [{
                            "name": run_config["engine"].lower(),
                            "image": self.image(),
                            "env": [
                                {"name": "TARGET_ENDPOINT", "value": run_config["target_endpoint"]},
                                {"name": "VUS", "value": str(run_config["target_vusers"])},
                                {"name": "DURATION", "value": str(run_config["duration_minutes"] * 60) + "s"},
                            ],
                            "volumeMounts": [
                                {"name": "scripts", "mountPath": "/scripts", "readOnly": True},
                                {"name": "results", "mountPath": "/results"},
                            ],
                            "resources": resources,
                        }],
                        "volumes": [
                            {"name": "scripts", "configMap": {"name": run_config["script_configmap"]}},
                            {"name": "results", "emptyDir": {}},
                        ],
                    },
                },
            },
        }

    def ensure_result_dir(self, result_dir: str) -> Path:
        path = Path(result_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path
