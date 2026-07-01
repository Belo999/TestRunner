# Sequence Diagram

```text
User/Jenkins -> API Gateway: Request test run
API Gateway -> Control Plane: Validate request
Control Plane -> Scheduler: Check policy and capacity
Scheduler -> Jenkins: Start pipeline
Jenkins -> Ansible: Prepare infrastructure
Ansible -> Rancher/Kubernetes: Apply resources
Jenkins -> Kubernetes: Launch engine pods
Engine Pods -> Redis: Read runtime data
Engine Pods -> System Under Test: Generate load
Engine Pods -> Monitoring: Publish metrics/logs
Jenkins -> Object Storage: Store artifacts
Jenkins -> Control Plane: Publish run result
Control Plane -> Portal: Show report and status
```

This sequence shows the full governed execution path from request to result.
