"""文档的接口：上传、列表、切片预览、重新切分、确认入库、重试、替换、停用、删除。"""

import hashlib
import json
import logging
from pathlib import PurePath
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Query, UploadFile
from pydantic import ValidationError
from sqlalchemy import delete, func, or_, select
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
    DOC_READY,
    ORIGIN_MANUAL,
    STAGE_EMBED,
    Chunk,
    Document,
    KnowledgeBase,
)
from app.kb.schemas import ChunkOut, ChunkPage, DocumentOut, DocumentPage, DocumentUpdateIn, RechunkIn

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
_STATUS_NAMES = {
    "parsing": "解析中",
    "pending_confirm": "待确认",
    "embedding": "向量化中",
    "ready": "可用",
    "failed": "失败",
}


# ---------- 公共部分 ----------


def edited_chunk_counts(db: Session, docs: list[Document]) -> dict[str, int]:
    """各文档已入库版本中，手工修改或手工新增的切片数。"""
    if not docs:
        return {}
    rows = db.execute(
        select(Chunk.doc_id, func.count())
        .join(Document, Document.id == Chunk.doc_id)
        .where(
            Chunk.doc_id.in_([d.id for d in docs]),
            Chunk.generation == Document.generation,
            or_(Chunk.is_edited.is_(True), Chunk.origin == ORIGIN_MANUAL),
        )
        .group_by(Chunk.doc_id)
    ).all()
    return {doc_id: count for doc_id, count in rows}


def doc_out(db: Session, doc: Document, edited: int | None = None) -> DocumentOut:
    if edited is None:
        edited = edited_chunk_counts(db, [doc]).get(doc.id, 0)
    return DocumentOut.model_validate(
        {
            **{c.key: getattr(doc, c.key) for c in doc.__table__.columns},
            "searchable": doc.generation > 0 and doc.enabled,
            "replacing": doc.generation > 0 and doc.status != DOC_READY,
            "edited_chunks": edited,
        }
    )


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


def require_status(doc: Document, allowed: tuple[str, ...], action: str) -> None:
    if doc.status not in allowed:
        raise AppError(
            409,
            "INVALID_STATUS",
            f"文档当前状态是「{_STATUS_NAMES[doc.status]}」，不能{action}",
            {"status": doc.status},
        )


def _require_edits_discarded(db: Session, doc: Document, discard: bool) -> None:
    """替换或重新切分会丢掉已入库版本里的手工修改，必须由操作者明确确认。"""
    edited = edited_chunk_counts(db, [doc]).get(doc.id, 0)
    if edited and not discard:
        raise AppError(
            409,
            "EDITS_WILL_BE_LOST",
            f"这份文档有 {edited} 个手工修改或新增的切片，继续操作后它们将丢失。"
            "确认要继续请勾选「丢弃手工修改」",
            {"edited_chunks": edited},
        )


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


def validate_tags(raw: Any) -> dict[str, str]:
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


async def _read_upload(file: UploadFile) -> tuple[str, str, bytes, str]:
    """校验并读取上传的文件，返回（文件名，类型，内容，内容哈希）。"""
    name = PurePath((file.filename or "").replace("\\", "/")).name.strip()[:255]
    extension = PurePath(name).suffix.lower().lstrip(".")
    if not name or extension not in SUPPORTED_TYPES:
        hint = _UNSUPPORTED_HINTS.get(extension, "支持的格式：" + "、".join(SUPPORTED_TYPES))
        raise AppError(400, "UNSUPPORTED_FILE_TYPE", hint)
    limit = get_settings().UPLOAD_MAX_SIZE_MB * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise AppError(413, "FILE_TOO_LARGE", f"文件超过 {get_settings().UPLOAD_MAX_SIZE_MB}MB 的上限")
    if not data:
        raise AppError(400, "INVALID_FILE", "文件是空的")
    return name, extension, data, hashlib.sha256(data).hexdigest()


def _reject_duplicate(db: Session, kb_id: str, sha256: str, exclude_doc_id: str | None = None) -> None:
    query = select(Document).where(Document.kb_id == kb_id, Document.sha256 == sha256)
    if exclude_doc_id:
        query = query.where(Document.id != exclude_doc_id)
    duplicate = db.scalar(query)
    if duplicate is not None:
        raise AppError(
            409,
            "DUPLICATE_DOCUMENT",
            f"这个知识库里已有内容完全相同的文档：{duplicate.name}",
            {"document_id": duplicate.id},
        )


def _save_file(key: str, data: bytes) -> None:
    try:
        storage.put_file(key, data)
    except Exception as exc:
        logger.exception("保存原始文件失败")
        raise AppError(503, "STORAGE_UNAVAILABLE", "文件存储暂时不可用，请稍后重试") from exc


