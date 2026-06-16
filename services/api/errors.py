"""Problem Details (RFC 9457) errors + handlers.

Every error response is ``application/problem+json``. Internal exceptions are mapped to a generic
500 problem (no stack traces or internals leak to clients).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic import BaseModel

logger = logging.getLogger("api.errors")


class Problem(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: Optional[str] = None
    instance: Optional[str] = None
    code: Optional[str] = None
    errors: Optional[Any] = None


class ApiError(Exception):
    status: int = 500
    title: str = "Internal Server Error"
    code: Optional[str] = None

    def __init__(self, detail: Optional[str] = None, *, code: Optional[str] = None,
                 headers: Optional[dict[str, str]] = None, errors: Optional[Any] = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        self.code = code or self.code
        self.headers = headers or {}
        self.errors = errors

    def to_problem(self, instance: Optional[str] = None) -> Problem:
        return Problem(title=self.title, status=self.status, detail=self.detail,
                       instance=instance, code=self.code, errors=self.errors)


class BadRequest(ApiError):
    status, title = 400, "Bad Request"


class Unauthorized(ApiError):
    status, title = 401, "Unauthorized"

    def __init__(self, detail: Optional[str] = None, **kw: Any) -> None:
        headers = kw.pop("headers", {}) or {}
        headers.setdefault("WWW-Authenticate", 'Bearer')
        super().__init__(detail, headers=headers, **kw)


class Forbidden(ApiError):
    status, title = 403, "Forbidden"


class NotFound(ApiError):
    status, title = 404, "Not Found"


class Conflict(ApiError):
    status, title = 409, "Conflict"


class UnprocessableEntity(ApiError):
    status, title = 422, "Unprocessable Entity"


class TooManyRequests(ApiError):
    status, title = 429, "Too Many Requests"


class ServiceUnavailable(ApiError):
    status, title = 503, "Service Unavailable"


def install_error_handlers(app: Any) -> None:
    from fastapi import Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    def _resp(problem: Problem, headers: Optional[dict[str, str]] = None) -> JSONResponse:
        return JSONResponse(
            status_code=problem.status,
            content=problem.model_dump(exclude_none=True),
            media_type="application/problem+json",
            headers=headers or None,
        )

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):  # noqa: ANN202
        return _resp(exc.to_problem(instance=str(request.url.path)), exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):  # noqa: ANN202
        problem = Problem(title="Unprocessable Entity", status=422,
                          detail="Request validation failed", code="validation_error",
                          instance=str(request.url.path), errors=exc.errors())
        return _resp(problem)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):  # noqa: ANN202
        logger.exception("unhandled error on %s", request.url.path)
        problem = Problem(title="Internal Server Error", status=500,
                          detail="An unexpected error occurred", instance=str(request.url.path))
        return _resp(problem)
