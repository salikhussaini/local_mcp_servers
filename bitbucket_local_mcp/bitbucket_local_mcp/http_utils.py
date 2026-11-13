from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger("mcp_http")


@dataclass(frozen=True)
class ErrorPayload:
    type: str
    message: str
    hint: str | None = None
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": self.type,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.hint:
            payload["hint"] = self.hint
        return payload


def error_response(
    *,
    error_type: str,
    message: str,
    status_code: int = 400,
    hint: str | None = None,
    retryable: bool = False,
) -> JSONResponse:
    return JSONResponse(
        {"error": ErrorPayload(error_type, message, hint=hint, retryable=retryable).to_dict()},
        status_code=status_code,
    )


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, header_name: str = "X-Correlation-Id"):
        super().__init__(app)
        self.header_name = header_name

    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]) -> Response:
        correlation_id = request.headers.get(self.header_name) or str(uuid.uuid4())
        request.state.correlation_id = correlation_id
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled exception correlation_id=%s path=%s", correlation_id, request.url.path)
            raise
        duration_ms = (time.perf_counter() - start) * 1000
        response.headers[self.header_name] = correlation_id
        logger.info(
            "request correlation_id=%s method=%s path=%s status=%s duration_ms=%.2f",
            correlation_id,
            request.method,
            request.url.path,
            getattr(response, "status_code", "?"),
            duration_ms,
        )
        return response


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def maybe_enable_json_logging() -> None:
    if os.getenv("MCP_LOG_FORMAT", "").strip().lower() != "json":
        return

    root = logging.getLogger()
    formatter = JsonLogFormatter()
    for handler in root.handlers:
        handler.setFormatter(formatter)
