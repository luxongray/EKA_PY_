"""业务异常与全局异常兜底

与原 Java 后端对齐：任何未处理异常都翻译成统一 Result 失败返回体，
HTTP 状态码保持 200，前端只认响应体里的 code（200/500）。

@author Luxon
@date 2026/10/10
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.common.result import fail


class BusinessException(Exception):
    """业务异常：message 为面向用户的中文原因，由全局兜底转成 code=500 返回体"""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def register_exception_handlers(app: FastAPI) -> None:
    """把各类异常统一转成 HTTP 200 + Result 失败体，保证前端只处理一种结构"""

    @app.exception_handler(BusinessException)
    async def handle_business(_: Request, exc: BusinessException) -> JSONResponse:
        # 业务异常：直接把中文原因回给前端
        return JSONResponse(status_code=200, content=fail(exc.message))

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # 参数校验失败：取首个错误拼可读提示（FastAPI 默认 422，这里统一转 200 + code 500）
        errors = exc.errors()
        message = None
        if errors:
            first = errors[0]
            location = [str(p) for p in first.get("loc", []) if p not in ("body", "query", "path")]
            message = ("".join(location) + first.get("msg", "")).strip() or first.get("msg")
        return JSONResponse(status_code=200, content=fail(message))

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # 框架层 HTTP 异常（如 404）：同样收敛为统一返回体
        return JSONResponse(status_code=200, content=fail(str(exc.detail)))

    @app.exception_handler(Exception)
    async def handle_any(_: Request, exc: Exception) -> JSONResponse:
        # 兜底：其余未处理异常把自带信息回给前端
        return JSONResponse(status_code=200, content=fail(str(exc) or None))
