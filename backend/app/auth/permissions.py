"""知识库权限判定。所有接口都通过这里判断权限，不在别处自己写判断。

有效权限取以下各项中最高的一档：
1. 平台管理员：管理
2. 账号被单独授予的档位
3. 账号所在各分组被授予的档位
4. 知识库公开：只读
"""

from collections.abc import Iterable

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.auth.models import (
    LEVEL_MANAGE,
    LEVEL_RANK,
    LEVEL_READ,
    SUBJECT_ACCOUNT,
    SUBJECT_GROUP,
    Account,
    GroupMember,
    KbGrant,
)
from app.common.errors import AppError
from app.kb.models import KnowledgeBase


def _higher(a: str | None, b: str) -> str:
    return b if a is None or LEVEL_RANK[b] > LEVEL_RANK[a] else a


def effective_levels(db: Session, account: Account, kb_ids: Iterable[str] | None = None) -> dict[str, str]:
    """返回账号在各知识库上的有效档位。无权限的知识库不出现在结果里。

    kb_ids 为 None 时计算所有知识库；否则只计算给定的这些。
    """
    wanted = None if kb_ids is None else list(set(kb_ids))
    if wanted is not None and not wanted:
        return {}

    kb_query = select(KnowledgeBase.id, KnowledgeBase.is_public)
    if wanted is not None:
        kb_query = kb_query.where(KnowledgeBase.id.in_(wanted))
    kbs = db.execute(kb_query).all()

    if account.is_admin:
        return {kb_id: LEVEL_MANAGE for kb_id, _ in kbs}

    levels: dict[str, str] = {kb_id: LEVEL_READ for kb_id, is_public in kbs if is_public}

    my_groups = select(GroupMember.group_id).where(GroupMember.account_id == account.id)
    grant_query = select(KbGrant.kb_id, KbGrant.level).where(
        or_(
            and_(KbGrant.subject_type == SUBJECT_ACCOUNT, KbGrant.subject_id == account.id),
            and_(KbGrant.subject_type == SUBJECT_GROUP, KbGrant.subject_id.in_(my_groups)),
        )
    )
    if wanted is not None:
        grant_query = grant_query.where(KbGrant.kb_id.in_(wanted))
    for kb_id, level in db.execute(grant_query):
        levels[kb_id] = _higher(levels.get(kb_id), level)
    return levels


def effective_level(db: Session, account: Account, kb_id: str) -> str | None:
    """返回账号在某个知识库上的有效档位，无权限或知识库不存在时返回 None。"""
    return effective_levels(db, account, [kb_id]).get(kb_id)


def has_level(actual: str | None, required: str) -> bool:
    return actual is not None and LEVEL_RANK[actual] >= LEVEL_RANK[required]


def require_kb_level(db: Session, account: Account, kb_id: str, required: str) -> str:
    """要求账号在知识库上至少有 required 档位，返回实际档位。

    完全无权限时按「不存在」处理，不泄露知识库是否存在；有权限但档位不够时返回 403。
    """
    actual = effective_level(db, account, kb_id)
    if actual is None:
        raise AppError(404, "NOT_FOUND", "知识库不存在")
    if not has_level(actual, required):
        names = {"read": "只读", "edit": "编辑", "manage": "管理"}
        raise AppError(
            403, "FORBIDDEN", f"需要「{names[required]}」权限", {"required": required, "actual": actual}
        )
    return actual
