"""召回接口（/open/v1，密钥鉴权）与管理界面的召回测试（/api/v1，登录凭证鉴权）。"""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import CurrentAccount, Db, KeyAccount
from app.auth.models import Account
from app.auth.permissions import effective_levels
from app.common.errors import AppError
from app.kb.models import KB_DELETING, Chunk, Document, KnowledgeBase
from app.retrieval import service
from app.retrieval.schemas import NeighborsOut, OpenChunkOut, OpenKbOut, SearchIn, SearchOut

open_router = APIRouter(tags=["召回接口"])
console_router = APIRouter(tags=["召回测试"])

MAX_NEIGHBORS = 5


@open_router.post(
    "/retrieval/search",
    response_model=SearchOut,
    summary="检索",
    description="在指定的知识库中检索。必须明确给出知识库；只要其中一个不存在或没有只读权限，"
    "整个请求返回 403 并列出是哪些。平台不做重排序，返回的是召回的候选。",
)
def search(body: SearchIn, request: Request, account: KeyAccount, db: Db) -> SearchOut:
    return service.search(db, account, body, getattr(request.state, "api_key_id", None))


@console_router.post("/recall-test", response_model=SearchOut, summary="召回测试，参数与召回接口相同")
def recall_test(body: SearchIn, account: CurrentAccount, db: Db) -> SearchOut:
    return service.search(db, account, body, None)


@open_router.get("/knowledge-bases", response_model=list[OpenKbOut], summary="这把密钥可以检索的知识库")
def list_kbs(account: KeyAccount, db: Db) -> list[OpenKbOut]:
    levels = effective_levels(db, account)
    if not levels:
        return []
    kbs = db.scalars(
        select(KnowledgeBase)
        .where(KnowledgeBase.id.in_(levels), KnowledgeBase.status != KB_DELETING)
        .order_by(KnowledgeBase.name)
    ).all()
    counts = dict(
        db.execute(
            select(Document.kb_id, func.count()).where(Document.kb_id.in_(levels)).group_by(Document.kb_id)
        ).all()
    )
    return [
        OpenKbOut(
            id=kb.id,
            name=kb.name,
            description=kb.description,
            my_level=levels[kb.id],
            document_count=counts.get(kb.id, 0),
        )
        for kb in kbs
    ]


def _require_kb(db: Session, account: Account, kb_id: str) -> None:
    kb = db.get(KnowledgeBase, kb_id) if kb_id in effective_levels(db, account, [kb_id]) else None
    if kb is None or kb.status == KB_DELETING:
        raise AppError(403, "KB_FORBIDDEN", "以下知识库不存在或没有权限", {"kb_ids": [kb_id]})


def _visible_chunks(kb_id: str):
    """可被调用方看到的切片：属于该知识库、所属文档启用、是已入库的版本、切片本身启用。"""
    return (
        select(Chunk, Document.name)
        .join(Document, Document.id == Chunk.doc_id)
        .where(
            Chunk.kb_id == kb_id,
            Chunk.generation == Document.generation,
            Chunk.enabled.is_(True),
            Document.enabled.is_(True),
        )
    )


def _chunk_out(chunk: Chunk, doc_name: str) -> OpenChunkOut:
    return OpenChunkOut(
        chunk_id=chunk.id,
        kb_id=chunk.kb_id,
        doc_id=chunk.doc_id,
        doc_name=doc_name,
        seq=chunk.seq,
        content=chunk.content,
        metadata=chunk.meta,
    )


def _get_chunk(db: Session, kb_id: str, chunk_id: str) -> tuple[Chunk, str]:
    row = db.execute(_visible_chunks(kb_id).where(Chunk.id == chunk_id)).first()
    if row is None:
        raise AppError(404, "NOT_FOUND", "切片不存在")
    return row[0], row[1]


@open_router.get(
    "/knowledge-bases/{kb_id}/chunks/{chunk_id}", response_model=OpenChunkOut, summary="按切片 ID 取原文"
)
def get_chunk(kb_id: str, chunk_id: str, account: KeyAccount, db: Db) -> OpenChunkOut:
    _require_kb(db, account, kb_id)
    return _chunk_out(*_get_chunk(db, kb_id, chunk_id))


@open_router.get(
    "/knowledge-bases/{kb_id}/chunks/{chunk_id}/neighbors",
    response_model=NeighborsOut,
    summary="取同一文档中前后相邻的切片，用于补充上下文",
)
def get_neighbors(
    kb_id: str,
    chunk_id: str,
    account: KeyAccount,
    db: Db,
    before: Annotated[int, Query(ge=0, le=MAX_NEIGHBORS)] = 1,
    after: Annotated[int, Query(ge=0, le=MAX_NEIGHBORS)] = 1,
) -> NeighborsOut:
    _require_kb(db, account, kb_id)
    chunk, doc_name = _get_chunk(db, kb_id, chunk_id)
    same_doc = _visible_chunks(kb_id).where(Chunk.doc_id == chunk.doc_id)
    # 按序号取紧邻的若干个；中间有停用的切片时跳过它们
    previous = db.execute(
        same_doc.where(Chunk.seq < chunk.seq).order_by(Chunk.seq.desc()).limit(before)
    ).all()
    following = db.execute(same_doc.where(Chunk.seq > chunk.seq).order_by(Chunk.seq).limit(after)).all()
    return NeighborsOut(
        before=[_chunk_out(c, name) for c, name in reversed(previous)],
        chunk=_chunk_out(chunk, doc_name),
        after=[_chunk_out(c, name) for c, name in following],
    )
