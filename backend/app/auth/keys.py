"""密钥的创建、列表、吊销。个人管理自己的密钥和管理员管理服务账号的密钥共用这里的逻辑。"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import log_operation
from app.auth.models import Account, ApiKey
from app.auth.schemas import KeyCreateIn, KeyCreateOut, KeyOut
from app.auth.security import generate_api_key
from app.common.errors import AppError

MAX_ACTIVE_KEYS = 20


def list_keys(db: Session, account_id: str) -> list[ApiKey]:
    return list(
        db.scalars(select(ApiKey).where(ApiKey.account_id == account_id).order_by(ApiKey.created_at.desc()))
    )


def create_key(db: Session, *, owner: Account, operator: Account, body: KeyCreateIn) -> KeyCreateOut:
    now = datetime.now(UTC)
    if body.expires_at is not None:
        if body.expires_at.tzinfo is None:
            raise AppError(400, "INVALID_PARAM", "过期时间必须带时区，例如 2027-01-01T00:00:00+08:00")
        if body.expires_at <= now:
            raise AppError(400, "INVALID_PARAM", "过期时间必须晚于当前时间")
    active = db.scalar(
        select(func.count())
        .select_from(ApiKey)
        .where(ApiKey.account_id == owner.id, ApiKey.revoked_at.is_(None))
    )
    if (active or 0) >= MAX_ACTIVE_KEYS:
        raise AppError(400, "TOO_MANY_KEYS", f"每个账号最多保留 {MAX_ACTIVE_KEYS} 把未吊销的密钥")

    plain, prefix, key_hash = generate_api_key()
    key = ApiKey(
        account_id=owner.id,
        name=body.name.strip(),
        key_prefix=prefix,
        key_hash=key_hash,
        expires_at=body.expires_at,
        created_by=operator.id,
    )
    db.add(key)
    db.flush()
    log_operation(
        db, operator.id, "key.create", "api_key", key.id, detail={"owner": owner.username, "name": key.name}
    )
    db.commit()
    return KeyCreateOut(key=plain, info=KeyOut.model_validate(key))


def revoke_key(db: Session, *, owner_id: str, key_id: str, operator: Account) -> ApiKey:
    key = db.get(ApiKey, key_id)
    if key is None or key.account_id != owner_id:
        raise AppError(404, "NOT_FOUND", "密钥不存在")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(UTC)
        log_operation(db, operator.id, "key.revoke", "api_key", key.id, detail={"name": key.name})
        db.commit()
    return key
