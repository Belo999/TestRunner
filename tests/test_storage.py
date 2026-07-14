from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from apps.api.storage import (
    OBJECT_STORAGE_BUCKET,
    OBJECT_STORAGE_ENDPOINT,
    check_object_storage,
    upload_artifact,
)


class TestUploadArtifact:
    @patch("apps.api.storage._ensure_bucket", return_value=True)
    @patch("apps.api.storage.urllib.request.urlopen")
    def test_upload_success(self, mock_urlopen, mock_bucket, tmp_path: Path):
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        test_file = tmp_path / "result.txt"
        test_file.write_text("test data")

        result = upload_artifact(str(test_file), "runs/1/result.txt")
        assert result is not None
        assert result.startswith("s3://")
        assert "runs/1/result.txt" in result

    @patch("apps.api.storage._ensure_bucket", return_value=False)
    def test_upload_bucket_fails(self, mock_bucket, tmp_path: Path):
        test_file = tmp_path / "result.txt"
        test_file.write_text("test data")

        result = upload_artifact(str(test_file), "key")
        assert result is None

    def test_upload_nonexistent_file(self, tmp_path: Path):
        result = upload_artifact(str(tmp_path / "nonexistent.txt"), "key")
        assert result is None

    @patch("apps.api.storage.OBJECT_STORAGE_ENABLED", False)
    def test_upload_disabled(self, tmp_path: Path):
        test_file = tmp_path / "result.txt"
        test_file.write_text("test")
        result = upload_artifact(str(test_file), "key")
        assert result is None


class TestCheckObjectStorage:
    @patch("apps.api.storage.OBJECT_STORAGE_ENABLED", False)
    def test_disabled_returns_false(self):
        assert check_object_storage() is False

    @patch("apps.api.storage.urllib.request.urlopen")
    def test_healthy(self, mock_urlopen):
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
        assert check_object_storage() is True

    @patch("apps.api.storage.urllib.request.urlopen", side_effect=OSError("connection refused"))
    def test_unhealthy(self, mock_urlopen):
        assert check_object_storage() is False
