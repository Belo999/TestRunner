# Deployment

MarathonRunner Enterprise should be deployed as containerized services on Kubernetes.

## Deployment Components

- MarathonRunner services.
- API Gateway or ingress.
- PostgreSQL or external managed database.
- Redis or external managed Redis.
- Object storage.
- Monitoring stack.
- Logging stack.
- Jenkins integration.
- Harbor image registry.

## Deployment Automation

Ansible should provision infrastructure and configure Rancher-managed Kubernetes clusters. Helm charts or Kustomize should deploy platform services.

## Environments

- Development.
- Integration.
- Test.
- Performance.
- Production-like.

## Release Strategy

Use versioned container images, environment-specific configuration, automated smoke tests, and rollback procedures.
