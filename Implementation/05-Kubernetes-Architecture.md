# Kubernetes Architecture

Kubernetes provides the elastic execution layer for MarathonRunner Enterprise.

## Namespace Model

- `marathonrunner-system`: Platform services.
- `marathonrunner-execution`: Shared execution workloads.
- `marathonrunner-team-*`: Optional team-specific execution namespaces.
- `marathonrunner-observability`: Monitoring agents and collectors.

## Workload Types

- Deployments for long-running platform services.
- Jobs for finite test execution workloads.
- StatefulSets for Redis or stateful supporting components when self-hosted.
- CronJobs for scheduled maintenance.
- Custom resources for test runs when using the Kubernetes Operator.

## Required Kubernetes Controls

- Resource requests and limits.
- Network policies.
- Pod security standards.
- Service accounts and RBAC.
- Image pull secrets for Harbor.
- ConfigMaps for non-secret runtime configuration.
- Secrets for credentials and tokens.

## Load Generation Model

JMeter pods are launched as distributed load injectors. Each pod receives test configuration, Redis details, target endpoints, and run metadata. Pods are labeled by project, run ID, engine, scenario, and environment for monitoring and cleanup.
