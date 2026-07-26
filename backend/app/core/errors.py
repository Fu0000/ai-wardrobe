from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = structlog.get_logger(__name__)


class ErrorDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    request_id: str
    details: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    error: ErrorDetail


class AppError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details
        self.headers = headers


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


def _response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            request_id=_request_id(request),
            details=details,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers=headers,
    )


async def handle_app_error(request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, AppError):
        raise error
    return _response(
        request,
        status_code=error.status_code,
        code=error.code,
        message=error.message,
        details=error.details,
        headers=error.headers,
    )


async def handle_validation_error(
    request: Request,
    error: Exception,
) -> JSONResponse:
    if not isinstance(error, RequestValidationError):
        raise error
    safe_details = [
        {
            "location": [str(part) for part in issue["loc"]],
            "message": issue["msg"],
            "type": issue["type"],
        }
        for issue in error.errors()
    ]
    return _response(
        request,
        status_code=422,
        code="INVALID_REQUEST",
        message="请求参数不符合接口要求。",
        details=safe_details,
    )


async def handle_http_error(
    request: Request,
    error: Exception,
) -> JSONResponse:
    if not isinstance(error, StarletteHTTPException):
        raise error
    message = str(error.detail) if error.status_code < 500 else "服务暂时不可用。"
    return _response(
        request,
        status_code=error.status_code,
        code=f"HTTP_{error.status_code}",
        message=message,
    )


async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
    await logger.aexception(
        "unhandled_request_error",
        request_id=_request_id(request),
        method=request.method,
        path=request.url.path,
    )
    return _response(
        request,
        status_code=500,
        code="INTERNAL_ERROR",
        message="服务暂时不可用，请稍后重试。",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
