"""操作日志与检索记录的查询。

平台管理员可以查看全部；其他账号必须指定一个自己有「管理」权限的知识库，只能看这个库的。
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import OperationLog, RetrievalLog
from app.auth.deps import CurrentAccount, Db
from app.auth.models import LEVEL_MANAGE, Account
from app.auth.permissions import require_kb_level
from app.common.errors import AppError

router = APIRouter(tags=["日志"])


class OperationLogOut(BaseModel):
    id: str
    created_at: datetime
    account_id: str | None
    account_name: str
    action: str
    kb_id: str | None
    target_type: str
    target_id: str
    detail: dict[str, Any]


class OperationLogPage(BaseModel):
    total: int
    items: list[OperationLogOut]


class RetrievalLogOut(BaseModel):
    id: str
    created_at: datetime
    account_id: str
    account_name: str
    via_api_key: bool
    kb_ids: list[str]
    query: str
    mode: str
    top_k: int
    result_count: int
    took_ms: int
    status: str
    error_code: str | None


class RetrievalLogPage(BaseModel):
    total: int
    items: list[RetrievalLogOut]


def _check_scope(db: Session, account: Account, kb_id: str | None) -> None:
    if account.is_admin:
        return
    if not kb_id:
        raise AppError(403, "FORBIDDEN", "请指定一个你有管理权限的知识库")
    require_kb_level(db, account, kb_id, LEVEL_MANAGE)


def _names(db: Session, account_ids: set[str | None]) -> dict[str, str]:
    ids = [a for a in account_ids if a]
    if not ids:
        return {}
    return {
        a.id: f"{a.display_name}（{a.username}）"
        for a in db.scalars(select(Account).where(Account.id.in_(ids)))
    }


@router.get("/logs/operations", response_model=OperationLogPage, summary="操作日志")
def list_operations(
    account: CurrentAccount,
    db: Db,
    kb_id: str | None = None,
    account_id: str | None = None,
    action: Annotated[str | None, Query(description="操作类型或其前缀，如 doc 或 chunk.update")] = None,
    start: datetime | None = None,
    end: datetime | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> OperationLogPage:
    _check_scope(db, account, kb_id)
    query = select(OperationLog)
    if kb_id:
        query = query.where(OperationLog.kb_id == kb_id)
    if account_id:
        query = query.where(OperationLog.account_id == account_id)
    if action:
        escaped = action.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(OperationLog.action.like(f"{escaped}%"))
    if start:
        query = query.where(OperationLog.created_at >= start)
    if end:
        query = query.where(OperationLog.created_at < end)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    logs = list(
        db.scalars(
            query.order_by(OperationLog.created_at.desc(), OperationLog.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    names = _names(db, {log.account_id for log in logs})
    items = [
        OperationLogOut(
            id=log.id,
            created_at=log.created_at,
            account_id=log.account_id,
            account_name=names.get(
                log.account_id or "", "系统" if log.account_id is None else "（已不存在）"
            ),
            action=log.action,
            kb_id=log.kb_id,
            target_type=log.target_type,
            target_id=log.target_id,
            detail=log.detail,
        )
        for log in logs
    ]
    return OperationLogPage(total=total, items=items)


@router.get("/logs/retrievals", response_model=RetrievalLogPage, summary="检索记录")
def list_retrievals(
    account: CurrentAccount,
    db: Db,
    kb_id: str | None = None,
    account_id: str | None = None,
    status: Literal["ok", "forbidden", "error"] | None = None,
    keyword: Annotated[str | None, Query(description="查询文本中包含的文字")] = None,
    start: datetime | None = None,
    end: datetime | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> RetrievalLogPage:
    _check_scope(db, account, kb_id)
    query = select(RetrievalLog)
    if kb_id:
        query = query.where(RetrievalLog.kb_ids.contains([kb_id]))
    if account_id:
        query = query.where(RetrievalLog.account_id == account_id)
    if status:
        query = query.where(RetrievalLog.status == status)
    if keyword and keyword.strip():
        escaped = keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(RetrievalLog.query.ilike(f"%{escaped}%"))
    if start:
        query = query.where(RetrievalLog.created_at >= start)
    if end:
        query = query.where(RetrievalLog.created_at < end)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    logs = list(
        db.scalars(
            query.order_by(RetrievalLog.created_at.desc(), RetrievalLog.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    names = _names(db, {log.account_id for log in logs})
    items = [
        RetrievalLogOut(
            id=log.id,
            created_at=log.created_at,
            account_id=log.account_id,
            account_name=names.get(log.account_id, "（已不存在）"),
            via_api_key=log.api_key_id is not None,
            kb_ids=log.kb_ids,
            query=log.query,
            mode=log.mode,
            top_k=log.top_k,
            result_count=log.result_count,
            took_ms=log.took_ms,
            status=log.status,
            error_code=log.error_code,
        )
        for log in logs
    ]
    return RetrievalLogPage(total=total, items=items)
