"""基础召回：权限判定、查询文本向量化、在各知识库的集合里检索、跨库合并。

平台只提供取数的原料，不做重排序、问题改写和答案生成，这些由调用的应用决定。
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pymilvus import AnnSearchRequest, RRFRanker
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import RetrievalLog
from app.auth.models import Account
from app.auth.permissions import effective_levels
from app.common.clients import get_milvus
from app.common.db import SessionLocal
from app.common.errors import AppError
from app.config import get_settings
from app.ingest.embedding import EmbeddingError, embed_query
from app.ingest.vector_store import OUTPUT_FIELDS, tokenize_for_search
from app.kb.models import KB_DELETING, KB_REBUILDING, KnowledgeBase
from app.retrieval.schemas import SearchFilter, SearchHit, SearchIn, SearchOut

logger = logging.getLogger(__name__)

RRF_K = 60
_pool = ThreadPoolExecutor(max_workers=10, thread_name_prefix="retrieval")


def search(db: Session, account: Account, body: SearchIn, api_key_id: str | None) -> SearchOut:
    """执行一次检索并写检索记录。被拒绝和出错的请求也会记录。"""
    started = time.perf_counter()
    status, error_code, count = "ok", None, 0
    try:
        result = _search(db, account, body)
        result.took_ms = round((time.perf_counter() - started) * 1000)
        count = len(result.results)
        return result
    except AppError as exc:
        status, error_code = ("forbidden" if exc.status == 403 else "error"), exc.code
        raise
    except Exception:
        status, error_code = "error", "INTERNAL_ERROR"
        raise
    finally:
        _write_log(account.id, api_key_id, body, status, error_code, count, started)


def _write_log(
    account_id: str,
    api_key_id: str | None,
    body: SearchIn,
    status: str,
    error_code: str | None,
    count: int,
    started: float,
) -> None:
    # 用独立的会话写记录：检索失败时也要写，且记录写失败不能影响检索结果
    try:
        with SessionLocal() as log_db:
            log_db.add(
                RetrievalLog(
                    account_id=account_id,
                    api_key_id=api_key_id,
                    kb_ids=body.kb_ids,
                    query=body.query,
                    mode=body.mode,
                    top_k=body.top_k,
                    result_count=count,
                    took_ms=round((time.perf_counter() - started) * 1000),
                    status=status,
                    error_code=error_code,
                )
            )
            log_db.commit()
    except Exception:
        logger.exception("写检索记录失败")


def _authorized_kbs(db: Session, account: Account, kb_ids: list[str]) -> list[KnowledgeBase]:
    """只要有一个知识库不存在或无权限，整个请求拒绝，不悄悄跳过。"""
    levels = effective_levels(db, account, kb_ids)
    kbs = {
        kb.id: kb
        for kb in db.scalars(
            select(KnowledgeBase).where(KnowledgeBase.id.in_(levels), KnowledgeBase.status != KB_DELETING)
        )
    }
    denied = [kb_id for kb_id in kb_ids if kb_id not in kbs]
    if denied:
        # 不存在和无权限返回同样的错误，不泄露知识库是否存在
        raise AppError(403, "KB_FORBIDDEN", "以下知识库不存在或没有权限", {"kb_ids": denied})
    return [kbs[kb_id] for kb_id in kb_ids]


def build_filter(search_filter: SearchFilter | None) -> str:
    """Milvus 过滤表达式。停用的切片始终排除。"""
    parts = ["enabled == true"]
    if search_filter and search_filter.doc_ids:
        parts.append(f"doc_id in {json.dumps(search_filter.doc_ids)}")
    if search_filter and search_filter.tags:
        for key, value in search_filter.tags.items():
            # 值用 JSON 编码，引号和反斜杠都会被转义；名称的字符范围已在请求校验时限制
            parts.append(f'metadata["tags"]["{key}"] == {json.dumps(value, ensure_ascii=False)}')
    return " and ".join(parts)


def _search(db: Session, account: Account, body: SearchIn) -> SearchOut:
    kbs = _authorized_kbs(db, account, body.kb_ids)
    rebuilding = [kb.id for kb in kbs if kb.status == KB_REBUILDING]
    if rebuilding:
        raise AppError(409, "KB_REBUILDING", "以下知识库正在重建索引，暂时不能检索", {"kb_ids": rebuilding})
    settings = get_settings()
    use_vector = body.mode in ("vector", "hybrid")
    if use_vector:
        stale = [kb.id for kb in kbs if kb.embedding_model != settings.EMBEDDING_MODEL]
        if stale:
            raise AppError(
                409,
                "KB_NEEDS_REBUILD",
                "以下知识库入库时使用的向量模型与当前配置不同，需要重建索引后才能做向量检索",
                {"kb_ids": stale},
            )

    vector: list[float] | None = None
    if use_vector:
        try:
            vector = embed_query(body.query)
        except EmbeddingError as exc:
            logger.warning("查询文本向量化失败：%s", exc)
            raise AppError(503, "EMBEDDING_UNAVAILABLE", "向量模型服务暂时不可用，可改用关键字检索") from exc

    expr = build_filter(body.filter)
    keyword = tokenize_for_search(body.query)
    try:
        per_kb = list(_pool.map(lambda kb: _search_one(kb, body, vector, keyword, expr), kbs))
    except Exception as exc:
        logger.exception("在向量库中检索失败")
        raise AppError(503, "VECTOR_STORE_UNAVAILABLE", "向量库暂时不可用") from exc

    hits, score_type = merge(per_kb, body.mode, body.top_k)
    if body.mode == "vector" and body.score_threshold is not None:
        hits = [h for h in hits if h.score >= body.score_threshold]
    return SearchOut(mode=body.mode, score_type=score_type, took_ms=0, results=hits)  # type: ignore[arg-type]


def _search_one(
    kb: KnowledgeBase, body: SearchIn, vector: list[float] | None, keyword: str, expr: str
) -> list[SearchHit]:
    client = get_milvus()
    name = kb.collection_name
    if body.mode == "vector":
        raw = client.search(
            name,
            data=[vector],
            anns_field="dense_vector",
            limit=body.top_k,
            filter=expr,
            output_fields=OUTPUT_FIELDS,
        )
    elif body.mode == "keyword":
        raw = client.search(
            name,
            data=[keyword],
            anns_field="sparse_vector",
            limit=body.top_k,
            filter=expr,
            output_fields=OUTPUT_FIELDS,
        )
    else:
        candidates = max(body.top_k * 2, 20)
        requests = [
            AnnSearchRequest(data=[vector], anns_field="dense_vector", param={}, limit=candidates, expr=expr),
            AnnSearchRequest(
                data=[keyword], anns_field="sparse_vector", param={}, limit=candidates, expr=expr
            ),
        ]
        raw = client.hybrid_search(
            name, requests, ranker=RRFRanker(RRF_K), limit=body.top_k, output_fields=OUTPUT_FIELDS
        )
    return [_to_hit(kb, hit) for hit in raw[0]]


def _to_hit(kb: KnowledgeBase, hit: dict[str, Any]) -> SearchHit:
    entity = hit["entity"]
    metadata = dict(entity.get("metadata") or {})
    doc_name = metadata.pop("doc_name", "")
    return SearchHit(
        chunk_id=entity["chunk_id"],
        kb_id=kb.id,
        kb_name=kb.name,
        doc_id=entity["doc_id"],
        doc_name=doc_name,
        seq=entity["seq"],
        content=entity["content"],
        score=float(hit["distance"]),
        metadata=metadata,
    )


def merge(per_kb: list[list[SearchHit]], mode: str, top_k: int) -> tuple[list[SearchHit], str]:
    """合并各知识库的结果，返回（结果，分数类型）。

    - 向量检索：所有库用同一个模型，余弦相似度可以直接比较，按分数合并。
    - 只检索一个库：保持 Milvus 给出的顺序和分数。
    - 多个库的关键字或融合检索：各库的分数来自各自的词频统计，不能直接比较，
      改为按各库内的名次合并——每个库的第 1 名同等重要，然后是各库的第 2 名，依此类推。
      同名次之间按请求中知识库的先后顺序排列。
    """
    if mode == "vector":
        hits = sorted((h for hits in per_kb for h in hits), key=lambda h: h.score, reverse=True)
        return hits[:top_k], "cosine"
    if len(per_kb) == 1:
        return per_kb[0][:top_k], "bm25" if mode == "keyword" else "rrf"

    merged: list[SearchHit] = []
    for rank in range(max((len(hits) for hits in per_kb), default=0)):
        for hits in per_kb:
            if rank < len(hits):
                merged.append(hits[rank].model_copy(update={"score": 1 / (RRF_K + rank + 1)}))
    return merged[:top_k], "rrf"
