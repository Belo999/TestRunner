# Object Storage Diagram

```text
Execution Pods
      |
      v
Result Collector
      |
      v
Object Storage
      |
      +--> raw-results/
      +--> logs/
      +--> reports/
      +--> screenshots/
      +--> environment-snapshots/
```

Object storage holds large artifacts and links them back to test run metadata in the configuration database.
