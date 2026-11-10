"""统一的错误格式：{"code": "...", "message": "...", "detail": {...}}"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """业务错误。接口里直接抛出，由统一处理器转成响应。"""

    def __init__(self, status: int, code: str, message: str, detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail or {}


def _body(code: str, message: str, detail: Any = None) -> dict[str, Any]:
    return {"code": code, "message": message, "detail": detail or {}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(_body(exc.code, exc.message, exc.detail), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"field": ".".join(str(p) for p in e["loc"][1:]), "reason": e["msg"]} for e in exc.errors()]
        return JSONResponse(_body("INVALID_PARAM", "参数不正确", {"errors": errors}), status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        known = {404: ("NOT_FOUND", "接口不存在"), 405: ("METHOD_NOT_ALLOWED", "不支持的请求方法")}
        code, message = known.get(exc.status_code, ("HTTP_ERROR", str(exc.detail)))
        return JSONResponse(_body(code, message), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理的异常：%s %s", request.method, request.url.path)
        return JSONResponse(_body("INTERNAL_ERROR", "服务器内部错误"), status_code=500)
