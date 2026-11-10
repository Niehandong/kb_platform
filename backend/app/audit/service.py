"""写操作日志。只往会话里加一条记录，由调用方随业务一起提交。"""

from typing import Any

from sqlalchemy.orm import Session

from app.audit.models import OperationLog


def log_operation(
    db: Session,
    account_id: str | None,
    action: str,
    target_type: str,
    target_id: str,
    *,
    kb_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    db.add(
        OperationLog(
            account_id=account_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            kb_id=kb_id,
            detail=detail or {},
        )
    )
