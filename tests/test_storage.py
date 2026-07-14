from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from apps.api.storage import (
    OBJECT_STORAGE_BUCKET,
    OBJECT_STORAGE_ENDPOINT,
    _ensure_bucket,
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


class TestEnsureBucket:
    @patch("apps.api.storage.OBJECT_STORAGE_ENABLED", False)
    def test_disabled_returns_false(self):
        assert _ensure_bucket() is False

    @patch("apps.api.storage.urllib.request.urlopen")
    def test_success(self, mock_urlopen):
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
        assert _ensure_bucket() is True

    @patch("apps.api.storage.urllib.request.urlopen")
    def test_already_exists_returns_true(self, mock_urlopen):
        import urllib.error
        error = urllib.error.HTTPError(url="", code=409, msg="Conflict", hdrs=None, fp=None)
        mock_urlopen.side_effect = error
        assert _ensure_bucket() is True

    @patch("apps.api.storage.urllib.request.urlopen")
    def test_other_http_error_returns_false(self, mock_urlopen):
        import urllib.error
        error = urllib.error.HTTPError(url="", code=500, msg="Error", hdrs=None, fp=None)
        mock_urlopen.side_effect = error
        assert _ensure_bucket() is False

    @patch("apps.api.storage.urllib.request.urlopen", side_effect=OSError("timeout"))
    def test_oserror_returns_false(self, mock_urlopen):
        assert _ensure_bucket() is False


class TestUploadArtifactErrors:
    @patch("apps.api.storage._ensure_bucket", return_value=True)
    @patch("apps.api.storage.urllib.request.urlopen", side_effect=OSError("timeout"))
    def test_upload_oserror_returns_none(self, mock_urlopen, mock_bucket, tmp_path):
        test_file = tmp_path / "data.txt"
        test_file.write_text("content")
        result = upload_artifact(str(test_file), "key")
        assert result is None

    @patch("apps.api.storage._ensure_bucket", return_value=True)
    @patch("apps.api.storage.urllib.request.urlopen")
    def test_upload_http_error_returns_none(self, mock_urlopen, mock_bucket, tmp_path):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(url="", code=500, msg="Error", hdrs=None, fp=None)
        test_file = tmp_path / "data.txt"
        test_file.write_bytes(b"content")
        result = upload_artifact(str(test_file), "key")
        assert result is None
