# Test Scheduler

The Test Scheduler controls when tests can run.

## Capabilities

- Manual test starts.
- Scheduled test starts.
- Recurring schedules.
- Execution windows.
- Blackout windows.
- Approval-required runs.
- Capacity reservations.
- Queueing when capacity is unavailable.

## Governance

The scheduler should prevent uncontrolled execution against shared or sensitive environments. It should enforce maximum concurrency, maximum load, and environment-specific rules.

## Better Feature

Add cost-aware and capacity-aware scheduling so large tests run during approved windows and on the most appropriate cluster.
