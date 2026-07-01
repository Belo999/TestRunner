# API Gateway

The API Gateway is the entry point for MarathonRunner Portal, Jenkins, automation clients, and external integrations.

## Responsibilities

- Route API traffic to backend services.
- Enforce authentication.
- Apply request rate limits.
- Terminate TLS where appropriate.
- Provide request tracing and correlation IDs.
- Normalize external API paths.

## Key Routes

- `/api/projects`
- `/api/tests`
- `/api/scenarios`
- `/api/runs`
- `/api/results`
- `/api/schedules`
- `/api/engines`
- `/api/admin`

## Enterprise Features

- API keys or service accounts for CI/CD systems.
- OAuth2/OIDC integration for users.
- Audit trail correlation for every request.
- Environment-specific access enforcement.
