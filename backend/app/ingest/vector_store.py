"""Milvus 中的数据结构与读写。一个知识库对应一个集合。

每条切片存两份文字：
- content：切片原文，原样返回给调用方。
- search_text：用于关键字检索的文字 = 文档名 + 章节标题路径 + 原文，并把型号里的
  连字符、斜杠、下划线换成空格。这样「YDFLP-20-M8」会被分成 ydflp、20、m8 三个词，
  只输入型号的一部分也能命中；查询文本做同样的处理。
"""

import re
from pathlib import PurePath
from typing import Any

from pymilvus import DataType, Function, FunctionType

from app.common.clients import get_milvus

CONTENT_MAX_BYTES = 16384
SEARCH_TEXT_MAX_BYTES = 24000
CONTEXT_MAX_CHARS = 300
_INSERT_BATCH = 200

# jieba 分词，统一小写，丢弃标点等非中文、字母、数字的词
ANALYZER_PARAMS = {"tokenizer": "jieba", "filter": ["lowercase", "cnalphanumonly"]}
_MODEL_JOINER = re.compile(r"(?<=[A-Za-z0-9])[-/_](?=[A-Za-z0-9])")

OUTPUT_FIELDS = ["chunk_id", "doc_id", "seq", "content", "metadata"]


def collection_name(kb_id: str) -> str:
    return f"kb_{kb_id}"


def normalize_for_search(text: str) -> str:
    """关键字检索前对文字的处理，入库和查询必须一致。"""
    return _MODEL_JOINER.sub(" ", text)


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
    client.create_collection(name, schema=schema, index_params=index_params)
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
    search_text = normalize_for_search(embedding_text(doc_name, metadata.get("heading_path"), content))
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
    data = text.encode("utf-8")
    return text if len(data) <= limit else data[:limit].decode("utf-8", errors="ignore")


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
