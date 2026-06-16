"""FastAPI application factory.

`create_app(deps)` takes a fully-built `AppDeps` so the whole app is testable with fakes. It
installs problem+json error handlers, a request-id middleware, optional CORS, and a lifespan that
runs the deps' shutdown hook (e.g. disposing the DB engine).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request

from services.api.deps import AppDeps
from services.api.errors import install_error_handlers
from services.api.routers import investigations, review, system

API_TITLE = "RCA & Incident Troubleshooting Platform API"
API_VERSION = "v1"


def create_app(deps: AppDeps) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if deps.aclose is not None:
            await deps.aclose()

    app = FastAPI(title=API_TITLE, version=API_VERSION, lifespan=lifespan)
    app.state.deps = deps

    @app.middleware("http")
    async def _request_id(request: Request, call_next):  # noqa: ANN001, ANN202
        rid = request.headers.get("X-Request-ID") or uuid4().hex
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response

    if deps.settings.cors_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware, allow_origins=deps.settings.cors_origins,
            allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type",
                                                          "Idempotency-Key", "X-Request-ID"],
        )

    install_error_handlers(app)
    app.include_router(system.router)
    app.include_router(investigations.router)
    app.include_router(review.router)
    return app