# ---------- 上传与查看 ----------


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
    config = _parse_chunk_config(chunk_config, kb.chunk_config)
    raw_tags = _parse_json_form(tags, "标签")
    tag_values = validate_tags(raw_tags) if raw_tags is not None else {}
    name, extension, data, sha256 = await _read_upload(file)
    _reject_duplicate(db, kb.id, sha256)

    doc_id = new_id()
    key = storage.storage_key(kb.id, doc_id, 1, extension)
    _save_file(key, data)
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
    return doc_out(db, doc, 0)


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
    docs = list(
        db.scalars(query.order_by(Document.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    )
    edited = edited_chunk_counts(db, docs)
    return DocumentPage(total=total, items=[doc_out(db, d, edited.get(d.id, 0)) for d in docs])


@router.get("/documents/{doc_id}", response_model=DocumentOut, summary="文档详情，可用来查询处理进度")
def get_document_detail(doc_id: str, account: CurrentAccount, db: Db) -> DocumentOut:
    doc, _ = get_document(db, account, doc_id, LEVEL_READ)
    return doc_out(db, doc)


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
    return ChunkPage(total=total, view=view, items=[chunk_out(c) for c in chunks])


def chunk_out(chunk: Chunk) -> ChunkOut:
    return ChunkOut(
        id=chunk.id,
        seq=chunk.seq,
        content=chunk.content,
        char_count=chunk.char_count,
        enabled=chunk.enabled,
        origin=chunk.origin,
        is_edited=chunk.is_edited,
        metadata=chunk.meta,
        sync_status=chunk.sync_status,
    )


# ---------- 处理流程 ----------


@router.post(
    "/documents/{doc_id}/rechunk",
    response_model=DocumentOut,
    summary="修改切分设置后重新切分",
    description="待确认或失败的文档：按新设置重新切分。已入库的文档：用原文件按新设置准备一个新版本，"
    "确认入库后替换旧版本，在此之前旧版本照常可以检索。",
)
def rechunk(doc_id: str, body: RechunkIn, account: CurrentAccount, db: Db) -> DocumentOut:
    doc, kb = get_document(db, account, doc_id, LEVEL_EDIT)
    require_status(doc, (DOC_PENDING_CONFIRM, DOC_FAILED, DOC_READY), "重新切分")
    if doc.generation > 0:
        _require_edits_discarded(db, doc, body.discard_edits)
    doc.chunk_config = body.chunk_config.model_dump()
    # 重新切分就是为了看新的切分效果，所以一律先预览再确认，不沿用上传时的「跳过确认」
    doc.auto_confirm = False
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
    return doc_out(db, doc)


@router.post(
    "/documents/{doc_id}/confirm", response_model=DocumentOut, summary="确认切片无误，开始向量化入库"
)
def confirm(doc_id: str, account: CurrentAccount, db: Db) -> DocumentOut:
    doc, kb = get_document(db, account, doc_id, LEVEL_EDIT)
    require_status(doc, (DOC_PENDING_CONFIRM,), "确认入库")
    doc.status = DOC_EMBEDDING
    log_operation(db, account.id, "doc.confirm", "document", doc.id, kb_id=kb.id, detail={"name": doc.name})
    db.commit()
    enqueue(tasks.embed_document, doc.id)
    return doc_out(db, doc)


@router.post(
    "/documents/{doc_id}/retry", response_model=DocumentOut, summary="重试失败的文档，从失败的那一步继续"
)
def retry(doc_id: str, account: CurrentAccount, db: Db) -> DocumentOut:
    doc, kb = get_document(db, account, doc_id, LEVEL_EDIT)
    require_status(doc, (DOC_FAILED,), "重试")
    stage = doc.failed_stage
    doc.status = DOC_EMBEDDING if stage == STAGE_EMBED else DOC_PARSING
    doc.failed_stage = None
    doc.error_message = None
    log_operation(db, account.id, "doc.retry", "document", doc.id, kb_id=kb.id, detail={"stage": stage})
    db.commit()
    enqueue(tasks.embed_document if stage == STAGE_EMBED else tasks.parse_document, doc.id)
    return doc_out(db, doc)


# ---------- 替换 ----------


@router.post(
    "/documents/{doc_id}/replace",
    response_model=DocumentOut,
    summary="用新文件替换已入库的文档",
    description="新文件走一遍「解析 → 待确认 → 入库」。新版本入库成功后才删除旧版本，"
    "在此之前旧版本照常可以检索；"
    "中途失败或取消，旧版本不受影响。",
)
async def replace_document(
    doc_id: str,
    account: CurrentAccount,
    db: Db,
    file: Annotated[UploadFile, File()],
    chunk_config: Annotated[
        str | None, Form(description="切分设置的 JSON；不填则沿用这份文档现在的设置")
    ] = None,
    auto_confirm: Annotated[bool, Form(description="跳过人工确认，解析后直接入库")] = False,
    discard_edits: Annotated[bool, Form(description="确认丢弃已入库版本中手工修改过的切片")] = False,
) -> DocumentOut:
    doc, kb = get_document(db, account, doc_id, LEVEL_EDIT)
    require_status(doc, (DOC_READY,), "替换")
    _require_edits_discarded(db, doc, discard_edits)
    config = _parse_chunk_config(chunk_config, doc.chunk_config)
    name, extension, data, sha256 = await _read_upload(file)
    if sha256 == doc.sha256:
        raise AppError(409, "DUPLICATE_DOCUMENT", "新文件与当前版本的内容完全相同", {"document_id": doc.id})
    _reject_duplicate(db, kb.id, sha256, exclude_doc_id=doc.id)

    key = storage.storage_key(kb.id, doc.id, doc.generation + 1, extension)
    _save_file(key, data)
    doc.pending_storage_key = key
    doc.pending_info = {"name": name, "file_type": extension, "file_size": len(data), "sha256": sha256}
    doc.chunk_config = config.model_dump()
    doc.auto_confirm = auto_confirm
    doc.status = DOC_PARSING
    doc.failed_stage = None
    doc.error_message = None
    log_operation(
        db,
        account.id,
        "doc.replace",
        "document",
        doc.id,
        kb_id=kb.id,
        detail={"old_name": doc.name, "new_name": name, "size": len(data)},
    )
    db.commit()
    enqueue(tasks.parse_document, doc.id)
    return doc_out(db, doc)


@router.post(
    "/documents/{doc_id}/cancel-replace",
    response_model=DocumentOut,
    summary="放弃正在准备的新版本，文档回到已入库的版本",
)
def cancel_replace(doc_id: str, account: CurrentAccount, db: Db) -> DocumentOut:
    doc, kb = get_document(db, account, doc_id, LEVEL_EDIT)
    if doc.generation == 0:
        raise AppError(409, "INVALID_STATUS", "这份文档还没有入库过，没有可以回到的版本；不需要它请直接删除")
    require_status(doc, (DOC_PENDING_CONFIRM, DOC_FAILED), "取消")
    pending_key = doc.pending_storage_key
    db.execute(delete(Chunk).where(Chunk.doc_id == doc.id, Chunk.generation == doc.generation + 1))
    doc.pending_storage_key = None
    doc.pending_info = None
    doc.status = DOC_READY
    doc.failed_stage = None
    doc.error_message = None
    doc.chunk_count = (
        db.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.doc_id == doc.id, Chunk.generation == doc.generation)
        )
        or 0
    )
    log_operation(
        db, account.id, "doc.cancel_replace", "document", doc.id, kb_id=kb.id, detail={"name": doc.name}
    )
    db.commit()
    if pending_key:
        try:
            storage.remove_file(pending_key)
        except Exception as exc:
            logger.warning("删除未采用的新文件 %s 失败，不影响使用：%s", pending_key, exc)
    return doc_out(db, doc)


