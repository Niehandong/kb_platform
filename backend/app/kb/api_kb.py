"""知识库与授权的接口。"""

import logging

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import log_operation
from app.auth.deps import CurrentAccount, Db
from app.auth.models import (
    LEVEL_EDIT,
    LEVEL_MANAGE,
    LEVEL_READ,
    SUBJECT_ACCOUNT,
    SUBJECT_GROUP,
    Account,
    KbGrant,
    UserGroup,
)
from app.auth.permissions import effective_levels, require_kb_level
from app.common.clients import enqueue
from app.common.db import new_id
from app.common.errors import AppError
from app.config import get_settings
from app.ingest import tasks, vector_store
from app.kb.models import KB_ACTIVE, KB_DELETING, KB_REBUILDING, Document, KnowledgeBase
from app.kb.schemas import GrantIn, GrantOut, KbCreateIn, KbOut, KbUpdateIn

logger = logging.getLogger(__name__)
router = APIRouter(tags=["知识库"])


def get_kb(db: Session, account: Account, kb_id: str, required: str) -> tuple[KnowledgeBase, str]:
    """取知识库并校验权限，返回（知识库，当前账号的档位）。正在删除的知识库视为不存在。"""
    level = require_kb_level(db, account, kb_id, required)
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or kb.status == KB_DELETING:
        raise AppError(404, "NOT_FOUND", "知识库不存在")
    return kb, level


def _out(kb: KnowledgeBase, level: str, document_count: int) -> KbOut:
    return KbOut(
        id=kb.id,
        name=kb.name,
        description=kb.description,
        is_public=kb.is_public,
        status=kb.status,
        embedding_model=kb.embedding_model,
        chunk_config=kb.chunk_config,
        my_level=level,  # type: ignore[arg-type]
        document_count=document_count,
        rebuild_error=kb.rebuild_error,
        needs_rebuild=kb.embedding_model != get_settings().EMBEDDING_MODEL
        or kb.embedding_dim != get_settings().EMBEDDING_DIM,
        created_at=kb.created_at,
    )


def _document_count(db: Session, kb_id: str) -> int:
    return db.scalar(select(func.count()).select_from(Document).where(Document.kb_id == kb_id)) or 0


@router.get("/knowledge-bases", response_model=list[KbOut], summary="我能访问的知识库")
def list_kbs(account: CurrentAccount, db: Db) -> list[KbOut]:
    levels = effective_levels(db, account)
    if not levels:
        return []
    kbs = db.scalars(
        select(KnowledgeBase)
        .where(KnowledgeBase.id.in_(levels), KnowledgeBase.status != KB_DELETING)
        .order_by(KnowledgeBase.created_at.desc())
    ).all()
    counts = dict(
        db.execute(
            select(Document.kb_id, func.count()).where(Document.kb_id.in_(levels)).group_by(Document.kb_id)
        ).all()
    )
    return [_out(kb, levels[kb.id], counts.get(kb.id, 0)) for kb in kbs]


@router.post("/knowledge-bases", response_model=KbOut, summary="创建知识库，创建者自动获得管理权限")
def create_kb(body: KbCreateIn, account: CurrentAccount, db: Db) -> KbOut:
    if not (account.is_admin or account.can_create_kb):
        raise AppError(403, "FORBIDDEN", "你没有创建知识库的权限，请联系平台管理员开通")
    settings = get_settings()
    kb_id = new_id()
    kb = KnowledgeBase(
        id=kb_id,
        name=body.name.strip(),
        description=body.description.strip(),
        is_public=body.is_public,
        collection_name=vector_store.collection_name(kb_id),
        embedding_model=settings.EMBEDDING_MODEL,
        embedding_dim=settings.EMBEDDING_DIM,
        chunk_config=body.chunk_config.model_dump(),
        status=KB_ACTIVE,
        created_by=account.id,
    )
    db.add(kb)
    db.flush()
    db.add(
        KbGrant(
            kb_id=kb.id,
            subject_type=SUBJECT_ACCOUNT,
            subject_id=account.id,
            level=LEVEL_MANAGE,
            granted_by=account.id,
        )
    )
    log_operation(db, account.id, "kb.create", "knowledge_base", kb.id, kb_id=kb.id, detail={"name": kb.name})
    try:
        vector_store.create_collection(kb.collection_name, kb.embedding_dim)
    except Exception as exc:
        db.rollback()
        logger.exception("创建 Milvus 集合失败")
        raise AppError(503, "VECTOR_STORE_UNAVAILABLE", "向量库暂时不可用，知识库未创建") from exc
    db.commit()
    return _out(kb, LEVEL_MANAGE, 0)


