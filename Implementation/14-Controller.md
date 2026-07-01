# Controller

The Controller manages the lifecycle of test runs.

## Responsibilities

- Resolve run configuration.
- Create Kubernetes execution resources.
- Track pod status.
- Coordinate with Jenkins.
- Update run state.
- Handle cancellation.
- Trigger cleanup.
- Publish lifecycle events.

## LoadRunner Enterprise Alignment

The Controller acts like the modern equivalent of a central performance test controller. Instead of managing fixed load generator machines, it manages Kubernetes workloads and engine pods.

## Better Features

- Auto-healing failed load pods when safe.
- Dynamic scaling during test ramp-up.
- Policy-based stopping when SLA thresholds are severely breached.
- Automatic cleanup after failed or abandoned runs.
