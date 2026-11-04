"""身份证号字段加密（AES-256-GCM，数据库设计说明书 8.2）。

- 密钥由环境变量 `ID_CARD_KEY` 注入（base64url 编码的 32 字节），**不入库、不入代码库**；
- 存储格式：`v1:` + base64url(nonce(12B) + ciphertext + tag(16B))，前缀用于将来轮换算法；
- 仅用于生成初始密码等极少数场景，业务接口一律不返回明文，只返回脱敏值；
- 未配置密钥时 `encryption_available()` 为 False，调用加密会显式报错，避免"静默存明文"。
"""
from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

PREFIX = "v1"
_NONCE_LEN = 12


def _b64d(raw: str) -> bytes:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def _key() -> bytes | None:
    raw = (settings.ID_CARD_KEY or "").strip()
    if not raw:
        return None
    try:
        key = _b64d(raw)
    except Exception:  # noqa: BLE001 - 密钥配置非法时按不可用处理
        return None
    return key if len(key) == 32 else None


def encryption_available() -> bool:
    return _key() is not None


def encrypt_id_card(plain: str | None) -> str | None:
    """加密身份证号；空值原样返回 None。未配置密钥时抛 RuntimeError（不落明文）。"""
    if plain is None or plain == "":
        return None
    key = _key()
    if key is None:
        raise RuntimeError("ID_CARD_KEY 未配置或格式不正确，拒绝写入身份证号")
    nonce = os.urandom(_NONCE_LEN)
    ciphertext = AESGCM(key).encrypt(nonce, plain.encode("utf-8"), None)
    return f"{PREFIX}:" + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_id_card(token: str | None) -> str | None:
    """解密；密文被篡改（tag 校验失败）返回 None。"""
    if not token:
        return None
    if not token.startswith(f"{PREFIX}:"):
        return None
    key = _key()
    if key is None:
        return None
    try:
        blob = _b64d(token[len(PREFIX) + 1 :])
        nonce, ciphertext = blob[:_NONCE_LEN], blob[_NONCE_LEN:]
        return AESGCM(key).decrypt(nonce, ciphertext, None).decode("utf-8")
    except (InvalidTag, ValueError):
        return None


def mask_id_card(plain: str | None) -> str | None:
    """身份证号脱敏：保留前 6 与后 4，中间以 * 填充（18 位 → 110101********1234）。"""
    if not plain:
        return None
    if len(plain) <= 10:
        return plain[0] + "*" * (len(plain) - 1)
    return plain[:6] + "*" * (len(plain) - 10) + plain[-4:]


def masked_from_encrypted(token: str | None) -> str | None:
    """直接从密文得到脱敏值（管理员列表用，避免把明文带到上层）。"""
    return mask_id_card(decrypt_id_card(token))
