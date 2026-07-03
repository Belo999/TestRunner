from __future__ import annotations

import os
import urllib.error
import urllib.request
from pathlib import Path


OBJECT_STORAGE_ENDPOINT = os.environ.get("OBJECT_STORAGE_ENDPOINT", "http://minio:9000").rstrip("/")
OBJECT_STORAGE_BUCKET = os.environ.get("OBJECT_STORAGE_BUCKET", "marathonrunner-artifacts")
OBJECT_STORAGE_ACCESS_KEY = os.environ.get("OBJECT_STORAGE_ACCESS_KEY", "marathonrunner")
OBJECT_STORAGE_SECRET_KEY = os.environ.get("OBJECT_STORAGE_SECRET_KEY", "marathonrunner123")
OBJECT_STORAGE_ENABLED = os.environ.get("MARATHONRUNNER_OBJECT_STORAGE_ENABLED", "1") == "1"


def _ensure_bucket() -> bool:
    if not OBJECT_STORAGE_ENABLED:
        return False
    url = f"{OBJECT_STORAGE_ENDPOINT}/{OBJECT_STORAGE_BUCKET}"
    request = urllib.request.Request(url, method="PUT")
    credentials = f"{OBJECT_STORAGE_ACCESS_KEY}:{OBJECT_STORAGE_SECRET_KEY}".encode("utf-8")
    import base64

    request.add_header("Authorization", "Basic " + base64.b64encode(credentials).decode("ascii"))
    try:
        with urllib.request.urlopen(request, timeout=5):
            return True
    except urllib.error.HTTPError as exc:
        return exc.code in {200, 409}
    except OSError:
        return False


def upload_artifact(local_path: str | Path, object_key: str) -> str | None:
    if not OBJECT_STORAGE_ENABLED:
        return None
    path = Path(local_path)
    if not path.is_file():
        return None
    if not _ensure_bucket():
        return None

    url = f"{OBJECT_STORAGE_ENDPOINT}/{OBJECT_STORAGE_BUCKET}/{object_key.lstrip('/')}"
    request = urllib.request.Request(url, data=path.read_bytes(), method="PUT")
    credentials = f"{OBJECT_STORAGE_ACCESS_KEY}:{OBJECT_STORAGE_SECRET_KEY}".encode("utf-8")
    import base64

    request.add_header("Authorization", "Basic " + base64.b64encode(credentials).decode("ascii"))
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=10):
            return f"s3://{OBJECT_STORAGE_BUCKET}/{object_key.lstrip('/')}"
    except (urllib.error.HTTPError, OSError):
        return None


def check_object_storage() -> bool:
    if not OBJECT_STORAGE_ENABLED:
        return False
    url = f"{OBJECT_STORAGE_ENDPOINT}/minio/health/live"
    try:
        with urllib.request.urlopen(url, timeout=2):
            return True
    except OSError:
        return False
