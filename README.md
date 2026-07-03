# MarathonRunner Enterprise

MarathonRunner Enterprise is a Kubernetes-native performance testing platform scaffold inspired by LoadRunner Enterprise and extended with AI-assisted analysis, multi-engine execution, governance, and cloud-native orchestration.

This repository currently contains:

- Architecture and implementation documentation in `Implementation/`.
- A local runnable scaffold in `apps/api` and `apps/web`.
- Docker Compose wiring for the local app and platform dependencies.

## Run Locally Without Installing Node

The local scaffold uses Python standard library modules and serves the API plus UI from one process.

```bash
python3 apps/api/main.py
```

Then open:

```text
http://localhost:8080
```

## Run With Docker Compose

```bash
docker compose up --build
```

Then open:

```text
http://localhost:8080
```

Docker Compose starts:

- `marathonrunner-app`: local API and UI (PostgreSQL backend, Redis runtime cache, MinIO artifact storage).
- `marathonrunner-worker`: background worker that queues runs, launches engine Docker containers, and collects results.
- `postgres`: primary configuration database.
- `redis`: runtime run-state cache for live monitoring.
- `minio`: object storage for result artifacts.

### Engine Execution

The worker launches real test engines via Docker containers:

- **JMeter** (`justb4/jmeter:latest`): Mounts test plan from `data/scripts/jmeter/`, writes JTL results to `data/artifacts/run-{id}/`.
- **k6** (`grafana/k6:latest`): Mounts test script from `data/scripts/k6/`, writes JSON summary to `data/artifacts/run-{id}/`.

When a run is created, the worker:
1. Queues ready/approved runs
2. Starts queued runs and launches the appropriate Docker container
3. Monitors container execution via `docker inspect`
4. On completion, parses engine output and stores real metrics
5. Supports cancellation via `docker kill`

## Local API

### Health and Dashboard

- `GET /api/health` - service health and dependency status
- `GET /api/dashboard` - counts, run status breakdown, latest runs

### Core Entities (full CRUD)

- `GET /api/projects` - list projects
- `POST /api/projects` - create project
- `PUT /api/projects/<id>` - update project
- `DELETE /api/projects/<id>` - delete project
- `GET /api/environments` - list environments
- `POST /api/environments` - create environment
- `PUT /api/environments/<id>` - update environment
- `DELETE /api/environments/<id>` - delete environment
- `GET /api/scenarios` - list scenarios
- `POST /api/scenarios` - create scenario
- `PUT /api/scenarios/<id>` - update scenario
- `DELETE /api/scenarios/<id>` - delete scenario
- `GET /api/pools` - load generator pools
- `POST /api/pools` - create pool
- `PUT /api/pools/<id>` - update pool
- `DELETE /api/pools/<id>` - delete pool
- `GET /api/policies` - governance policies
- `POST /api/policies` - create policy
- `PUT /api/policies/<id>` - update policy
- `DELETE /api/policies/<id>` - delete policy

### Test Run Lifecycle

- `GET /api/runs` - list runs with joined metadata
- `GET /api/runs/<id>` - run detail with results
- `POST /api/runs` - create run (triggers policy checks)
- `POST /api/runs/<id>/approve` - approve a run
- `POST /api/runs/<id>/start` - start execution
- `POST /api/runs/<id>/complete` - complete with simulated results
- `POST /api/runs/<id>/cancel` - cancel a run

### Results, AI, and Audit

- `GET /api/results` - run result metrics
- `GET /api/ai/recommendations` - AI insights and recommendations
- `GET /api/audit` - audit event trail
- `GET /api/notifications` - notification queue
- `GET /api/roadmap` - feature roadmap

### Worker

- `POST /api/worker/tick` - process worker queue (auto-triggered by worker service)

## Scaffold Intent

This is the third implementation slice. It provides the local shell of the product so the full platform can be built incrementally:

1. Portal and API with entity management.
2. Project, environment, scenario, and pool CRUD.
3. Test run lifecycle with policy guardrails.
4. Roadmap and AI capability visibility.
5. Local persistence with SQLite.
6. Docker-first development path with worker service.
7. Audit trail and notification queue.
8. Real engine execution via Docker containers (JMeter, k6).
9. Engine abstraction layer for pluggable adapters.
10. Result parsing from JTL (JMeter) and JSON (k6).
11. Execution logs viewing in the UI.
