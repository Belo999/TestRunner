from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from apps.api.redis_cache import (
    ACTIVE_RUNS_KEY,
    RUN_KEY_PREFIX,
    clear_run_state,
    get_run_state,
    ping_redis,
    set_run_state,
    track_active_run,
    untrack_active_run,
)


class TestRedisCache:
    @patch("apps.api.redis_cache.REDIS_ENABLED", False)
    def test_disabled_returns_none(self):
        assert set_run_state(1, {"status": "running"}) is False
        assert get_run_state(1) is None
        assert ping_redis() is False

    @patch("apps.api.redis_cache._send_command")
    def test_set_run_state_success(self, mock_cmd):
        mock_cmd.return_value = "+OK\r\n"
        result = set_run_state(42, {"status": "running", "vusers": 100}, ttl_seconds=1800)
        assert result is True
        mock_cmd.assert_called_once()
        args = mock_cmd.call_args[0]
        assert args[0] == "SET"
        assert args[1] == f"{RUN_KEY_PREFIX}42"
        payload = json.loads(args[2])
        assert payload["status"] == "running"
        assert args[3] == "EX"
        assert args[4] == "1800"

    @patch("apps.api.redis_cache._send_command")
    def test_set_run_state_failure(self, mock_cmd):
        mock_cmd.return_value = "-ERR some error\r\n"
        result = set_run_state(1, {"status": "running"})
        assert result is False

    @patch("apps.api.redis_cache._send_command")
    def test_get_run_state_success(self, mock_cmd):
        data = json.dumps({"status": "completed"})
        mock_cmd.return_value = f"${len(data)}\r\n{data}\r\n"
        result = get_run_state(1)
        assert result == {"status": "completed"}

    @patch("apps.api.redis_cache._send_command")
    def test_get_run_state_nil(self, mock_cmd):
        mock_cmd.return_value = "$-1\r\n"
        result = get_run_state(1)
        assert result is None

    @patch("apps.api.redis_cache._send_command")
    def test_get_run_state_not_found(self, mock_cmd):
        mock_cmd.return_value = "$-1\r\nnil\r\n"
        result = get_run_state(1)
        assert result is None

    @patch("apps.api.redis_cache._send_command")
    def test_clear_run_state(self, mock_cmd):
        mock_cmd.return_value = ":1\r\n"
        clear_run_state(1)
        mock_cmd.assert_called_once_with("DEL", f"{RUN_KEY_PREFIX}1")

    @patch("apps.api.redis_cache._send_command")
    def test_track_active_run(self, mock_cmd):
        mock_cmd.return_value = ":1\r\n"
        track_active_run(42)
        mock_cmd.assert_called_once_with("SADD", ACTIVE_RUNS_KEY, "42")

    @patch("apps.api.redis_cache._send_command")
    def test_untrack_active_run(self, mock_cmd):
        mock_cmd.return_value = ":1\r\n"
        untrack_active_run(42)
        mock_cmd.assert_called_once_with("SREM", ACTIVE_RUNS_KEY, "42")

    @patch("apps.api.redis_cache._send_command")
    def test_ping_success(self, mock_cmd):
        mock_cmd.return_value = "+PONG\r\n"
        assert ping_redis() is True

    @patch("apps.api.redis_cache._send_command")
    def test_ping_failure(self, mock_cmd):
        mock_cmd.return_value = None
        assert ping_redis() is False