@router.get("/knowledge-bases/{kb_id}", response_model=KbOut, summary="知识库详情")
def get_kb_detail(kb_id: str, account: CurrentAccount, db: Db) -> KbOut:
    kb, level = get_kb(db, account, kb_id, LEVEL_READ)
    return _out(kb, level, _document_count(db, kb.id))


@router.patch(
    "/knowledge-bases/{kb_id}",
    response_model=KbOut,
    summary="修改知识库",
    description="修改默认切分设置需要「编辑」权限；修改名称、描述、是否公开需要「管理」权限。",
)
def update_kb(kb_id: str, body: KbUpdateIn, account: CurrentAccount, db: Db) -> KbOut:
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    needs_manage = any(name in changes for name in ("name", "description", "is_public"))
    kb, level = get_kb(db, account, kb_id, LEVEL_MANAGE if needs_manage else LEVEL_EDIT)
    before = {name: getattr(kb, name) for name in changes}
    for name, value in changes.items():
        setattr(kb, name, value.strip() if isinstance(value, str) else value)
    if changes and before != changes:
        log_operation(
            db,
            account.id,
            "kb.update",
            "knowledge_base",
            kb.id,
            kb_id=kb.id,
            detail={"before": before, "after": changes},
        )
        db.commit()
    return _out(kb, level, _document_count(db, kb.id))


@router.delete("/knowledge-bases/{kb_id}", summary="删除知识库及其全部文档、切片和原始文件，不可恢复")
def delete_kb(kb_id: str, account: CurrentAccount, db: Db) -> dict:
    kb, _ = get_kb(db, account, kb_id, LEVEL_MANAGE)
    documents = _document_count(db, kb.id)
    kb.status = KB_DELETING
    log_operation(
        db,
        account.id,
        "kb.delete",
        "knowledge_base",
        kb.id,
        kb_id=kb.id,
        detail={"name": kb.name, "documents": documents},
    )
    db.commit()
    enqueue(tasks.delete_knowledge_base, kb.id)
    return {"deleted_documents": documents}


@router.post(
    "/knowledge-bases/{kb_id}/rebuild",
    response_model=KbOut,
    summary="重建索引",
    description="按数据库里的切片重新生成这个知识库在 Milvus 中的全部数据，并改用当前配置的向量模型。"
    "重建期间这个知识库不能检索。上一次重建失败时可以再次发起。",
)
def rebuild_kb(kb_id: str, account: CurrentAccount, db: Db) -> KbOut:
    kb, level = get_kb(db, account, kb_id, LEVEL_MANAGE)
    if kb.status == KB_REBUILDING and kb.rebuild_error is None:
        raise AppError(409, "INVALID_STATUS", "正在重建索引，请等待完成")
    kb.status = KB_REBUILDING
    kb.rebuild_error = None
    log_operation(
        db, account.id, "kb.rebuild", "knowledge_base", kb.id, kb_id=kb.id, detail={"name": kb.name}
    )
    db.commit()
    enqueue(tasks.rebuild_knowledge_base, kb.id, timeout=6 * 3600)
    return _out(kb, level, _document_count(db, kb.id))


# ---------- 授权 ----------


def _subject_names(db: Session, grants: list[KbGrant]) -> dict[tuple[str, str], str]:
    account_ids = [g.subject_id for g in grants if g.subject_type == SUBJECT_ACCOUNT]
    group_ids = [g.subject_id for g in grants if g.subject_type == SUBJECT_GROUP]
    names: dict[tuple[str, str], str] = {}
    if account_ids:
        for a in db.scalars(select(Account).where(Account.id.in_(account_ids))):
            names[(SUBJECT_ACCOUNT, a.id)] = f"{a.display_name}（{a.username}）"
    if group_ids:
        for g in db.scalars(select(UserGroup).where(UserGroup.id.in_(group_ids))):
            names[(SUBJECT_GROUP, g.id)] = g.name
    return names


