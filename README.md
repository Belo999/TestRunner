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

- `marathonrunner-app`: local API and UI.
- `postgres`: placeholder configuration database.
- `redis`: runtime data cache.
- `minio`: object storage placeholder.

## Local API

- `GET /api/health`
- `GET /api/projects`
- `GET /api/runs`
- `POST /api/runs`
- `GET /api/roadmap`
- `GET /api/ai/recommendations`

## Scaffold Intent

This is the first implementation slice. It provides the local shell of the product so the full platform can be built incrementally:

1. Portal and API.
2. Project and test run management.
3. Roadmap and AI capability visibility.
4. Local persistence.
5. Docker-first development path.
