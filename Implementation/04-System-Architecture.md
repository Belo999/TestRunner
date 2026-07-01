# System Architecture

MarathonRunner Enterprise is built as a distributed system of microservices and infrastructure components.

## Major Systems

- Portal UI for users.
- API Gateway for external traffic.
- Control Plane API for orchestration.
- Scheduler for test timing and reservation management.
- Controller for execution lifecycle management.
- Jenkins for pipeline orchestration.
- Ansible for infrastructure automation.
- Rancher for Kubernetes management.
- Kubernetes for test execution.
- Redis for runtime test data.
- Configuration database for platform metadata.
- Object storage for artifacts and result files.
- Monitoring and logging stack for observability.

## Control Plane Responsibilities

- Validate test requests.
- Resolve configuration.
- Enforce governance.
- Trigger orchestration.
- Track lifecycle state.
- Store result metadata.

## Execution Plane Responsibilities

- Pull engine images from Harbor.
- Launch distributed engine pods.
- Generate load against the system under test.
- Stream metrics and logs.
- Persist result artifacts.
