"""后台任务。任务函数必须能被任务进程按模块路径导入。"""

import logging
import time

logger = logging.getLogger(__name__)


def ping(sent_at: float) -> dict[str, float]:
    """自检任务：确认接口进程入队、任务进程执行这条链路是通的。"""
    waited_ms = round((time.time() - sent_at) * 1000, 1)
    logger.info("自检任务已执行，排队耗时 %s 毫秒", waited_ms)
    return {"waited_ms": waited_ms}
