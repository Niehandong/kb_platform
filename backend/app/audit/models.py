"""操作日志的数据表。检索记录的表在实现召回接口时加入。"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base, new_id


class OperationLog(Base):
    __tablename__ = "operation_log"
    __table_args__ = (
        Index("ix_operation_log_created_at", "created_at"),
        Index("ix_operation_log_kb_id", "kb_id"),
        {"comment": "操作日志：谁在何时对哪个对象做了什么"},
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id, comment="主键")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="操作时间"
    )
    account_id: Mapped[str | None] = mapped_column(String(32), comment="操作者账号；系统自动执行时为空")
    action: Mapped[str] = mapped_column(String(64), comment="操作类型，如 account.create、kb.grant")
    kb_id: Mapped[str | None] = mapped_column(String(32), comment="涉及的知识库，没有则为空")
    target_type: Mapped[str] = mapped_column(String(32), comment="操作对象的类型，如 account、group、api_key")
    target_id: Mapped[str] = mapped_column(String(32), comment="操作对象的主键")
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, comment="操作的关键内容；不记录密码和密钥"
    )
