# Autoscaling Diagram

```text
Pending Runs / Queue Depth / Target Load
                |
                v
              KEDA
                |
                v
Kubernetes Horizontal Scaling
                |
                +--> Controller Workers
                +--> Result Ingestion Workers
                +--> Engine Pods
```

KEDA allows MarathonRunner to scale based on workload demand while still respecting governance limits.