# ---------- 停用、标签、删除 ----------


@router.patch("/documents/{doc_id}", response_model=DocumentOut, summary="停用或启用文档，修改标签")
def update_document(doc_id: str, body: DocumentUpdateIn, account: CurrentAccount, db: Db) -> DocumentOut:
    doc, kb = get_document(db, account, doc_id, LEVEL_EDIT)
    if doc.status in _BUSY:
        require_status(doc, (), "修改，请等处理结束后再试")
    changes: dict[str, Any] = {}
    if body.enabled is not None and body.enabled != doc.enabled:
        changes["enabled"] = body.enabled
    if body.tags is not None and validate_tags(body.tags) != doc.tags:
        changes["tags"] = body.tags
    if not changes:
        return doc_out(db, doc)

    before = {name: getattr(doc, name) for name in changes}
    enabled = changes.get("enabled", doc.enabled)
    tags = changes.get("tags", doc.tags)
    # 标签存在每个切片的元数据里，已入库的和正在准备的都要改
    if "tags" in changes:
        for chunk in db.scalars(select(Chunk).where(Chunk.doc_id == doc.id)):
            chunk.meta = {**chunk.meta, "tags": tags}
    active = list(db.scalars(select(Chunk).where(Chunk.doc_id == doc.id, Chunk.generation == doc.generation)))
    if doc.generation > 0 and active:
        rows = []
        for chunk in active:
            row: dict[str, Any] = {"chunk_id": chunk.id, "enabled": chunk.enabled and enabled}
            if "tags" in changes:
                row["metadata"] = {**chunk.meta, "doc_name": doc.name}
            rows.append(row)
        try:
            vector_store.update_fields(kb.collection_name, rows)
        except Exception as exc:
            db.rollback()
            logger.exception("更新向量库失败")
            raise AppError(503, "VECTOR_STORE_UNAVAILABLE", "向量库暂时不可用，修改未保存") from exc
    doc.enabled = enabled
    doc.tags = tags
    log_operation(
        db,
        account.id,
        "doc.update",
        "document",
        doc.id,
        kb_id=kb.id,
        detail={"name": doc.name, "before": before, "after": changes},
    )
    db.commit()
    return doc_out(db, doc)


@router.delete("/documents/{doc_id}", summary="删除文档及其切片和原始文件，不可恢复")
def delete_document(doc_id: str, account: CurrentAccount, db: Db) -> dict:
    doc, kb = get_document(db, account, doc_id, LEVEL_EDIT)
    if doc.status in _BUSY:
        require_status(doc, (), "删除，请等处理结束后再试")
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
