# Logging

Logging captures platform, execution, and engine behavior for troubleshooting and audit.

## Log Sources

- Portal UI.
- API Gateway.
- Control Plane API.
- Scheduler.
- Controller.
- Jenkins jobs.
- Ansible runs.
- JMeter pods.
- Kubernetes events.
- Redis.

## Requirements

- Logs shall include correlation IDs.
- Logs shall include run IDs where applicable.
- Logs shall be searchable by project, application, run, environment, and engine.
- Sensitive values shall be masked.

## Tools

Kibana with Elasticsearch or OpenSearch is recommended for log search and investigation.
