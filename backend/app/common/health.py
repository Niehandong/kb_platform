"""健康检查：逐个检查依赖的组件，任何一个不可用时返回 503 并指出是哪个。"""

import logging
import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.common import clients
from app.common.db import engine
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["健康检查"])


def _check_postgres() -> str:
    with engine.connect() as conn:
        return str(conn.execute(text("show server_version")).scalar())


def _check_redis() -> str:
    clients.get_redis().ping()
    return str(clients.get_redis().info("server")["redis_version"])


def _check_minio() -> str:
    exists = clients.get_minio().bucket_exists(get_settings().MINIO_BUCKET)
    return "桶已存在" if exists else "桶尚未创建"


def _check_milvus() -> str:
    return str(clients.get_milvus().get_server_version())


_CHECKS: dict[str, Callable[[], str]] = {
    "postgres": _check_postgres,
    "redis": _check_redis,
    "minio": _check_minio,
    "milvus": _check_milvus,
}


@router.get("/health", summary="检查接口进程及各依赖组件是否可用")
def health() -> JSONResponse:
    components: dict[str, Any] = {}
    for name, check in _CHECKS.items():
        started = time.perf_counter()
        try:
            info = check()
            components[name] = {"ok": True, "info": info}
        except Exception as exc:
            logger.warning("健康检查失败：%s：%s", name, exc)
            components[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
        components[name]["took_ms"] = round((time.perf_counter() - started) * 1000)
    ok = all(c["ok"] for c in components.values())
    return JSONResponse({"ok": ok, "components": components}, status_code=200 if ok else 503)
