"""密码、令牌与验证码等安全原语。

- 密码：bcrypt（cost=12），符合《数据库设计说明书》8.1「bcrypt（cost ≥ 10）」
  同时兼容校验历史 PBKDF2 哈希，便于存量数据平滑迁移
- 访问令牌：JWT（HS256），2 小时有效，绑定会话 ID，可随会话撤销立即失效
- 刷新令牌：高熵随机串，仅存 SHA-256 摘要，写入 HttpOnly Cookie
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.config import settings

BCRYPT_ROUNDS = 12
PBKDF2_ITERATIONS = 260_000  # 仅用于校验历史数据
_ALPHABET = string.ascii_letters + string.digits


# ---------------------------------------------------------------- 密码
def hash_password(password: str) -> str:
    """生成 bcrypt 哈希（60 字符，适配 password_hash VARCHAR(100)）。"""
    raw = password.encode("utf-8")
    if len(raw) > 72:  # bcrypt 只取前 72 字节
        raw = raw[:72]
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def _verify_pbkdf2(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        expected = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations)
        )
        return hmac.compare_digest(expected.hex(), digest)
    except (ValueError, TypeError):
        return False


def verify_password(password: str, stored: str | None) -> bool:
    """校验密码；支持 bcrypt（$2a/$2b/$2y）与历史 PBKDF2 两种格式。"""
    if not stored:
        return False
    if stored.startswith(("$2a$", "$2b$", "$2y$")):
        raw = password.encode("utf-8")
        if len(raw) > 72:
            raw = raw[:72]
        try:
            return bcrypt.checkpw(raw, stored.encode("ascii"))
        except (ValueError, TypeError):
            return False
    if stored.startswith("pbkdf2_sha256$"):
        return _verify_pbkdf2(password, stored)
    return False


def password_needs_rehash(stored: str | None) -> bool:
    """历史 PBKDF2 哈希建议在下次登录时升级为 bcrypt。"""
    if not stored:
        return False
    return stored.startswith("pbkdf2_sha256$")


def password_strength_error(password: str, student_no: str | None = None, old_password: str | None = None) -> str | None:
    """校验密码强度，返回错误原因；None 表示通过（文档 3.4：8—20 位，含字母与数字）。"""
    if not (8 <= len(password) <= 20):
        return "密码长度须为 8—20 位"
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return "密码须同时包含字母与数字"
    if not password.isascii():
        return "密码仅支持 ASCII 字符"
    if student_no and password == student_no:
        return "新密码不得与学号相同"
    if old_password and password == old_password:
        return "新密码不得与原密码相同"
    return None


# ---------------------------------------------------------------- 随机串
def random_token(length: int = 48) -> str:
    return secrets.token_urlsafe(length)[:length]


def random_share_token(length: int = 12) -> str:
    """12 位随机字符，全局唯一，不含自增 ID（文档 4.5）。"""
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def random_checkin_code(length: int = 6) -> str:
    """6 位数字签到码。"""
    return "".join(secrets.choice(string.digits) for _ in range(length))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    """恒定时间比较，用于签到码与令牌校验（文档 7.3.2）。"""
    return hmac.compare_digest((a or "").encode("utf-8"), (b or "").encode("utf-8"))


# ---------------------------------------------------------------- JWT
def create_access_token(
    *,
    subject: int,
    account_type: str,
    session_id: str,
    role: str | None = None,
    extra: dict[str, Any] | None = None,
    ttl: int | None = None,
) -> tuple[str, int]:
    """返回 (token, expires_in 秒)。"""
    ttl = ttl or settings.ACCESS_TOKEN_TTL
    issued = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "typ": account_type,          # student / admin
        "sid": session_id,
        "scope": "access",
        "jti": uuid.uuid4().hex,
        "iat": int(issued.timestamp()),
        "exp": int((issued + timedelta(seconds=ttl)).timestamp()),
    }
    if role:
        payload["role"] = role
    if extra:
        payload.update(extra)
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, ttl


def decode_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


def create_download_token(*, subject: int, account_type: str, path: str, ttl: int | None = None) -> str:
    """带时效的下载令牌，绑定账号与路径（文档 2.8）。"""
    ttl = ttl or settings.DOWNLOAD_TOKEN_TTL
    issued = datetime.now(timezone.utc)
    payload = {
        "sub": str(subject),
        "typ": account_type,
        "scope": "download",
        "path": path,
        "iat": int(issued.timestamp()),
        "exp": int((issued + timedelta(seconds=ttl)).timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_download_token(token: str) -> dict[str, Any] | None:
    payload = decode_token(token)
    if payload and payload.get("scope") == "download":
        return payload
    return None


# ---------------------------------------------------------------- 验证码
def generate_captcha_code(length: int = 4) -> str:
    alphabet = "".join(c for c in string.ascii_uppercase + string.digits if c not in "0O1IL")
    return "".join(secrets.choice(alphabet) for _ in range(length))


def render_captcha_svg(code: str, width: int = 120, height: int = 40) -> str:
    """生成 SVG 验证码图片（避免引入 Pillow 依赖）。"""
    import random

    rnd = random.Random()
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f2f4f8"/>',
    ]
    for _ in range(6):
        x1, y1 = rnd.randint(0, width), rnd.randint(0, height)
        x2, y2 = rnd.randint(0, width), rnd.randint(0, height)
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#c3cad6" stroke-width="1"/>'
        )
    step = width / (len(code) + 1)
    for idx, char in enumerate(code):
        x = step * (idx + 1)
        y = height / 2 + rnd.randint(-4, 6)
        rotate = rnd.randint(-18, 18)
        parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-family="Verdana,DejaVu Sans,sans-serif" '
            f'font-size="24" font-weight="700" fill="#2f3b52" text-anchor="middle" '
            f'transform="rotate({rotate} {x:.1f} {y:.1f})">{html.escape(char)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def svg_to_data_url(svg: str) -> str:
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def mask_mobile(mobile: str | None) -> str | None:
    """手机号脱敏：138****1234（文档 6.4）。"""
    if not mobile or len(mobile) < 7:
        return mobile
    return f"{mobile[:3]}****{mobile[-4:]}"


def mask_student_no(student_no: str | None) -> str | None:
    """学号脱敏，用于签到看板。"""
    if not student_no or len(student_no) <= 4:
        return student_no
    return f"{student_no[:3]}****{student_no[-3:]}"
