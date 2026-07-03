from __future__ import annotations

import hashlib
import os
import secrets
import time
import jwt
from typing import Any


JWT_SECRET = os.environ.get("MARATHONRUNNER_JWT_SECRET", "marathonrunner-enterprise-secret-key-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = int(os.environ.get("MARATHONRUNNER_JWT_EXPIRY_HOURS", "24"))
DEFAULT_PASSWORD = os.environ.get("MARATHONRUNNER_DEFAULT_PASSWORD", "marathonrunner")


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return dk.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    computed, _ = hash_password(password, salt)
    return secrets.compare_digest(computed, password_hash)


def generate_token(user_id: int, username: str, role: str, display_name: str) -> str:
    payload = {
        "user_id": user_id,
        "username": username,
        "role": role,
        "display_name": display_name,
        "iat": int(time.time()),
        "exp": int(time.time()) + (JWT_EXPIRY_HOURS * 3600),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def extract_token_from_header(auth_header: str | None) -> str | None:
    if not auth_header:
        return None
    parts = auth_header.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def require_auth(handler_method):
    def wrapper(self):
        auth_header = self.headers.get("Authorization")
        token = extract_token_from_header(auth_header)
        if not token:
            from http import HTTPStatus
            self.send_json({"error": "Missing or invalid authorization header. Use: Authorization: Bearer <token>"}, HTTPStatus.UNAUTHORIZED)
            return
        payload = decode_token(token)
        if payload is None:
            from http import HTTPStatus
            self.send_json({"error": "Invalid or expired token"}, HTTPStatus.UNAUTHORIZED)
            return
        self.user = payload
        handler_method(self)
    return wrapper


def require_role(*roles):
    def decorator(handler_method):
        def wrapper(self):
            auth_header = self.headers.get("Authorization")
            token = extract_token_from_header(auth_header)
            if not token:
                from http import HTTPStatus
                self.send_json({"error": "Missing or invalid authorization header"}, HTTPStatus.UNAUTHORIZED)
                return
            payload = decode_token(token)
            if payload is None:
                from http import HTTPStatus
                self.send_json({"error": "Invalid or expired token"}, HTTPStatus.UNAUTHORIZED)
                return
            if payload.get("role") not in roles:
                from http import HTTPStatus
                self.send_json({"error": f"Insufficient permissions. Required roles: {', '.join(roles)}"}, HTTPStatus.FORBIDDEN)
                return
            self.user = payload
            handler_method(self)
        return wrapper
    return decorator
