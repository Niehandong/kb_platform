"""知识库、授权、文档、切片接口的请求与响应结构。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.ingest.chunking import ChunkConfig

Level = Literal["read", "edit", "manage"]


class KbCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    is_public: bool = False
    chunk_config: ChunkConfig = Field(default_factory=ChunkConfig, description="默认切分设置")


class KbUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    is_public: bool | None = None
    chunk_config: ChunkConfig | None = None


class KbOut(BaseModel):
    id: str
    name: str
    description: str
    is_public: bool
    status: str
    embedding_model: str
    chunk_config: dict[str, Any]
    my_level: Level = Field(description="当前账号在这个知识库上的权限档位")
    document_count: int
    rebuild_error: str | None = Field(description="最近一次重建索引失败的原因")
    needs_rebuild: bool = Field(description="建库时的向量模型与当前配置不一致，需要重建索引后才能检索")
    created_at: datetime


class GrantIn(BaseModel):
    subject_type: Literal["group", "account"]
    subject_id: str = Field(min_length=32, max_length=32)
    level: Level


class GrantOut(BaseModel):
    id: str
    subject_type: str
    subject_id: str
    subject_name: str = Field(description="分组名称，或账号的「显示名称（登录名）」")
    level: Level
    created_at: datetime


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kb_id: str
    name: str
    file_type: str
    file_size: int
    status: str
    failed_stage: str | None
    error_message: str | None
    auto_confirm: bool
    enabled: bool
    searchable: bool = Field(description="当前是否有已入库的版本可被检索")
    replacing: bool = Field(description="已有入库的版本，同时正在准备新版本（替换文件或重新切分）")
    edited_chunks: int = Field(description="已入库版本中手工修改或手工新增的切片数；替换或重新切分会丢失它们")
    chunk_config: dict[str, Any]
    chunk_count: int
    tags: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class DocumentPage(BaseModel):
    total: int
    items: list[DocumentOut]


class ChunkOut(BaseModel):
    id: str
    seq: int
    content: str
    char_count: int
    enabled: bool
    origin: str
    is_edited: bool
    metadata: dict[str, Any]
    sync_status: str


class ChunkPage(BaseModel):
    total: int
    view: Literal["pending", "active"] = Field(
        description="pending 正在准备、尚未入库的版本；active 已入库的版本"
    )
    items: list[ChunkOut]


class RechunkIn(BaseModel):
    chunk_config: ChunkConfig
    discard_edits: bool = Field(default=False, description="确认丢弃已入库版本中手工修改过的切片")


class DocumentUpdateIn(BaseModel):
    enabled: bool | None = Field(default=None, description="停用后文档的切片不参与检索，但不删除")
    tags: dict[str, str] | None = Field(default=None, description="整体替换文档的自定义标签")


class ChunkUpdateIn(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    enabled: bool | None = None


class ChunkCreateIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    position: int | None = Field(
        default=None, ge=0, description="插入到这个序号的位置，其后的切片顺延；不填则加在最后"
    )
