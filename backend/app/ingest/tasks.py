"""文档处理的后台任务：解析与切分、向量化与入库、重建索引、删除知识库。

每个任务自己捕获异常并把失败原因写回文档，不依赖队列的失败记录。
"""

import logging

from sqlalchemy import delete, func, select, update

from app.common.clients import enqueue
from app.common.db import SessionLocal
from app.config import get_settings
from app.ingest import storage, vector_store
from app.ingest.chunking import ChunkConfig, ChunkingError, chunk_document
from app.ingest.embedding import EmbeddingError, embed_texts
from app.ingest.parsers import ParseError, parse_file
from app.kb.models import (
    DOC_EMBEDDING,
    DOC_FAILED,
    DOC_PARSING,
    DOC_PENDING_CONFIRM,
    DOC_READY,
    KB_ACTIVE,
    KB_REBUILDING,
    STAGE_EMBED,
    STAGE_PARSE,
    SYNC_FAILED,
    SYNC_SYNCED,
    Chunk,
    Document,
    KnowledgeBase,
)

logger = logging.getLogger(__name__)


def _fail(doc_id: str, stage: str, message: str) -> None:
    with SessionLocal() as db:
        db.execute(
            update(Document)
            .where(Document.id == doc_id)
            .values(status=DOC_FAILED, failed_stage=stage, error_message=message[:2000])
        )
        db.commit()
    logger.warning("文档 %s 处理失败（%s）：%s", doc_id, stage, message)


def parse_document(doc_id: str) -> None:
    """解析文件并切分，切片写入数据库。之后等待人工确认，或直接进入向量化。"""
    try:
        _parse_document(doc_id)
    except (ParseError, ChunkingError) as exc:
        _fail(doc_id, STAGE_PARSE, str(exc))
    except Exception as exc:
        logger.exception("解析文档 %s 时出现未预料的错误", doc_id)
        _fail(doc_id, STAGE_PARSE, f"解析时出现内部错误：{type(exc).__name__}")


def _parse_document(doc_id: str) -> None:
    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        if doc is None or doc.status != DOC_PARSING:
            logger.info("文档 %s 不存在或不在解析状态，跳过", doc_id)
            return
        data = storage.get_file(doc.pending_storage_key or doc.storage_key)
        parsed = parse_file((doc.pending_info or {}).get("file_type", doc.file_type), data)
        config = ChunkConfig.model_validate(
            {k: v for k, v in doc.chunk_config.items() if k != "effective_mode"}
        )
        drafts, effective_mode = chunk_document(parsed, config)

        target = doc.generation + 1
        db.execute(delete(Chunk).where(Chunk.doc_id == doc.id, Chunk.generation == target))
        db.add_all(
            Chunk(
                kb_id=doc.kb_id,
                doc_id=doc.id,
                generation=target,
                seq=seq,
                content=draft.content,
                char_count=len(draft.content),
                meta={**draft.metadata, "tags": doc.tags},
            )
            for seq, draft in enumerate(drafts)
        )
        doc.chunk_config = {**config.model_dump(), "effective_mode": effective_mode}
        doc.chunk_count = len(drafts)
        doc.failed_stage = None
        doc.error_message = None
        doc.status = DOC_EMBEDDING if doc.auto_confirm else DOC_PENDING_CONFIRM
        auto = doc.auto_confirm
        db.commit()
        logger.info("文档 %s 解析完成，切片 %s 个，切分方式 %s", doc_id, len(drafts), effective_mode)
    if auto:
        enqueue(embed_document, doc_id)


def embed_document(doc_id: str) -> None:
    """把准备好的切片向量化并写入 Milvus，成功后新版本生效。"""
    try:
        _embed_document(doc_id)
    except EmbeddingError as exc:
        _fail(doc_id, STAGE_EMBED, str(exc))
    except Exception as exc:
        logger.exception("文档 %s 向量化入库时出现未预料的错误", doc_id)
        _fail(doc_id, STAGE_EMBED, f"向量化入库时出现内部错误：{type(exc).__name__}")


