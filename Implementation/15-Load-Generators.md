# Load Generators

Load generators are the execution units that produce traffic against the system under test.

## Initial Engine

JMeter is the initial primary engine. It runs in Kubernetes pods and can scale horizontally.

## LoadRunner Enterprise-Class Features

- Load generator grouping.
- Capacity reservation.
- Health checks.
- Geographic grouping.
- Environment restrictions.
- Execution logs.
- Real-time status.

## Kubernetes Implementation

- Load generators are pods or jobs.
- Images are pulled from Harbor.
- Runtime configuration is injected through ConfigMaps, secrets, and API-provided parameters.
- Pods are labeled for monitoring, ownership, and cleanup.

## Future Engines

- k6.
- Gatling.
- Locust.
- Playwright.
- Selenium.
- Custom protocol drivers.
