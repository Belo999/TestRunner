# CI/CD

Jenkins is the primary orchestration tool for CI/CD integration.

## Jenkins Responsibilities

- Trigger tests from deployment pipelines.
- Run pre-test checks.
- Invoke Ansible playbooks.
- Launch Kubernetes execution resources.
- Track run status.
- Collect artifacts.
- Publish results.
- Enforce quality gates.

## Pipeline Stages

1. Checkout test assets.
2. Resolve MarathonRunner configuration.
3. Validate target environment.
4. Prepare runtime data.
5. Deploy load pods.
6. Monitor execution.
7. Collect results.
8. Evaluate thresholds.
9. Publish reports.

## Better Feature

Add performance gates that can block releases when response time, error rate, throughput, or saturation thresholds fail.
