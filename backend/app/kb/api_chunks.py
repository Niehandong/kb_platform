"""切片的手工维护：修改正文、停用启用、新增、删除。

可以维护两种切片：
- 已入库的（文档状态为「可用」）：修改立刻同步到 Milvus，下一次检索就生效。
- 待确认的（文档状态为「待确认」）：只改数据库，确认入库时按改过的内容处理。
文档在解析中、向量化中，或已入库但正在准备新版本时，不能维护切片。
"""

import logging
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import log_operation
from app.auth.deps import CurrentAccount, Db
from app.auth.models import LEVEL_EDIT, Account
from app.common.db import new_id
from app.common.errors import AppError
from app.ingest import vector_store
from app.ingest.embedding import EmbeddingError, embed_query
from app.kb.api_documents import chunk_out, get_document
from app.kb.models import (
    DOC_PENDING_CONFIRM,
    DOC_READY,
    ORIGIN_MANUAL,
    SYNC_PENDING,
    SYNC_SYNCED,
    Chunk,
    Document,
    KnowledgeBase,
)
from app.kb.schemas import ChunkCreateIn, ChunkOut, ChunkUpdateIn

logger = logging.getLogger(__name__)
router = APIRouter(tags=["切片"])

ACTIVE = "active"
PENDING = "pending"
_LOG_TEXT_LIMIT = 500


def _scope(doc: Document, generation: int) -> str:
    """这个版本的切片现在能不能维护；能的话是已入库的还是待确认的。"""
    if doc.status == DOC_READY and generation == doc.generation:
        return ACTIVE
    if doc.status == DOC_PENDING_CONFIRM and generation == doc.generation + 1:
        return PENDING
    if doc.status == DOC_PENDING_CONFIRM:
        raise AppError(
            409,
            "INVALID_STATUS",
            "这份文档正在准备新版本，已入库的切片暂时不能修改；请先确认入库或取消新版本",
        )
    raise AppError(409, "INVALID_STATUS", "文档正在处理或处理失败，暂时不能修改切片", {"status": doc.status})


def _load(db: Session, account: Account, chunk_id: str) -> tuple[Chunk, Document, KnowledgeBase, str]:
    chunk = db.get(Chunk, chunk_id)
    if chunk is None:
        raise AppError(404, "NOT_FOUND", "切片不存在")
    try:
        doc, kb = get_document(db, account, chunk.doc_id, LEVEL_EDIT)
    except AppError as exc:
        if exc.status == 404:
            raise AppError(404, "NOT_FOUND", "切片不存在") from exc
        raise
    return chunk, doc, kb, _scope(doc, chunk.generation)


def _clean(content: str) -> str:
    content = content.strip()
    if not vector_store.search_tokens(content):
        raise AppError(400, "INVALID_PARAM", "切片正文没有可检索的内容")
    return content


def _vector(doc: Document, chunk_meta: dict[str, Any], content: str) -> list[float]:
    try:
        return embed_query(vector_store.embedding_text(doc.name, chunk_meta.get("heading_path"), content))
    except EmbeddingError as exc:
        logger.warning("切片向量化失败：%s", exc)
        raise AppError(503, "EMBEDDING_UNAVAILABLE", "向量模型服务暂时不可用，修改未保存") from exc


def _milvus(action, db: Session) -> None:
    """执行一次对 Milvus 的写入。失败时回滚数据库里尚未提交的修改，两边保持一致。"""
    try:
        action()
    except Exception as exc:
        db.rollback()
        logger.exception("写入向量库失败")
        raise AppError(503, "VECTOR_STORE_UNAVAILABLE", "向量库暂时不可用，修改未保存") from exc


def _row(chunk: Chunk, doc: Document, vector: list[float]) -> dict[str, Any]:
    return vector_store.build_row(
        chunk_id=chunk.id,
        doc_id=doc.id,
        seq=chunk.seq,
        content=chunk.content,
        doc_name=doc.name,
        metadata=chunk.meta,
        enabled=chunk.enabled and doc.enabled,
        vector=vector,
    )


def _shift(db: Session, doc: Document, generation: int, from_seq: int, delta: int) -> list[dict[str, Any]]:
    """把序号不小于 from_seq 的切片整体前移或后移，返回它们在 Milvus 中需要更新的序号。"""
    moved = list(
        db.scalars(
            select(Chunk).where(Chunk.doc_id == doc.id, Chunk.generation == generation, Chunk.seq >= from_seq)
        )
    )
    for chunk in moved:
        chunk.seq += delta
    return [{"chunk_id": c.id, "seq": c.seq} for c in moved]


