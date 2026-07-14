from __future__ import annotations

import hashlib
import time
from http import HTTPStatus
from unittest.mock import MagicMock

import jwt
import pytest

from apps.api.auth import (
    JWT_ALGORITHM,
    JWT_SECRET,
    decode_token,
    extract_token_from_header,
    generate_token,
    hash_password,
    require_auth,
    require_role,
    verify_password,
)


def _decode_token(token) -> str:
    """Ensure token is a string (some PyJWT versions return bytes)."""
    return token.decode("utf-8") if isinstance(token, bytes) else token


# ── Password Hashing ──────────────────────────────────────────────────────────

class TestPasswordHashing:
    def test_hash_password_returns_hex_and_salt(self):
        hashed, salt = hash_password("mypassword")
        assert isinstance(hashed, str)
        assert isinstance(salt, str)
        assert len(hashed) == 64  # SHA-256 hex digest
        assert len(salt) == 32  # 16 bytes hex

    def test_same_password_different_salts(self):
        h1, s1 = hash_password("test")
        h2, s2 = hash_password("test")
        assert s1 != s2
        assert h1 != h2

    def test_same_password_same_salt(self):
        h1, _ = hash_password("test", salt="fixedsalt")
        h2, _ = hash_password("test", salt="fixedsalt")
        assert h1 == h2

    def test_different_passwords_different_hashes(self):
        h1, _ = hash_password("password1", salt="salt")
        h2, _ = hash_password("password2", salt="salt")
        assert h1 != h2

    def test_verify_password_correct(self):
        hashed, salt = hash_password("secret")
        assert verify_password("secret", hashed, salt) is True

    def test_verify_password_incorrect(self):
        hashed, salt = hash_password("secret")
        assert verify_password("wrong", hashed, salt) is False

    def test_verify_with_known_vector(self):
        salt = "aabbccdd"
        dk = hashlib.pbkdf2_hmac("sha256", b"test", salt.encode(), 260_000)
        expected = dk.hex()
        assert verify_password("test", expected, salt) is True


# ── JWT ───────────────────────────────────────────────────────────────────────

