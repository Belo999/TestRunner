FROM docker:24-cli AS docker-cli

FROM python:3.12-slim

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir psycopg2-binary pyjwt

WORKDIR /app

COPY apps ./apps
COPY data ./data

EXPOSE 8080

CMD ["python", "-m", "apps.api.main"]
