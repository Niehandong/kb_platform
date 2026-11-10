"""任务进程入口：从 Redis 队列取任务执行（解析、切分、向量化、同步 Milvus）。"""

import logging
from datetime import timedelta

from rq import Worker

from app.audit import maintenance
from app.common.clients import QUEUE_NAME, get_redis
from app.common.logging import setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    setup_logging("worker")
    logger.info("任务进程已启动，队列：%s", QUEUE_NAME)
    maintenance.schedule(timedelta(minutes=1))
    worker = Worker([QUEUE_NAME], connection=get_redis())
    # 日志已由 setup_logging 接管，不让 RQ 再加一套输出
    worker.work(logging_level=logging.getLogger().level, with_scheduler=True)


if __name__ == "__main__":
    main()
