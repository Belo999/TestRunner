# Execution Diagram

```text
Jenkins Pipeline
      |
      v
Ansible Infrastructure Check
      |
      v
Kubernetes Execution Jobs
      |
      +--> JMeter Pod 1
      +--> JMeter Pod 2
      +--> JMeter Pod N
      |
      v
System Under Test
```

Execution pods pull images from Harbor, load configuration from MarathonRunner, retrieve runtime data from Redis, and generate traffic against the system under test.
