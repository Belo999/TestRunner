# Messaging

Messaging enables asynchronous communication between platform services.

## Candidate Technologies

- Kafka.
- RabbitMQ.
- NATS.
- Redis Streams for smaller deployments.

## Event Types

- TestRunRequested.
- TestRunApproved.
- TestRunScheduled.
- TestRunStarted.
- TestRunScaled.
- TestRunCompleted.
- TestRunFailed.
- ResultIngested.
- ThresholdBreached.

## Benefits

- Decouples services.
- Improves resilience.
- Enables real-time notifications.
- Supports audit and event replay patterns.
