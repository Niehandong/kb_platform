"""知识库的数据表。文档与切片的表在实现文档入库时加入。"""

from typing import Any

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base, TimestampMixin, new_id

KB_ACTIVE = "active"
KB_REBUILDING = "rebuilding"
KB_DELETING = "deleting"


class KnowledgeBase(Base, TimestampMixin):
    __tablename__ = "knowledge_base"
    __table_args__ = {"comment": "知识库：一个知识库对应 Milvus 中的一个集合"}

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id, comment="主键")
    name: Mapped[str] = mapped_column(String(128), comment="知识库名称")
    description: Mapped[str] = mapped_column(Text, default="", comment="描述")
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否公开；公开时所有账号可只读")
    collection_name: Mapped[str] = mapped_column(
        String(64), unique=True, comment="对应的 Milvus 集合名，格式 kb_<主键>"
    )
    embedding_model: Mapped[str] = mapped_column(String(64), comment="建库时使用的向量模型名")
    embedding_dim: Mapped[int] = mapped_column(Integer, comment="向量维度")
    chunk_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, comment="默认切分设置：切分方式、分隔符、长度、重叠、标题层级"
    )
    status: Mapped[str] = mapped_column(
        String(16),
        default=KB_ACTIVE,
        comment="状态：active 正常，rebuilding 正在重建索引，deleting 正在删除",
    )
    created_by: Mapped[str] = mapped_column(String(32), comment="创建者账号")
