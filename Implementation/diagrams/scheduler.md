# Scheduler Diagram

```text
Run Request
    |
    v
Scheduler
    |
    +--> Check RBAC
    +--> Check Approval Rules
    +--> Check Capacity
    +--> Check Execution Window
    |
    v
Queued / Approved / Rejected / Started
```

The scheduler protects shared environments by enforcing controlled execution windows and capacity reservations.
