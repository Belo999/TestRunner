# Controller Diagram

```text
Control Plane API
      |
      v
Controller
      |
      +--> Resolve Test Configuration
      +--> Prepare Runtime Data
      +--> Create Kubernetes Jobs
      +--> Track Pod Status
      +--> Publish Run Events
      +--> Trigger Cleanup
```

The controller is the MarathonRunner equivalent of a performance test controller, implemented for Kubernetes workloads.
