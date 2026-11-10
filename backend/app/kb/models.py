"""知识库、文档、切片的数据表。"""

from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, Text
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
    rebuild_error: Mapped[str | None] = mapped_column(Text, comment="最近一次重建索引失败的原因；成功后清空")
    created_by: Mapped[str] = mapped_column(String(32), comment="创建者账号")


DOC_PARSING = "parsing"
DOC_PENDING_CONFIRM = "pending_confirm"
DOC_EMBEDDING = "embedding"
DOC_READY = "ready"
DOC_FAILED = "failed"

STAGE_PARSE = "parse"
STAGE_EMBED = "embed"

SYNC_PENDING = "pending"
SYNC_SYNCED = "synced"
SYNC_FAILED = "failed"

ORIGIN_AUTO = "auto"
ORIGIN_MANUAL = "manual"


class Document(Base, TimestampMixin):
    __tablename__ = "document"
    __table_args__ = (
        Index("ix_document_kb_id_created_at", "kb_id", "created_at"),
        Index("ix_document_kb_id_sha256", "kb_id", "sha256"),
        {"comment": "文档：上传到知识库的一个文件"},
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id, comment="主键")
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_base.id"), comment="所属知识库")
    name: Mapped[str] = mapped_column(String(255), comment="文件名")
    file_type: Mapped[str] = mapped_column(String(16), comment="文件类型：txt、md、docx、pdf、xlsx、csv")
    file_size: Mapped[int] = mapped_column(BigInteger, comment="文件大小，字节")
    storage_key: Mapped[str] = mapped_column(String(255), comment="当前生效版本在 MinIO 中的位置")
    sha256: Mapped[str] = mapped_column(String(64), comment="当前生效文件的内容哈希，用于识别重复上传")
    generation: Mapped[int] = mapped_column(
        Integer, default=0, comment="当前生效的版本号；0 表示还没有任何版本可检索，每次入库成功后加一"
    )
    pending_storage_key: Mapped[str | None] = mapped_column(
        String(255), comment="替换过程中新文件在 MinIO 中的位置，替换完成后清空"
    )
    status: Mapped[str] = mapped_column(
        String(16),
        default=DOC_PARSING,
        comment="处理状态：parsing 解析中，pending_confirm 待确认，"
        "embedding 向量化中，ready 可用，failed 失败",
    )
    failed_stage: Mapped[str | None] = mapped_column(
        String(16), comment="失败发生在哪一步：parse 解析与切分，embed 向量化与入库；重试时从这一步继续"
    )
    error_message: Mapped[str | None] = mapped_column(Text, comment="处理失败的原因")
    auto_confirm: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否跳过人工确认：解析完成后直接向量化入库"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用；停用后其切片不参与检索")
    chunk_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, comment="本文档使用的切分设置；effective_mode 记录实际采用的切分方式"
    )
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, comment="最近一次切分得到的切片数")
    tags: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, comment="上传时填写的自定义标签（键值对），会复制到每个切片的元数据"
    )
    created_by: Mapped[str] = mapped_column(String(32), comment="上传者账号")


class Chunk(Base, TimestampMixin):
    __tablename__ = "chunk"
    __table_args__ = (
        Index("ix_chunk_doc_id_generation_seq", "doc_id", "generation", "seq"),
        Index("ix_chunk_kb_id", "kb_id"),
        {"comment": "切片：文档切分后的一段文字，是检索的基本单位"},
    )

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=new_id, comment="主键，同时是 Milvus 中的主键"
    )
    kb_id: Mapped[str] = mapped_column(String(32), comment="所属知识库")
    doc_id: Mapped[str] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"), comment="所属文档")
    generation: Mapped[int] = mapped_column(
        Integer, comment="所属的文档版本号；等于文档的 generation 时已生效，大一号时是正在准备的新版本"
    )
    seq: Mapped[int] = mapped_column(Integer, comment="在文档中的序号，从 0 开始")
    content: Mapped[str] = mapped_column(Text, comment="切片正文")
    char_count: Mapped[int] = mapped_column(Integer, comment="正文字数")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    origin: Mapped[str] = mapped_column(
        String(16), default=ORIGIN_AUTO, comment="来源：auto 自动切分，manual 手工新增"
    )
    is_edited: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否被手工修改过")
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, comment="元数据：章节标题路径、页码、自定义标签"
    )
    sync_status: Mapped[str] = mapped_column(
        String(16),
        default=SYNC_PENDING,
        comment="与 Milvus 的同步状态：pending 待同步，synced 已同步，failed 失败",
    )
    updated_by: Mapped[str | None] = mapped_column(String(32), comment="最后修改者账号")
