# Microservices

MarathonRunner Enterprise should be decomposed into focused services.

## Recommended Services

- Portal UI Service.
- API Gateway.
- Identity Integration Service.
- Project Service.
- Test Catalog Service.
- Scenario Service.
- Scheduler Service.
- Controller Service.
- Execution Service.
- Engine Registry Service.
- Redis Test Data Service.
- Result Ingestion Service.
- Reporting Service.
- Notification Service.
- Audit Service.

## Service Principles

- Each service owns a clear business capability.
- Services expose stable APIs.
- Services publish domain events for lifecycle changes.
- Services are independently deployable.
- Shared database ownership should be avoided where practical.

## LoadRunner Enterprise Alignment

The microservice model maps traditional centralized test management into separate scalable domains: project management, scenario management, scheduling, load generation, monitoring, and reporting.
