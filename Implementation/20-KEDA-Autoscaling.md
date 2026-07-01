# KEDA Autoscaling

KEDA can scale execution components based on events and metrics.

## Scaling Signals

- Pending test runs.
- Queue depth.
- Desired virtual users.
- Target throughput.
- Redis queue size.
- Kafka topic lag.
- CPU or memory pressure.

## Use Cases

- Scale controller workers when many runs are queued.
- Scale result ingestion workers during large tests.
- Scale engine pods based on run demand.
- Scale notification workers during event bursts.

## Guardrails

- Maximum pod count.
- Maximum cluster capacity.
- Approved load profile limits.
- Environment-specific concurrency limits.
