# Cluster Diagram

```text
Users / Jenkins
      |
      v
API Gateway
      |
      v
Control Plane Services
      |
      +--> PostgreSQL Configuration DB
      +--> Redis Runtime Data
      +--> Object Storage
      +--> Messaging
      |
      v
Rancher Managed Kubernetes Cluster
      |
      +--> MarathonRunner Platform Namespace
      +--> Execution Namespaces
      +--> JMeter / k6 / Gatling / Locust Pods
      +--> Monitoring And Logging Agents
```

The cluster model separates platform services from execution workloads so test engines can scale without interfering with the control plane.
