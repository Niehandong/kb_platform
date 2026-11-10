"""Milvus 中的数据结构与读写。一个知识库对应一个集合。

每条切片存两份文字：
- content：切片原文，原样返回给调用方。
- search_text：用于关键字检索的词，由 tokenize_for_search 生成，词之间用空格分开。
  内容 = 文档名 + 章节标题路径 + 原文。

关键字检索的切词由平台自己完成，不使用 jieba 这类词典分词器。原因是词典分词器会按
上下文猜词：「卡料」单独出现时是一个词，在「中转台卡料」里却被切成「台卡」「料」，
于是搜「卡料」找不到这句话。行业术语大多不在词典里，这种情况很常见。
"""

import re
from pathlib import PurePath
from typing import Any

from pymilvus import DataType, Function, FunctionType

from app.common.clients import get_milvus

CONTENT_MAX_BYTES = 16384
SEARCH_TEXT_MAX_BYTES = 60000
CONTEXT_MAX_CHARS = 300
_INSERT_BATCH = 200

# 切词已在平台内完成，Milvus 只需按空格分开
ANALYZER_PARAMS = {"tokenizer": "whitespace"}
_CJK = "\u3400-\u4dbf\u4e00-\u9fff"
# 一段连续的汉字，或一段字母数字（数字中间的小数点保留，如 2.14.11、0.5）
_SEGMENT = re.compile(rf"[{_CJK}]+|[a-z0-9]+(?:\.[0-9][a-z0-9]*)*")

OUTPUT_FIELDS = ["chunk_id", "doc_id", "seq", "content", "metadata"]


def collection_name(kb_id: str) -> str:
    return f"kb_{kb_id}"


def search_tokens(text: str) -> list[str]:
    """把文字切成关键字检索用的词。入库和查询用同一个函数，结果与上下文无关。

    - 汉字：每相邻两个字为一个词。「中转台卡料」→ 中转、转台、台卡、卡料。
      单独一个汉字时就是它自己。
    - 字母数字：统一小写，按符号断开。「YDFLP-20-M8」→ ydflp、20、m8；「200W」→ 200w。
    """
    tokens: list[str] = []
    for segment in _SEGMENT.findall(text.lower()):
        if segment[0].isascii():
            tokens.append(segment)
        elif len(segment) == 1:
            tokens.append(segment)
        else:
            tokens.extend(segment[i : i + 2] for i in range(len(segment) - 1))
    return tokens


def tokenize_for_search(text: str) -> str:
    return " ".join(search_tokens(text))


def context_prefix(doc_name: str, heading_path: str | None) -> str:
    """切片的上下文：文档名（去掉扩展名）和章节标题路径。

    很多切片本身不含产品型号（如规格书里的一行参数），型号只出现在文档名或标题里，
    把它们一起参与向量化和关键字检索，这类切片才能被找到。
    """
    parts = [PurePath(doc_name).stem]
    if heading_path:
        parts.append(heading_path)
    return " > ".join(parts)[:CONTEXT_MAX_CHARS]


def embedding_text(doc_name: str, heading_path: str | None, content: str) -> str:
    """送去向量化的文字。"""
    return f"{context_prefix(doc_name, heading_path)}\n{content}"


def create_collection(name: str, dim: int) -> None:
    client = get_milvus()
    if client.has_collection(name):
        return
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("chunk_id", DataType.VARCHAR, max_length=32, is_primary=True)
    schema.add_field("doc_id", DataType.VARCHAR, max_length=32)
    schema.add_field("seq", DataType.INT32)
    schema.add_field("content", DataType.VARCHAR, max_length=CONTENT_MAX_BYTES)
    schema.add_field(
        "search_text",
        DataType.VARCHAR,
        max_length=SEARCH_TEXT_MAX_BYTES,
        enable_analyzer=True,
        analyzer_params=ANALYZER_PARAMS,
    )
    schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=dim)
    schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
    schema.add_field("enabled", DataType.BOOL)
    schema.add_field("metadata", DataType.JSON)
    schema.add_function(
        Function(
            name="search_text_bm25",
            function_type=FunctionType.BM25,
            input_field_names=["search_text"],
            output_field_names=["sparse_vector"],
        )
    )

    index_params = client.prepare_index_params()
    index_params.add_index(
        "dense_vector", index_type="HNSW", metric_type="COSINE", params={"M": 16, "efConstruction": 200}
    )
    index_params.add_index(
        "sparse_vector",
        index_type="SPARSE_INVERTED_INDEX",
        metric_type="BM25",
        params={"inverted_index_algo": "DAAT_MAXSCORE", "bm25_k1": 1.2, "bm25_b": 0.75},
    )
    index_params.add_index("doc_id", index_type="INVERTED")
    # 强一致读取：入库、删除、停用之后的下一次检索立刻生效。
    # 默认的一致性级别允许短暂读到旧数据，停用的内容可能在几秒内仍被搜到。
    client.create_collection(name, schema=schema, index_params=index_params, consistency_level="Strong")
    client.load_collection(name)


def drop_collection(name: str) -> None:
    client = get_milvus()
    if client.has_collection(name):
        client.drop_collection(name)


def build_row(
    *,
    chunk_id: str,
    doc_id: str,
    seq: int,
    content: str,
    doc_name: str,
    metadata: dict[str, Any],
    enabled: bool,
    vector: list[float],
) -> dict[str, Any]:
    search_text = tokenize_for_search(embedding_text(doc_name, metadata.get("heading_path"), content))
    return {
        "chunk_id": chunk_id,
        "doc_id": doc_id,
        "seq": seq,
        "content": content,
        "search_text": _fit_bytes(search_text, SEARCH_TEXT_MAX_BYTES),
        "dense_vector": vector,
        "enabled": enabled,
        "metadata": {**metadata, "doc_name": doc_name},
    }


def _fit_bytes(text: str, limit: int) -> str:
    """超过字段长度时从末尾丢弃整个词。4000 字的切片约 3 万字节，正常不会触发。"""
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    return data[:limit].decode("utf-8", errors="ignore").rsplit(" ", 1)[0]


def insert_rows(name: str, rows: list[dict[str, Any]]) -> None:
    client = get_milvus()
    for start in range(0, len(rows), _INSERT_BATCH):
        client.insert(name, rows[start : start + _INSERT_BATCH])


def delete_chunks(name: str, chunk_ids: list[str]) -> None:
    client = get_milvus()
    for start in range(0, len(chunk_ids), 500):
        client.delete(name, ids=chunk_ids[start : start + 500])


def delete_document(name: str, doc_id: str) -> None:
    get_milvus().delete(name, filter=f'doc_id == "{doc_id}"')


def count_document(name: str, doc_id: str) -> int:
    rows = get_milvus().query(
        name, filter=f'doc_id == "{doc_id}"', output_fields=["count(*)"], consistency_level="Strong"
    )
    return int(rows[0]["count(*)"]) if rows else 0
