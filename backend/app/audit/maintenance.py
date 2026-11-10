"""每日维护任务：清理过期的检索记录。任务执行完后把自己排到 24 小时之后。"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from app.audit.models import RetrievalLog
from app.common.clients import get_queue
from app.common.db import SessionLocal
from app.config import get_settings

logger = logging.getLogger(__name__)
JOB_ID = "daily-maintenance"


def purge_retrieval_logs() -> int:
    """删除超过保留天数的检索记录，返回删除的条数。"""
    cutoff = datetime.now(UTC) - timedelta(days=get_settings().RETRIEVAL_LOG_KEEP_DAYS)
    with SessionLocal() as db:
        removed = db.execute(delete(RetrievalLog).where(RetrievalLog.created_at < cutoff)).rowcount
        db.commit()
    return removed


def daily() -> None:
    try:
        removed = purge_retrieval_logs()
        logger.info("每日维护完成：清理检索记录 %s 条", removed)
    except Exception:
        logger.exception("每日维护失败")
    finally:
        schedule(timedelta(hours=24))


def schedule(delay: timedelta) -> None:
    """安排下一次执行。固定任务编号，重复安排时只保留一个。"""
    queue = get_queue()
    existing = queue.fetch_job(JOB_ID)
    if existing is not None:
        existing.delete()
    queue.enqueue_in(delay, daily, job_id=JOB_ID, result_ttl=0)
