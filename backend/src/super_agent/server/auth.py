"""认证模块——独立可替换（v2 可换成内网 GitLab OAuth）。

职责：用户存储（scrypt 口令哈希）、不透明 token 签发与校验。
存储为 SQLite（users + auth_tokens 两张表）。
"""

import hashlib
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

TOKEN_TTL_SECONDS = 7 * 24 * 3600
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


@dataclass
class User:
    id: int
    username: str


class Credentials(BaseModel):
    username: str
    password: str


class AuthService:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " username TEXT UNIQUE NOT NULL,"
            " password_hash TEXT NOT NULL,"
            " created_at REAL NOT NULL)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS auth_tokens ("
            " token TEXT PRIMARY KEY,"
            " user_id INTEGER NOT NULL REFERENCES users(id),"
            " expires_at REAL NOT NULL)"
        )
        self._db.commit()

    def register(self, username: str, password: str) -> User:
        try:
            cur = self._db.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, _hash_password(password), time.time()),
            )
            self._db.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("username already taken") from exc
        return User(id=cur.lastrowid, username=username)

    def verify_login(self, username: str, password: str) -> User | None:
        row = self._db.execute(
            "SELECT id, password_hash FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row is None or not _verify_password(password, row[1]):
            return None
        return User(id=row[0], username=username)

    def issue_token(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        self._db.execute(
            "INSERT INTO auth_tokens (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, time.time() + TOKEN_TTL_SECONDS),
        )
        self._db.commit()
        return token

    def user_for_token(self, token: str) -> User | None:
        row = self._db.execute(
            "SELECT u.id, u.username FROM auth_tokens t"
            " JOIN users u ON u.id = t.user_id"
            " WHERE t.token = ? AND t.expires_at > ?",
            (token, time.time()),
        ).fetchone()
        if row is None:
            return None
        return User(id=row[0], username=row[1])


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return f"{salt.hex()}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split("$", 1)
    digest = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt_hex), n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    return secrets.compare_digest(digest.hex(), digest_hex)


def auth_router(service: AuthService) -> APIRouter:
    router = APIRouter()

    @router.post("/api/auth/register", status_code=status.HTTP_201_CREATED)
    def register(body: Credentials) -> dict:
        try:
            service.register(body.username, body.password)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="username already taken"
            )
        return {"username": body.username}

    @router.post("/api/auth/login")
    def login(body: Credentials) -> dict:
        user = service.verify_login(body.username, body.password)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials"
            )
        return {"token": service.issue_token(user.id), "username": user.username}

    @router.get("/api/me")
    def me(request: Request) -> dict:
        user = user_from_request(request, service)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="missing or invalid token"
            )
        return {"username": user.username}

    return router


def user_from_request(request: Request, service: AuthService) -> User | None:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return service.user_for_token(header.removeprefix("Bearer ").strip())
    return None
