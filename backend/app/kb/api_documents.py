"""文档的接口：上传、列表、切片预览、重新切分、确认入库、重试、删除。"""

import hashlib
import json
import logging
from pathlib import PurePath
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Query, UploadFile
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.audit.service import log_operation
from app.auth.deps import CurrentAccount, Db
from app.auth.models import LEVEL_EDIT, LEVEL_READ, Account
from app.common.clients import enqueue
from app.common.db import new_id
from app.common.errors import AppError
from app.config import get_settings
from app.ingest import storage, tasks, vector_store
from app.ingest.chunking import ChunkConfig
from app.ingest.parsers import SUPPORTED_TYPES
from app.kb.api_kb import get_kb
from app.kb.models import (
    DOC_EMBEDDING,
    DOC_FAILED,
    DOC_PARSING,
    DOC_PENDING_CONFIRM,
    STAGE_EMBED,
    Chunk,
    Document,
    KnowledgeBase,
)
from app.kb.schemas import ChunkOut, ChunkPage, DocumentOut, DocumentPage, RechunkIn

logger = logging.getLogger(__name__)
router = APIRouter(tags=["文档"])

_UNSUPPORTED_HINTS = {
    "doc": "不支持旧的 doc 格式，请另存为 docx 后上传",
    "xls": "不支持旧的 xls 格式，请另存为 xlsx 后上传",
    "ppt": "暂不支持演示文稿",
    "pptx": "暂不支持演示文稿",
    "jpg": "暂不支持图片",
    "jpeg": "暂不支持图片",
    "png": "暂不支持图片",
}
_BUSY = (DOC_PARSING, DOC_EMBEDDING)


def _doc_out(doc: Document) -> DocumentOut:
    out = DocumentOut.model_validate(
        {**{c.key: getattr(doc, c.key) for c in doc.__table__.columns}, "searchable": False}
    )
    out.searchable = doc.generation > 0 and doc.enabled
    return out


def get_document(db: Session, account: Account, doc_id: str, required: str) -> tuple[Document, KnowledgeBase]:
    """取文档并按它所属的知识库校验权限。无权限时与文档不存在的表现相同。"""
    doc = db.get(Document, doc_id)
    if doc is None:
        raise AppError(404, "NOT_FOUND", "文档不存在")
    try:
        kb, _ = get_kb(db, account, doc.kb_id, required)
    except AppError as exc:
        if exc.status == 404:
            raise AppError(404, "NOT_FOUND", "文档不存在") from exc
        raise
    return doc, kb


