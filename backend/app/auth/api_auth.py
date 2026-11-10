"""登录、当前账号、修改密码、自己的密钥。"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import select

from app.audit.service import log_operation
from app.auth import keys
from app.auth.deps import CurrentAccount, Db, LoginAccount
from app.auth.models import ACCOUNT_PERSON, STATUS_ACTIVE, Account
from app.auth.schemas import (
    AccountOut,
    ChangePasswordIn,
    KeyCreateIn,
    KeyCreateOut,
    KeyOut,
    LoginIn,
    LoginOut,
)
from app.auth.security import (
    create_token,
    hash_password,
    next_cutoff,
    verify_password,
)
from app.common.clients import get_redis
from app.common.errors import AppError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["登录与个人"])

MAX_LOGIN_FAILURES = 5
LOCK_SECONDS = 600
# 账号不存在时也做一次同样耗时的校验，避免通过响应时间判断登录名是否存在
_DUMMY_HASH = hash_password("dummy-password-for-timing")


def _failure_key(username: str) -> str:
    return f"kb:login_fail:{username.lower()}"


def _failure_count(username: str) -> int:
    try:
        return int(get_redis().get(_failure_key(username)) or 0)
    except Exception as exc:
        logger.warning("读取登录失败次数出错，本次不做锁定判断：%s", exc)
        return 0


def _record_failure(username: str) -> None:
    try:
        pipe = get_redis().pipeline()
        pipe.incr(_failure_key(username))
        pipe.expire(_failure_key(username), LOCK_SECONDS)
        pipe.execute()
    except Exception as exc:
        logger.warning("记录登录失败次数出错：%s", exc)


def _clear_failures(username: str) -> None:
    try:
        get_redis().delete(_failure_key(username))
    except Exception as exc:
        logger.warning("清除登录失败次数出错：%s", exc)


@router.post("/auth/login", response_model=LoginOut, summary="账号密码登录")
def login(body: LoginIn, db: Db) -> LoginOut:
    username = body.username.strip()
    if _failure_count(username) >= MAX_LOGIN_FAILURES:
        raise AppError(
            429, "LOGIN_LOCKED", f"连续输错 {MAX_LOGIN_FAILURES} 次，请 {LOCK_SECONDS // 60} 分钟后再试"
        )

    account = db.scalar(select(Account).where(Account.username == username))
    can_login = account is not None and account.account_type == ACCOUNT_PERSON
    password_ok = verify_password(body.password, account.password_hash if can_login else _DUMMY_HASH)
    if not (can_login and password_ok):
        _record_failure(username)
        logger.info("登录失败：%s", username)
        raise AppError(401, "LOGIN_FAILED", "登录名或密码不正确")
    assert account is not None
    if account.status != STATUS_ACTIVE:
        raise AppError(403, "ACCOUNT_DISABLED", "账号已停用，请联系平台管理员")

    _clear_failures(username)
    account.last_login_at = datetime.now(UTC)
    db.commit()
    token, expires_at = create_token(account.id, account.token_valid_after)
    logger.info("登录成功：%s", username)
    return LoginOut(token=token, expires_at=expires_at, account=AccountOut.model_validate(account))


@router.get("/auth/me", response_model=AccountOut, summary="当前登录的账号")
def me(account: LoginAccount) -> Account:
    return account


@router.post("/auth/change-password", response_model=LoginOut, summary="修改自己的密码")
def change_password(body: ChangePasswordIn, account: LoginAccount, db: Db) -> LoginOut:
    if not verify_password(body.old_password, account.password_hash):
        raise AppError(400, "WRONG_PASSWORD", "原密码不正确")
    if body.new_password == body.old_password:
        raise AppError(400, "INVALID_PARAM", "新密码不能与原密码相同")

    account.password_hash = hash_password(body.new_password)
    account.must_change_password = False
    # 让此前签发的登录凭证全部失效，再签发一个新的给当前会话
    account.token_valid_after = next_cutoff(account.token_valid_after)
    log_operation(db, account.id, "auth.change_password", "account", account.id)
    db.commit()
    token, expires_at = create_token(account.id, account.token_valid_after)
    return LoginOut(token=token, expires_at=expires_at, account=AccountOut.model_validate(account))


@router.get("/me/keys", response_model=list[KeyOut], summary="我的密钥列表")
def list_my_keys(account: CurrentAccount, db: Db) -> list:
    return keys.list_keys(db, account.id)


@router.post("/me/keys", response_model=KeyCreateOut, summary="创建我的密钥，明文只返回这一次")
def create_my_key(body: KeyCreateIn, account: CurrentAccount, db: Db) -> KeyCreateOut:
    return keys.create_key(db, owner=account, operator=account, body=body)


@router.delete("/me/keys/{key_id}", response_model=KeyOut, summary="吊销我的密钥")
def revoke_my_key(key_id: str, account: CurrentAccount, db: Db):
    return keys.revoke_key(db, owner_id=account.id, key_id=key_id, operator=account)