def _embed_document(doc_id: str) -> None:
    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        if doc is None or doc.status != DOC_EMBEDDING:
            logger.info("文档 %s 不存在或不在向量化状态，跳过", doc_id)
            return
        kb = db.get(KnowledgeBase, doc.kb_id)
        assert kb is not None
        target = doc.generation + 1
        # 替换文档时，新版本用新文件名作为上下文
        doc_name = (doc.pending_info or {}).get("name", doc.name)
        chunks = list(
            db.scalars(
                select(Chunk).where(Chunk.doc_id == doc.id, Chunk.generation == target).order_by(Chunk.seq)
            )
        )
        if not chunks:
            raise EmbeddingError("没有可入库的切片，请重新切分")

        vectors = embed_texts(
            [vector_store.embedding_text(doc_name, c.meta.get("heading_path"), c.content) for c in chunks]
        )
        rows = [
            vector_store.build_row(
                chunk_id=c.id,
                doc_id=doc.id,
                seq=c.seq,
                content=c.content,
                doc_name=doc_name,
                metadata=c.meta,
                enabled=c.enabled and doc.enabled,
                vector=vector,
            )
            for c, vector in zip(chunks, vectors, strict=True)
        ]
        new_ids = [c.id for c in chunks]
        old_ids = list(
            db.scalars(select(Chunk.id).where(Chunk.doc_id == doc.id, Chunk.generation == doc.generation))
        )
        try:
            # 重试时可能已经写入过一部分，先清掉再写，避免重复
            vector_store.delete_chunks(kb.collection_name, new_ids)
            vector_store.insert_rows(kb.collection_name, rows)
        except Exception:
            vector_store.delete_chunks(kb.collection_name, new_ids)
            raise

        # 新版本已可检索，再清理旧版本
        if old_ids:
            vector_store.delete_chunks(kb.collection_name, old_ids)
            db.execute(delete(Chunk).where(Chunk.id.in_(old_ids)))
        db.execute(update(Chunk).where(Chunk.id.in_(new_ids)).values(sync_status=SYNC_SYNCED))
        old_key = doc.storage_key if doc.pending_storage_key else None
        if doc.pending_storage_key:
            doc.storage_key, doc.pending_storage_key = doc.pending_storage_key, None
        if doc.pending_info:
            for field in ("name", "file_type", "file_size", "sha256"):
                setattr(doc, field, doc.pending_info[field])
            doc.pending_info = None
        doc.generation = target
        doc.chunk_count = len(chunks)
        doc.status = DOC_READY
        doc.failed_stage = None
        doc.error_message = None
        db.commit()
        logger.info("文档 %s 入库完成，切片 %s 个", doc_id, len(chunks))
    if old_key:
        try:
            storage.remove_file(old_key)
        except Exception as exc:
            logger.warning("删除旧文件 %s 失败，不影响使用：%s", old_key, exc)


def delete_knowledge_base(kb_id: str) -> None:
    """删除知识库的全部数据：Milvus 集合、原始文件、切片、文档、知识库本身。"""
    with SessionLocal() as db:
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None:
            return
        vector_store.drop_collection(kb.collection_name)
        removed_files = storage.remove_prefix(f"kb/{kb_id}/")
        documents = db.scalar(select(func.count()).select_from(Document).where(Document.kb_id == kb_id))
        db.execute(delete(Chunk).where(Chunk.kb_id == kb_id))
        db.execute(delete(Document).where(Document.kb_id == kb_id))
        db.delete(kb)  # 授权记录随知识库级联删除
        db.commit()
        logger.info("知识库 %s 已删除：文档 %s 个，文件 %s 个", kb_id, documents, removed_files)


def rebuild_knowledge_base(kb_id: str) -> None:
    """重建索引：按数据库里的切片重新生成整个 Milvus 集合。

    用于更换向量模型、调整集合结构，或 Milvus 数据损坏之后的恢复。只处理已入库的版本，
    正在准备中的新版本不受影响。
    """
    settings = get_settings()
    with SessionLocal() as db:
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None or kb.status != KB_REBUILDING:
            logger.info("知识库 %s 不存在或不在重建状态，跳过", kb_id)
            return
        active = (
            select(Chunk, Document.name, Document.enabled)
            .join(Document, Document.id == Chunk.doc_id)
            .where(Chunk.kb_id == kb_id, Chunk.generation == Document.generation)
            .order_by(Chunk.doc_id, Chunk.seq)
        )
        rows = db.execute(active).all()
        chunk_ids = [chunk.id for chunk, _, _ in rows]
        try:
            vector_store.drop_collection(kb.collection_name)
            vector_store.create_collection(kb.collection_name, settings.EMBEDDING_DIM)
            batch = 256
            for start in range(0, len(rows), batch):
                part = rows[start : start + batch]
                vectors = embed_texts(
                    [
                        vector_store.embedding_text(name, c.meta.get("heading_path"), c.content)
                        for c, name, _ in part
                    ]
                )
                vector_store.insert_rows(
                    kb.collection_name,
                    [
                        vector_store.build_row(
                            chunk_id=c.id,
                            doc_id=c.doc_id,
                            seq=c.seq,
                            content=c.content,
                            doc_name=name,
                            metadata=c.meta,
                            enabled=c.enabled and doc_enabled,
                            vector=vector,
                        )
                        for (c, name, doc_enabled), vector in zip(part, vectors, strict=True)
                    ],
                )
        except Exception as exc:
            # 集合可能只写了一部分。保持「重建中」状态，检索会被拒绝，避免返回残缺的结果；
            # 重新发起重建即可。
            logger.exception("知识库 %s 重建索引失败", kb_id)
            if chunk_ids:
                db.execute(update(Chunk).where(Chunk.id.in_(chunk_ids)).values(sync_status=SYNC_FAILED))
            kb.rebuild_error = (
                str(exc)[:2000] if isinstance(exc, EmbeddingError) else f"内部错误：{type(exc).__name__}"
            )
            db.commit()
            return
        if chunk_ids:
            db.execute(update(Chunk).where(Chunk.id.in_(chunk_ids)).values(sync_status=SYNC_SYNCED))
        kb.embedding_model = settings.EMBEDDING_MODEL
        kb.embedding_dim = settings.EMBEDDING_DIM
        kb.rebuild_error = None
        kb.status = KB_ACTIVE
        db.commit()
        logger.info("知识库 %s 重建索引完成，切片 %s 个", kb_id, len(chunk_ids))