def _parse_json_form(text: str | None, what: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError as exc:
        raise AppError(400, "INVALID_PARAM", f"{what}不是合法的 JSON") from exc


def _parse_chunk_config(text: str | None, default: dict[str, Any]) -> ChunkConfig:
    raw = _parse_json_form(text, "切分设置")
    base = {k: v for k, v in default.items() if k != "effective_mode"}
    try:
        return ChunkConfig.model_validate(raw if raw is not None else base)
    except ValidationError as exc:
        reasons = "；".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise AppError(400, "INVALID_PARAM", f"切分设置不正确：{reasons}") from exc


def _parse_tags(text: str | None) -> dict[str, str]:
    raw = _parse_json_form(text, "标签")
    if raw is None:
        return {}
    ok = (
        isinstance(raw, dict)
        and len(raw) <= 20
        and all(
            isinstance(k, str) and 0 < len(k) <= 32 and isinstance(v, str) and len(v) <= 128
            for k, v in raw.items()
        )
    )
    if not ok:
        raise AppError(
            400, "INVALID_PARAM", "标签应为键值对，最多 20 个，键不超过 32 个字，值不超过 128 个字"
        )
    return raw


async def _read_limited(file: UploadFile) -> bytes:
    limit = get_settings().UPLOAD_MAX_SIZE_MB * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise AppError(413, "FILE_TOO_LARGE", f"文件超过 {get_settings().UPLOAD_MAX_SIZE_MB}MB 的上限")
    if not data:
        raise AppError(400, "INVALID_FILE", "文件是空的")
    return data


@router.post(
    "/knowledge-bases/{kb_id}/documents",
    response_model=DocumentOut,
    summary="上传文档",
    description="支持 txt、md、docx、文字版 PDF、xlsx、csv。上传后在后台解析和切分，"
    "完成后状态变为「待确认」，预览切片无误后调用确认接口入库；勾选跳过确认则解析后直接入库。",
)
async def upload_document(
    kb_id: str,
    account: CurrentAccount,
    db: Db,
    file: Annotated[UploadFile, File()],
    chunk_config: Annotated[
        str | None, Form(description="切分设置的 JSON；不填则使用知识库的默认设置")
    ] = None,
    tags: Annotated[str | None, Form(description='自定义标签的 JSON，如 {"产品线": "划线机"}')] = None,
    auto_confirm: Annotated[bool, Form(description="跳过人工确认，解析后直接入库")] = False,
) -> DocumentOut:
    kb, _ = get_kb(db, account, kb_id, LEVEL_EDIT)
    name = PurePath((file.filename or "").replace("\\", "/")).name.strip()[:255]
    extension = PurePath(name).suffix.lower().lstrip(".")
    if not name or extension not in SUPPORTED_TYPES:
        hint = _UNSUPPORTED_HINTS.get(extension, "支持的格式：" + "、".join(SUPPORTED_TYPES))
        raise AppError(400, "UNSUPPORTED_FILE_TYPE", hint)
    config = _parse_chunk_config(chunk_config, kb.chunk_config)
    tag_values = _parse_tags(tags)
    data = await _read_limited(file)

    sha256 = hashlib.sha256(data).hexdigest()
    duplicate = db.scalar(select(Document).where(Document.kb_id == kb.id, Document.sha256 == sha256))
    if duplicate is not None:
        raise AppError(
            409,
            "DUPLICATE_DOCUMENT",
            f"这个知识库里已有内容完全相同的文档：{duplicate.name}",
            {"document_id": duplicate.id},
        )

    doc_id = new_id()
    key = storage.storage_key(kb.id, doc_id, 1, extension)
    try:
        storage.put_file(key, data)
    except Exception as exc:
        logger.exception("保存原始文件失败")
        raise AppError(503, "STORAGE_UNAVAILABLE", "文件存储暂时不可用，请稍后重试") from exc

    doc = Document(
        id=doc_id,
        kb_id=kb.id,
        name=name,
        file_type=extension,
        file_size=len(data),
        storage_key=key,
        sha256=sha256,
        status=DOC_PARSING,
        auto_confirm=auto_confirm,
        chunk_config=config.model_dump(),
        tags=tag_values,
        created_by=account.id,
    )
    db.add(doc)
    log_operation(
        db,
        account.id,
        "doc.upload",
        "document",
        doc.id,
        kb_id=kb.id,
        detail={"name": name, "size": len(data)},
    )
    db.commit()
    enqueue(tasks.parse_document, doc.id)
    return _doc_out(doc)


@router.get("/knowledge-bases/{kb_id}/documents", response_model=DocumentPage, summary="知识库的文档列表")
def list_documents(
    kb_id: str,
    account: CurrentAccount,
    db: Db,
    keyword: str = "",
    status: Literal["parsing", "pending_confirm", "embedding", "ready", "failed"] | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DocumentPage:
    kb, _ = get_kb(db, account, kb_id, LEVEL_READ)
    query = select(Document).where(Document.kb_id == kb.id)
    if keyword.strip():
        escaped = keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(Document.name.ilike(f"%{escaped}%"))
    if status:
        query = query.where(Document.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    docs = db.scalars(
        query.order_by(Document.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return DocumentPage(total=total, items=[_doc_out(d) for d in docs])


@router.get("/documents/{doc_id}", response_model=DocumentOut, summary="文档详情，可用来查询处理进度")
def get_document_detail(doc_id: str, account: CurrentAccount, db: Db) -> DocumentOut:
    doc, _ = get_document(db, account, doc_id, LEVEL_READ)
    return _doc_out(doc)


@router.get(
    "/documents/{doc_id}/chunks",
    response_model=ChunkPage,
    summary="文档的切片",
    description="文档有尚未入库的新版本时（待确认、向量化中、失败），默认显示新版本的切片用于预览；"
    "否则显示已入库的切片。可用 view 参数指定。",
)
def list_chunks(
    doc_id: str,
    account: CurrentAccount,
    db: Db,
    view: Literal["pending", "active"] | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ChunkPage:
    doc, _ = get_document(db, account, doc_id, LEVEL_READ)
    pending_generation = doc.generation + 1
    if view is None:
        has_pending = db.scalar(
            select(Chunk.id).where(Chunk.doc_id == doc.id, Chunk.generation == pending_generation).limit(1)
        )
        view = "pending" if has_pending else "active"
    generation = pending_generation if view == "pending" else doc.generation
    query = select(Chunk).where(Chunk.doc_id == doc.id, Chunk.generation == generation)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    chunks = db.scalars(query.order_by(Chunk.seq).offset((page - 1) * page_size).limit(page_size))
    items = [
        ChunkOut(
            id=c.id,
            seq=c.seq,
            content=c.content,
            char_count=c.char_count,
            enabled=c.enabled,
            origin=c.origin,
            is_edited=c.is_edited,
            metadata=c.meta,
            sync_status=c.sync_status,
        )
        for c in chunks
    ]
    return ChunkPage(total=total, view=view, items=items)


def _require_status(doc: Document, allowed: tuple[str, ...], action: str) -> None:
    if doc.status not in allowed:
        names = {
            "parsing": "解析中",
            "pending_confirm": "待确认",
            "embedding": "向量化中",
            "ready": "可用",
            "failed": "失败",
        }
        raise AppError(
            409,
            "INVALID_STATUS",
            f"文档当前状态是「{names[doc.status]}」，不能{action}",
            {"status": doc.status},
        )


@router.post(
    "/documents/{doc_id}/rechunk",
    response_model=DocumentOut,
    summary="修改切分设置后重新切分（待确认或失败状态下可用）",
)
def rechunk(doc_id: str, body: RechunkIn, account: CurrentAccount, db: Db) -> DocumentOut:
    doc, kb = get_document(db, account, doc_id, LEVEL_EDIT)
    _require_status(doc, (DOC_PENDING_CONFIRM, DOC_FAILED), "重新切分")
    doc.chunk_config = body.chunk_config.model_dump()
    doc.status = DOC_PARSING
    doc.failed_stage = None
    doc.error_message = None
    log_operation(
        db,
        account.id,
        "doc.rechunk",
        "document",
        doc.id,
        kb_id=kb.id,
        detail={"chunk_config": doc.chunk_config},
    )
    db.commit()
    enqueue(tasks.parse_document, doc.id)
    return _doc_out(doc)


@router.post(
    "/documents/{doc_id}/confirm", response_model=DocumentOut, summary="确认切片无误，开始向量化入库"
)
def confirm(doc_id: str, account: CurrentAccount, db: Db) -> DocumentOut:
    doc, kb = get_document(db, account, doc_id, LEVEL_EDIT)
    _require_status(doc, (DOC_PENDING_CONFIRM,), "确认入库")
    doc.status = DOC_EMBEDDING
    log_operation(db, account.id, "doc.confirm", "document", doc.id, kb_id=kb.id, detail={"name": doc.name})
    db.commit()
    enqueue(tasks.embed_document, doc.id)
    return _doc_out(doc)


@router.post(
    "/documents/{doc_id}/retry", response_model=DocumentOut, summary="重试失败的文档，从失败的那一步继续"
)
def retry(doc_id: str, account: CurrentAccount, db: Db) -> DocumentOut:
    doc, kb = get_document(db, account, doc_id, LEVEL_EDIT)
    _require_status(doc, (DOC_FAILED,), "重试")
    stage = doc.failed_stage
    doc.status = DOC_EMBEDDING if stage == STAGE_EMBED else DOC_PARSING
    doc.failed_stage = None
    doc.error_message = None
    log_operation(db, account.id, "doc.retry", "document", doc.id, kb_id=kb.id, detail={"stage": stage})
    db.commit()
    enqueue(tasks.embed_document if stage == STAGE_EMBED else tasks.parse_document, doc.id)
    return _doc_out(doc)


@router.delete("/documents/{doc_id}", summary="删除文档及其切片和原始文件，不可恢复")
def delete_document(doc_id: str, account: CurrentAccount, db: Db) -> dict:
    doc, kb = get_document(db, account, doc_id, LEVEL_EDIT)
    if doc.status in _BUSY:
        _require_status(doc, (), "删除，请等处理结束后再试")
    try:
        vector_store.delete_document(kb.collection_name, doc.id)
    except Exception as exc:
        logger.exception("从向量库删除文档失败")
        raise AppError(503, "VECTOR_STORE_UNAVAILABLE", "向量库暂时不可用，文档未删除") from exc
    chunks = db.execute(delete(Chunk).where(Chunk.doc_id == doc.id)).rowcount
    log_operation(
        db,
        account.id,
        "doc.delete",
        "document",
        doc.id,
        kb_id=kb.id,
        detail={"name": doc.name, "chunks": chunks},
    )
    db.delete(doc)
    db.commit()
    try:
        storage.remove_prefix(f"kb/{kb.id}/{doc.id}/")
    except Exception as exc:
        # 文件残留不影响检索结果，只占空间；记录下来即可
        logger.warning("删除文档 %s 的原始文件失败：%s", doc.id, exc)
    return {"removed_chunks": chunks}
