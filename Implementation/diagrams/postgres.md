# PostgreSQL Diagram

```text
PostgreSQL Configuration DB
      |
      +--> projects
      +--> applications
      +--> environments
      +--> tests
      +--> scenarios
      +--> load_profiles
      +--> schedules
      +--> runs
      +--> results_metadata
      +--> audit_events
```

PostgreSQL stores durable platform configuration and metadata. Raw high-volume metrics and large artifacts should be stored outside PostgreSQL.
