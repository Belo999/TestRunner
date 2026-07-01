# Architecture Overview

MarathonRunner Enterprise uses a layered architecture that separates user experience, orchestration, execution, data, and observability.

## Architecture Layers

1. Portal and API layer for users and integrations.
2. Control plane layer for orchestration, validation, and governance.
3. CI/CD automation layer using Jenkins.
4. Infrastructure automation layer using Ansible and Rancher.
5. Kubernetes execution layer for distributed test engines.
6. Data layer for configuration, runtime data, and result storage.
7. Observability layer for metrics, logs, traces, dashboards, and analysis.

## Core Flow

1. A user or pipeline requests a performance test.
2. The Control Plane API validates the request.
3. Jenkins starts the execution pipeline.
4. Ansible prepares Kubernetes resources through Rancher.
5. Kubernetes schedules JMeter or other engine pods.
6. Redis supplies runtime data to load pods.
7. The system under test receives traffic.
8. Metrics and logs stream to Grafana, Kibana, or equivalent tooling.
9. Results are stored, analyzed, compared, and reported.

## Design Goal

The architecture should keep the proven LoadRunner Enterprise concepts of centralized control and distributed load generation while replacing fixed load generator infrastructure with Kubernetes-managed execution.
