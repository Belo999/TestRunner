# Redis

Redis provides high-speed runtime data for active performance tests.

## Use Cases

- Runtime credential pools.
- Token pools.
- Shared counters.
- Distributed queues.
- Session values.
- Correlation data.
- Temporary run state.
- Duplicate data prevention.

## Design Rules

- Redis is not the permanent source of record.
- Durable configuration remains in PostgreSQL or equivalent storage.
- Redis keys should include run ID, project ID, and scenario ID.
- Data should expire automatically after test completion.

## Better Feature

MarathonRunner can provide a Test Data Pool service that preloads, partitions, and cleans Redis data for each test run automatically.
