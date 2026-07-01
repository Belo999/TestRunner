# Database

The configuration database is the durable source of truth for MarathonRunner metadata.

## Recommended Database

PostgreSQL is recommended because it is reliable, mature, and well suited for relational platform metadata.

## Stored Data

- Projects.
- Applications.
- Environments.
- Users and role assignments.
- Test plans.
- Scenarios.
- Load profiles.
- Schedules.
- Engine definitions.
- Harbor image references.
- Run records.
- Result metadata.
- Audit records.

## Design Requirements

- Schema migrations shall be versioned.
- Backups shall be automated.
- Sensitive values shall not be stored as plain text.
- High-volume raw metrics should be stored outside the configuration database.
