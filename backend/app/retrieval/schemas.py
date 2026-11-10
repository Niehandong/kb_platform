"""召回接口的请求与响应结构。"""

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.ingest.vector_store import search_tokens

MAX_KBS = 10
MAX_TOP_K = 50
_ID = re.compile(r"^[0-9a-f]{32}$")
# 标签名会拼进 Milvus 的过滤表达式，不允许出现引号、反斜杠和方括号
_TAG_KEY = re.compile(r'^[^"\\\[\]]{1,32}$')


class SearchFilter(BaseModel):
    doc_ids: list[str] | None = Field(default=None, max_length=100, description="只在这些文档内检索")
    tags: dict[str, str] | None = Field(default=None, description="文档的自定义标签必须全部相等")

    @field_validator("doc_ids")
    @classmethod
    def _check_doc_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and not all(_ID.match(v) for v in value):
            raise ValueError("文档 ID 格式不正确")
        return value

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is not None:
            if len(value) > 20 or not all(_TAG_KEY.match(k) and len(v) <= 128 for k, v in value.items()):
                raise ValueError("标签最多 20 个，名称不超过 32 个字且不能包含引号、反斜杠和方括号")
        return value


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000, description="查询文本")
    kb_ids: list[str] = Field(min_length=1, max_length=MAX_KBS, description="要检索的知识库，1 到 10 个")
    mode: Literal["vector", "keyword", "hybrid"] = Field(
        default="hybrid", description="vector 向量检索，keyword 关键字检索，hybrid 两路融合"
    )
    top_k: int = Field(default=10, ge=1, le=MAX_TOP_K, description="返回条数")
    score_threshold: float | None = Field(
        default=None, ge=-1, le=1, description="只在 vector 模式下有效：丢弃余弦相似度低于该值的结果"
    )
    filter: SearchFilter | None = None

    @field_validator("query")
    @classmethod
    def _check_query(cls, value: str) -> str:
        if not search_tokens(value):
            raise ValueError("查询文本没有可检索的内容")
        return value.strip()

    @field_validator("kb_ids")
    @classmethod
    def _check_kb_ids(cls, value: list[str]) -> list[str]:
        # 格式不对的 ID 不在这里报错：它们会在权限判定时和「不存在」一样被拒绝
        return list(dict.fromkeys(value))


class SearchHit(BaseModel):
    chunk_id: str
    kb_id: str
    kb_name: str
    doc_id: str
    doc_name: str
    seq: int = Field(description="切片在文档中的序号，可用于取相邻切片")
    content: str
    score: float
    metadata: dict[str, Any] = Field(description="章节标题路径 heading_path、页码 page、自定义标签 tags")


class SearchOut(BaseModel):
    mode: str
    score_type: Literal["cosine", "bm25", "rrf"] = Field(
        description="score 的含义：cosine 余弦相似度；bm25 关键字相关度；rrf 按名次融合的分数。"
        "只有 cosine 可以跨请求比较和设置阈值"
    )
    took_ms: int
    results: list[SearchHit]


class OpenKbOut(BaseModel):
    id: str
    name: str
    description: str
    my_level: str
    document_count: int


class OpenChunkOut(BaseModel):
    chunk_id: str
    kb_id: str
    doc_id: str
    doc_name: str
    seq: int
    content: str
    metadata: dict[str, Any]


class NeighborsOut(BaseModel):
    before: list[OpenChunkOut]
    chunk: OpenChunkOut
    after: list[OpenChunkOut]