def _grant_out(grant: KbGrant, names: dict[tuple[str, str], str]) -> GrantOut:
    return GrantOut(
        id=grant.id,
        subject_type=grant.subject_type,
        subject_id=grant.subject_id,
        subject_name=names.get((grant.subject_type, grant.subject_id), "（已不存在）"),
        level=grant.level,  # type: ignore[arg-type]
        created_at=grant.created_at,
    )


def _ensure_manager_remains(db: Session, kb_id: str, excluding_grant_id: str) -> None:
    """除了要改动的这一条之外，必须还有别的「管理」授权，否则知识库会变成无人可管。"""
    others = db.scalar(
        select(func.count())
        .select_from(KbGrant)
        .where(KbGrant.kb_id == kb_id, KbGrant.level == LEVEL_MANAGE, KbGrant.id != excluding_grant_id)
    )
    if not others:
        raise AppError(
            400, "LAST_MANAGER", "这是最后一个「管理」授权，不能取消或降级；请先把管理权限授给其他人"
        )


@router.get("/knowledge-bases/{kb_id}/grants", response_model=list[GrantOut], summary="知识库的授权列表")
def list_grants(kb_id: str, account: CurrentAccount, db: Db) -> list[GrantOut]:
    kb, _ = get_kb(db, account, kb_id, LEVEL_MANAGE)
    grants = list(db.scalars(select(KbGrant).where(KbGrant.kb_id == kb.id).order_by(KbGrant.created_at)))
    names = _subject_names(db, grants)
    return [_grant_out(g, names) for g in grants]


@router.put(
    "/knowledge-bases/{kb_id}/grants",
    response_model=GrantOut,
    summary="授权给分组或账号；对象已有授权时改为新的档位",
)
def put_grant(kb_id: str, body: GrantIn, account: CurrentAccount, db: Db) -> GrantOut:
    kb, _ = get_kb(db, account, kb_id, LEVEL_MANAGE)
    subject = db.get(UserGroup if body.subject_type == SUBJECT_GROUP else Account, body.subject_id)
    if subject is None:
        raise AppError(400, "INVALID_PARAM", "授权对象不存在")

    grant = db.scalar(
        select(KbGrant).where(
            KbGrant.kb_id == kb.id,
            KbGrant.subject_type == body.subject_type,
            KbGrant.subject_id == body.subject_id,
        )
    )
    before = grant.level if grant else None
    if grant is None:
        grant = KbGrant(
            kb_id=kb.id,
            subject_type=body.subject_type,
            subject_id=body.subject_id,
            level=body.level,
            granted_by=account.id,
        )
        db.add(grant)
    elif grant.level != body.level:
        if grant.level == LEVEL_MANAGE:
            _ensure_manager_remains(db, kb.id, grant.id)
        grant.level = body.level
        grant.granted_by = account.id
    db.flush()
    names = _subject_names(db, [grant])
    if before != body.level:
        log_operation(
            db,
            account.id,
            "kb.grant",
            "kb_grant",
            grant.id,
            kb_id=kb.id,
            detail={
                "subject_type": body.subject_type,
                "subject": names.get((grant.subject_type, grant.subject_id)),
                "before": before,
                "after": body.level,
            },
        )
    db.commit()
    return _grant_out(grant, names)


@router.delete("/knowledge-bases/{kb_id}/grants/{grant_id}", summary="取消授权")
def delete_grant(kb_id: str, grant_id: str, account: CurrentAccount, db: Db) -> dict:
    kb, _ = get_kb(db, account, kb_id, LEVEL_MANAGE)
    grant = db.get(KbGrant, grant_id)
    if grant is None or grant.kb_id != kb.id:
        raise AppError(404, "NOT_FOUND", "授权不存在")
    if grant.level == LEVEL_MANAGE:
        _ensure_manager_remains(db, kb.id, grant.id)
    names = _subject_names(db, [grant])
    log_operation(
        db,
        account.id,
        "kb.revoke",
        "kb_grant",
        grant.id,
        kb_id=kb.id,
        detail={
            "subject_type": grant.subject_type,
            "subject": names.get((grant.subject_type, grant.subject_id)),
            "level": grant.level,
        },
    )
    db.delete(grant)
    db.commit()
    return {"removed": 1}
