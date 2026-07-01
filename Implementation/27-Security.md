# Security

Security is required because performance testing platforms can generate significant traffic and access sensitive environments.

## Controls

- OIDC or SAML authentication.
- RBAC authorization.
- Environment-level access policies.
- Approval workflow for sensitive tests.
- Kubernetes network policies.
- Least-privilege service accounts.
- Harbor image scanning.
- TLS for service communication.
- Secret management.
- Audit logging.

## Test Data Security

- Sensitive data should be masked.
- Runtime credentials should expire.
- Redis data should use TTLs.
- Access to test data pools should be governed by project and role.

## Better Feature

Add policy-as-code rules to validate test targets, maximum load, image provenance, and environment permissions before execution.
