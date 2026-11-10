"""接口进程入口。"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.audit import api as audit_api
from app.auth import api_accounts, api_auth, api_groups, api_open
from app.auth.bootstrap import ensure_initial_admin
from app.common import clients, health
from app.common.errors import register_error_handlers
from app.common.logging import setup_logging
from app.config import get_settings
from app.kb import api_chunks, api_documents, api_kb
from app.retrieval import api as retrieval_api

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging("api")
    get_settings().resolve_secret_key()
    try:
        clients.ensure_bucket()
    except Exception as exc:
        # 存储暂时不可用不阻止启动，健康检查会如实反映
        logger.warning("创建存储桶失败，稍后可通过健康检查确认：%s", exc)
    ensure_initial_admin()
    logger.info("接口进程已启动")
    yield
    logger.info("接口进程已停止")


app = FastAPI(
    title="知识库平台",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/v1/docs",
    redoc_url=None,
    openapi_url="/api/v1/openapi.json",
)
register_error_handlers(app)
app.include_router(health.router, prefix="/api/v1")
app.include_router(api_auth.router, prefix="/api/v1")
app.include_router(api_accounts.router, prefix="/api/v1")
app.include_router(api_groups.router, prefix="/api/v1")
app.include_router(api_kb.router, prefix="/api/v1")
app.include_router(api_documents.router, prefix="/api/v1")
app.include_router(api_chunks.router, prefix="/api/v1")
app.include_router(audit_api.router, prefix="/api/v1")
app.include_router(retrieval_api.console_router, prefix="/api/v1")
app.include_router(api_open.router, prefix="/open/v1")
app.include_router(retrieval_api.open_router, prefix="/open/v1")
