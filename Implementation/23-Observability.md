# Observability

Observability combines metrics, logs, traces, events, and dashboards into a unified operational view.

## Required Signals

- Metrics for performance and health.
- Logs for troubleshooting.
- Traces for request flow through platform services.
- Events for test lifecycle changes.
- Alerts for failed runs and threshold breaches.

## OpenTelemetry

OpenTelemetry should be used for standard traces and metrics where possible. It improves portability across Grafana, Elastic, OpenSearch, cloud monitoring tools, and enterprise observability platforms.

## Better Feature

MarathonRunner should correlate system-under-test telemetry with load generator metrics to identify whether failures originate from the application, infrastructure, network, data layer, or test engine.