@router.patch("/chunks/{chunk_id}", response_model=ChunkOut, summary="修改切片正文，或停用、启用切片")
def update_chunk(chunk_id: str, body: ChunkUpdateIn, account: CurrentAccount, db: Db) -> ChunkOut:
    chunk, doc, kb, scope = _load(db, account, chunk_id)
    detail: dict[str, Any] = {"document": doc.name, "seq": chunk.seq}

    content_changed = body.content is not None and _clean(body.content) != chunk.content
    enabled_changed = body.enabled is not None and body.enabled != chunk.enabled
    if not (content_changed or enabled_changed):
        return chunk_out(chunk)

    if content_changed:
        assert body.content is not None
        new_content = _clean(body.content)
        detail["before"] = chunk.content[:_LOG_TEXT_LIMIT]
        detail["after"] = new_content[:_LOG_TEXT_LIMIT]
        chunk.content = new_content
        chunk.char_count = len(new_content)
        chunk.is_edited = True
    if enabled_changed:
        assert body.enabled is not None
        detail["enabled"] = body.enabled
        chunk.enabled = body.enabled
    chunk.updated_by = account.id

    if scope == ACTIVE:
        if content_changed:
            # 正文变了要重新向量化，整行覆盖
            row = _row(chunk, doc, _vector(doc, chunk.meta, chunk.content))
            _milvus(lambda: vector_store.upsert_rows(kb.collection_name, [row]), db)
        else:
            fields = [{"chunk_id": chunk.id, "enabled": chunk.enabled and doc.enabled}]
            _milvus(lambda: vector_store.update_fields(kb.collection_name, fields), db)
        chunk.sync_status = SYNC_SYNCED
    log_operation(db, account.id, "chunk.update", "chunk", chunk.id, kb_id=kb.id, detail=detail)
    db.commit()
    return chunk_out(chunk)


@router.post("/documents/{doc_id}/chunks", response_model=ChunkOut, summary="手工新增一个切片")
def create_chunk(doc_id: str, body: ChunkCreateIn, account: CurrentAccount, db: Db) -> ChunkOut:
    doc, kb = get_document(db, account, doc_id, LEVEL_EDIT)
    # 文档待确认时加到正在准备的版本里，否则加到已入库的版本里
    generation = doc.generation + 1 if doc.status == DOC_PENDING_CONFIRM else doc.generation
    scope = _scope(doc, generation)
    content = _clean(body.content)

    count = (
        db.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.doc_id == doc.id, Chunk.generation == generation)
        )
        or 0
    )
    position = count if body.position is None else min(body.position, count)
    moved = _shift(db, doc, generation, position, +1)
    chunk = Chunk(
        id=new_id(),
        kb_id=kb.id,
        doc_id=doc.id,
        generation=generation,
        seq=position,
        content=content,
        char_count=len(content),
        enabled=True,
        is_edited=False,
        origin=ORIGIN_MANUAL,
        meta={"heading_path": None, "page": None, "tags": doc.tags},
        sync_status=SYNC_PENDING,
        updated_by=account.id,
    )
    db.add(chunk)
    db.flush()
    doc.chunk_count = count + 1

    if scope == ACTIVE:
        row = _row(chunk, doc, _vector(doc, chunk.meta, content))

        def write() -> None:
            vector_store.insert_rows(kb.collection_name, [row])
            if moved:
                vector_store.update_fields(kb.collection_name, moved)

        _milvus(write, db)
        chunk.sync_status = SYNC_SYNCED
    log_operation(
        db,
        account.id,
        "chunk.create",
        "chunk",
        chunk.id,
        kb_id=kb.id,
        detail={"document": doc.name, "seq": position, "content": content[:_LOG_TEXT_LIMIT]},
    )
    db.commit()
    return chunk_out(chunk)


@router.delete("/chunks/{chunk_id}", summary="删除切片，不可恢复")
def delete_chunk(chunk_id: str, account: CurrentAccount, db: Db) -> dict:
    chunk, doc, kb, scope = _load(db, account, chunk_id)
    remaining = (
        db.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.doc_id == doc.id, Chunk.generation == chunk.generation, Chunk.id != chunk.id)
        )
        or 0
    )
    if remaining == 0:
        raise AppError(400, "LAST_CHUNK", "这是文档的最后一个切片，不能删除；不需要这份文档请直接删除文档")

    detail = {"document": doc.name, "seq": chunk.seq, "content": chunk.content[:_LOG_TEXT_LIMIT]}
    removed_id, removed_seq, generation = chunk.id, chunk.seq, chunk.generation
    db.delete(chunk)
    db.flush()
    moved = _shift(db, doc, generation, removed_seq + 1, -1)
    doc.chunk_count = remaining

    if scope == ACTIVE:

        def write() -> None:
            vector_store.delete_chunks(kb.collection_name, [removed_id])
            if moved:
                vector_store.update_fields(kb.collection_name, moved)

        _milvus(write, db)
    log_operation(db, account.id, "chunk.delete", "chunk", removed_id, kb_id=kb.id, detail=detail)
    db.commit()
    return {"removed": 1}
