# Redis Diagram

```text
Test Data Service
      |
      v
Redis
      |
      +--> credentials:{runId}
      +--> tokens:{runId}
      +--> counters:{runId}
      +--> queues:{runId}
      +--> sessions:{runId}
      |
      v
JMeter / Engine Pods
```

Redis provides fast temporary runtime data and coordination for distributed load pods.
