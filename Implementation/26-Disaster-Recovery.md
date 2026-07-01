# Disaster Recovery

Disaster recovery protects MarathonRunner platform continuity and historical test data.

## Recovery Targets

- Define RPO for configuration and results.
- Define RTO for platform services.
- Define retention policies for artifacts and audits.

## Backup Scope

- Configuration database.
- Object storage.
- Harbor images.
- Kubernetes manifests.
- Secrets through approved secret management processes.
- Dashboard definitions.

## Recovery Procedures

- Restore database.
- Restore object storage.
- Reinstall platform services.
- Reconnect Jenkins.
- Reconnect Rancher-managed clusters.
- Validate sample test execution.
