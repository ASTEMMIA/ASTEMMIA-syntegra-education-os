"""RFC 9457 Problem Details with a human next step (§10.4)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from syntegra.core.context import maybe_context
from syntegra.core.errors import DomainError, TenantIsolationError

log = logging.getLogger(__name__)
PROBLEM_BASE = "https://errors.syntegra.education/"
MEDIA = "application/problem+json"


def problem(
    status: int, slug: str, title: str, detail: str, instance: str, **extra: Any
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": PROBLEM_BASE + slug,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
    }
    ctx = maybe_context()
    if ctx is not None:
        body["correlation_id"] = str(ctx.correlation_id)
    body.update({k: v for k, v in extra.items() if v is not None})
    return JSONResponse(body, status_code=status, media_type=MEDIA)


def install(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError) -> JSONResponse:
        return problem(
            exc.status,
            exc.type_slug,
            exc.title,
            exc.detail,
            request.url.path,
            next_action=exc.next_action,
            **exc.extra,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
            for e in exc.errors()
        ]
        return problem(
            422,
            "validation-failed",
            "Some details need correcting",
            "The request has fields that need correcting.",
            request.url.path,
            next_action="Correct the highlighted fields and try again.",
            errors=errors,
        )

    @app.exception_handler(TenantIsolationError)
    async def _isolation(request: Request, exc: TenantIsolationError) -> JSONResponse:
        log.error("tenant isolation violation: %s", exc)  # alerting hooks on this logger
        return problem(
            403,
            "forbidden",
            "You do not have access to this",
            "Access outside your organisation is not allowed.",
            request.url.path,
        )
