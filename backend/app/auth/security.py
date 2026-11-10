"""密码、登录凭证、密钥的生成与校验。"""

import hashlib
import secrets
import string
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import get_settings

_hasher = PasswordHasher()
_JWT_ALGORITHM = "HS256"
API_KEY_PREFIX = "kb-"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def generate_password(length: int = 12) -> str:
    """生成初始密码：字母和数字，去掉容易看错的字符。"""
    alphabet = "".join(c for c in string.ascii_letters + string.digits if c not in "0OoIl1")
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.isdigit() for c in password) and any(c.isalpha() for c in password):
            return password


@lru_cache
def _secret_key() -> str:
    return get_settings().resolve_secret_key()


def next_cutoff(previous: datetime | None) -> datetime:
    """新的凭证分界点：此时间之前签发的登录凭证一律失效。

    取下一个整秒，并且严格晚于上一个分界点。这样无论此刻之前签发过什么凭证
    （包括上一次改密时签发在旧分界点上的那个），都落在新分界点之前。
    """
    cutoff = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=1)
    if previous is not None and cutoff <= previous:
        cutoff = previous + timedelta(seconds=1)
    return cutoff


def create_token(account_id: str, valid_after: datetime | None = None) -> tuple[str, datetime]:
    """签发登录凭证，返回凭证和过期时间。

    valid_after 是账号的凭证分界点；签发时间不早于它，新凭证才不会被分界点判为失效。
    """
    now = datetime.now(UTC)
    issued_at = max(now, valid_after) if valid_after else now
    expires_at = now + timedelta(hours=get_settings().TOKEN_EXPIRE_HOURS)
    payload = {"sub": account_id, "iat": int(issued_at.timestamp()), "exp": int(expires_at.timestamp())}
    return jwt.encode(payload, _secret_key(), algorithm=_JWT_ALGORITHM), expires_at


def decode_token(token: str) -> dict | None:
    """校验登录凭证，无效或过期时返回 None。"""
    try:
        return jwt.decode(
            token,
            _secret_key(),
            algorithms=[_JWT_ALGORITHM],
            options={"require": ["sub", "iat", "exp"]},
            leeway=5,  # 签发时间可能被推到下一个整秒，留出余量
        )
    except jwt.PyJWTError:
        return None


def generate_api_key() -> tuple[str, str, str]:
    """生成密钥，返回（明文，前缀，哈希）。明文只在创建时返回一次。"""
    plain = API_KEY_PREFIX + secrets.token_urlsafe(30)
    return plain, plain[:12], hash_api_key(plain)


def hash_api_key(plain: str) -> str:
    # 密钥本身是高强度随机串，用 SHA-256 即可，不需要慢哈希
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()
