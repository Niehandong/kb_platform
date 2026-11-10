"""接口的身份校验。

管理接口（/api/v1）用登录凭证，召回接口（/open/v1）用密钥。两者都放在请求头：
    Authorization: Bearer <登录凭证或密钥>
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import ACCOUNT_PERSON, STATUS_ACTIVE, Account, ApiKey
from app.auth.security import API_KEY_PREFIX, decode_token, hash_api_key
from app.common.db import get_db
from app.common.errors import AppError

_bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
Db = Annotated[Session, Depends(get_db)]

_LAST_USED_INTERVAL = timedelta(minutes=1)


def login_account(cred: Credentials, db: Db) -> Account:
    """已登录的账号。允许「必须修改密码」的账号通过，只用于查看自己和修改密码。"""
    if cred is None:
        raise AppError(401, "NOT_LOGGED_IN", "请先登录")
    payload = decode_token(cred.credentials)
    if payload is None:
        raise AppError(401, "INVALID_TOKEN", "登录已失效，请重新登录")
    account = db.get(Account, payload["sub"])
    if account is None or account.status != STATUS_ACTIVE or account.account_type != ACCOUNT_PERSON:
        raise AppError(401, "INVALID_TOKEN", "登录已失效，请重新登录")
    if account.token_valid_after and payload["iat"] < int(account.token_valid_after.timestamp()):
        raise AppError(401, "INVALID_TOKEN", "登录已失效，请重新登录")
    return account


def current_account(account: Annotated[Account, Depends(login_account)]) -> Account:
    """已登录且不处于「必须修改密码」状态的账号。绝大多数管理接口使用这个。"""
    if account.must_change_password:
        raise AppError(403, "PASSWORD_CHANGE_REQUIRED", "请先修改初始密码")
    return account


def require_admin(account: Annotated[Account, Depends(current_account)]) -> Account:
    if not account.is_admin:
        raise AppError(403, "FORBIDDEN", "需要平台管理员权限")
    return account


def key_account(request: Request, cred: Credentials, db: Db) -> Account:
    """用密钥调用的账号。密钥不存在、过期、吊销，或所属账号停用时一律返回同样的错误。"""
    invalid = AppError(401, "INVALID_KEY", "密钥无效")
    if cred is None or not cred.credentials.startswith(API_KEY_PREFIX):
        raise invalid
    key = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(cred.credentials)))
    now = datetime.now(UTC)
    if key is None or key.revoked_at is not None or (key.expires_at and key.expires_at <= now):
        raise invalid
    account = db.get(Account, key.account_id)
    if account is None or account.status != STATUS_ACTIVE:
        raise invalid
    # 最近使用时间只精确到分钟级，避免每次调用都写库
    if key.last_used_at is None or now - key.last_used_at > _LAST_USED_INTERVAL:
        key.last_used_at = now
        db.commit()
    request.state.api_key_id = key.id
    return account


LoginAccount = Annotated[Account, Depends(login_account)]
CurrentAccount = Annotated[Account, Depends(current_account)]
AdminAccount = Annotated[Account, Depends(require_admin)]
KeyAccount = Annotated[Account, Depends(key_account)]