class TestJWT:
    def test_generate_and_decode_token(self):
        token = generate_token(1, "admin", "admin", "Admin User")
        payload = decode_token(_decode_token(token))
        assert payload is not None
        assert payload["user_id"] == 1
        assert payload["username"] == "admin"
        assert payload["role"] == "admin"
        assert payload["display_name"] == "Admin User"
        assert "iat" in payload
        assert "exp" in payload

    def test_decode_valid_token(self):
        token = generate_token(42, "engineer", "engineer", "Test User")
        payload = decode_token(_decode_token(token))
        assert payload["user_id"] == 42

    def test_decode_expired_token(self):
        payload = {
            "user_id": 1, "username": "admin", "role": "admin",
            "display_name": "Admin",
            "iat": int(time.time()) - 7200,
            "exp": int(time.time()) - 3600,
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        assert decode_token(_decode_token(token)) is None

    def test_decode_invalid_token(self):
        assert decode_token("not.a.valid.token") is None

    def test_decode_tampered_token(self):
        token = _decode_token(generate_token(1, "admin", "admin", "Admin"))
        tampered = token[:-5] + "XXXXX"
        assert decode_token(tampered) is None

    def test_decode_wrong_secret(self):
        payload = {
            "user_id": 1, "username": "admin", "role": "admin",
            "display_name": "Admin",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        }
        token = jwt.encode(payload, "wrong-secret", algorithm=JWT_ALGORITHM)
        assert decode_token(_decode_token(token)) is None


# ── Token Extraction ──────────────────────────────────────────────────────────

class TestExtractToken:
    def test_valid_bearer(self):
        assert extract_token_from_header("Bearer abc123") == "abc123"

    def test_case_insensitive(self):
        assert extract_token_from_header("bearer abc123") == "abc123"
        assert extract_token_from_header("BEARER abc123") == "abc123"

    def test_none_header(self):
        assert extract_token_from_header(None) is None

    def test_empty_header(self):
        assert extract_token_from_header("") is None

    def test_no_bearer_prefix(self):
        assert extract_token_from_header("Token abc123") is None

    def test_missing_token(self):
        assert extract_token_from_header("Bearer") is None

    def test_extra_parts(self):
        assert extract_token_from_header("Bearer token extra") is None

    def test_whitespace_around_token(self):
        result = extract_token_from_header("Bearer   abc123")
        assert result == "abc123"


# ── Auth Decorators ───────────────────────────────────────────────────────────

def _make_handler(auth_header=None):
    """Create a mock handler that mimics BaseHTTPRequestHandler."""
    handler = MagicMock()
    handler.headers = {}
    if auth_header:
        handler.headers["Authorization"] = auth_header
    handler.send_json = MagicMock()
    return handler


def _valid_auth_header(role="admin"):
    token = _decode_token(generate_token(1, "admin", role, "Admin"))
    return f"Bearer {token}"


class TestRequireAuth:
    def test_valid_token_calls_handler(self):
        handler = _make_handler(_valid_auth_header())
        called = []

        @require_auth
        def my_handler(self):
            called.append(True)

        my_handler(handler)
        assert len(called) == 1
        handler.send_json.assert_not_called()

    def test_missing_header_returns_401(self):
        handler = _make_handler(None)

        @require_auth
        def my_handler(self):
            pass

        my_handler(handler)
        args = handler.send_json.call_args[0]
        assert args[1] == HTTPStatus.UNAUTHORIZED

    def test_invalid_token_returns_401(self):
        handler = _make_handler("Bearer invalid.token.here")

        @require_auth
        def my_handler(self):
            pass

        my_handler(handler)
        args = handler.send_json.call_args[0]
        assert args[1] == HTTPStatus.UNAUTHORIZED

    def test_expired_token_returns_401(self):
        payload = {
            "user_id": 1, "username": "admin", "role": "admin",
            "display_name": "Admin",
            "iat": int(time.time()) - 7200,
            "exp": int(time.time()) - 3600,
        }
        token = _decode_token(jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM))
        handler = _make_handler(f"Bearer {token}")

        @require_auth
        def my_handler(self):
            pass

        my_handler(handler)
        args = handler.send_json.call_args[0]
        assert args[1] == HTTPStatus.UNAUTHORIZED

    def test_sets_user_on_handler(self):
        handler = _make_handler(_valid_auth_header())

        @require_auth
        def my_handler(self):
            pass

        my_handler(handler)
        assert handler.user["user_id"] == 1


class TestRequireRole:
    def test_valid_role_calls_handler(self):
        handler = _make_handler(_valid_auth_header("admin"))
        called = []

        @require_role("admin")
        def my_handler(self):
            called.append(True)

        my_handler(handler)
        assert len(called) == 1
        handler.send_json.assert_not_called()

    def test_multiple_valid_roles(self):
        handler = _make_handler(_valid_auth_header("performance_lead"))
        called = []

        @require_role("admin", "performance_lead")
        def my_handler(self):
            called.append(True)

        my_handler(handler)
        assert len(called) == 1

    def test_wrong_role_returns_403(self):
        handler = _make_handler(_valid_auth_header("viewer"))

        @require_role("admin")
        def my_handler(self):
            pass

        my_handler(handler)
        args = handler.send_json.call_args[0]
        assert args[1] == HTTPStatus.FORBIDDEN

    def test_missing_header_returns_401(self):
        handler = _make_handler(None)

        @require_role("admin")
        def my_handler(self):
            pass

        my_handler(handler)
        args = handler.send_json.call_args[0]
        assert args[1] == HTTPStatus.UNAUTHORIZED

    def test_invalid_token_returns_401(self):
        handler = _make_handler("Bearer garbage")

        @require_role("admin")
        def my_handler(self):
            pass

        my_handler(handler)
        args = handler.send_json.call_args[0]
        assert args[1] == HTTPStatus.UNAUTHORIZED

    def test_sets_user_on_success(self):
        handler = _make_handler(_valid_auth_header("admin"))

        @require_role("admin")
        def my_handler(self):
            pass

        my_handler(handler)
        assert handler.user["role"] == "admin"
