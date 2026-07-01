# Authentication

MarathonRunner Enterprise should integrate with enterprise identity providers.

## Supported Authentication Methods

- OIDC.
- OAuth2.
- SAML where required.
- Service accounts for Jenkins and automation systems.
- Short-lived tokens for internal service communication.

## Identity Providers

The platform should support providers such as Keycloak, Azure AD, Okta, Ping Identity, or enterprise LDAP-backed identity systems.

## Requirements

- All user access shall be authenticated.
- All API access shall be authenticated.
- Token expiry and refresh shall be enforced.
- Service-to-service authentication shall use trusted internal identities.
- User identity shall be included in audit logs.
