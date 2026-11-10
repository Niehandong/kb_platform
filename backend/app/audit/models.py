"""操作日志与检索记录的数据表。"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text, func
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


class RetrievalLog(Base):
    __tablename__ = "retrieval_log"
    __table_args__ = (
        Index("ix_retrieval_log_created_at", "created_at"),
        Index("ix_retrieval_log_account_id", "account_id"),
        {"comment": "检索记录：每次调用召回接口或召回测试的情况，按配置的天数保留"},
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id, comment="主键")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="检索时间"
    )
    account_id: Mapped[str] = mapped_column(String(32), comment="调用者账号")
    api_key_id: Mapped[str | None] = mapped_column(
        String(32), comment="使用的密钥；在管理界面做召回测试时为空"
    )
    kb_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, comment="本次请求检索的知识库")
    query: Mapped[str] = mapped_column(Text, comment="查询文本")
    mode: Mapped[str] = mapped_column(
        String(16), comment="检索方式：vector 向量，keyword 关键字，hybrid 融合"
    )
    top_k: Mapped[int] = mapped_column(Integer, comment="请求返回的条数")
    result_count: Mapped[int] = mapped_column(Integer, default=0, comment="实际返回的条数")
    took_ms: Mapped[int] = mapped_column(Integer, default=0, comment="耗时，毫秒")
    status: Mapped[str] = mapped_column(
        String(16), comment="结果：ok 成功，forbidden 无权限被拒绝，error 出错"
    )
    error_code: Mapped[str | None] = mapped_column(String(64), comment="未成功时的错误码")
