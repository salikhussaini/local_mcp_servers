from __future__ import annotations

import contextlib
import logging
import os

from .logging_utils import configure_file_logging
from .http_utils import CorrelationIdMiddleware, error_response, maybe_enable_json_logging

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

import platform
from importlib.metadata import PackageNotFoundError, version
from starlette.routing import Mount, Route
import uvicorn

from .bitbucket_cli import close_client, get_configuration, mask_token
from .tools import DEFAULT_ALLOWED_ORIGINS, _extra_origins, server

logger = logging.getLogger("bitbucket_local_mcp")


async def index(_: Request) -> HTMLResponse:
    return HTMLResponse(
        """
        <html>
          <head><title>Bitbucket Local MCP</title></head>
          <body>
            <h1>Bitbucket Local MCP</h1>
            <p>Server is running.</p>
            <ul>
              <li><a href=\"/health\">Health</a></li>
            </ul>
          </body>
        </html>
        """
    )


async def health(_: Request) -> JSONResponse:
    try:
        config = get_configuration()
    except Exception as exc:
        return error_response(
            error_type="config_error",
            message=str(exc),
            status_code=500,
            hint="Check BITBUCKET_EMAIL, BITBUCKET_API_TOKEN, and BITBUCKET_ALLOWED_REPOS.",
        )

    return JSONResponse(
        {
            "ok": True,
            "allowed_repos_count": len(config.allowed_repos),
            "allowed_origins": DEFAULT_ALLOWED_ORIGINS + _extra_origins,
        }
    )


async def auth_status(_: Request) -> JSONResponse:
    config = get_configuration()
    return JSONResponse(
        {
            "email": config.email,
            "token": mask_token(config.token),
            "allowed_repos": sorted(config.allowed_repos),
        }
    )


async def version_info(_: Request) -> JSONResponse:
    try:
        pkg_version = version("bitbucket-local-mcp")
    except PackageNotFoundError:
        pkg_version = "unknown"

    return JSONResponse(
        {
            "name": "bitbucket-local-mcp",
            "version": pkg_version,
            "python": platform.python_version(),
        }
    )


def main() -> None:
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8000"))
    log_level = os.getenv("MCP_LOG_LEVEL", "INFO").upper()

    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    maybe_enable_json_logging()
    log_path = configure_file_logging(app_name="bitbucket-local-mcp", log_level=log_level)

    # Fail before starting the server if configuration is missing or invalid.
    config = get_configuration()
    logger.info(
        "Starting Bitbucket Local MCP on %s:%s (allowlisted repos=%s) log_file=%s",
        host,
        port,
        len(config.allowed_repos),
        log_path,
    )

    mcp_app = server.streamable_http_app()

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette):
        async with server.session_manager.run():
            try:
                yield
            finally:
                close_client()

    app = Starlette(
        routes=[
            Route("/", index),
            Route("/health", health),
            Route("/auth/status", auth_status),
            Route("/version", version_info),
            # Starlette's Mount matches on a path prefix. Mounting at "/mcp" ensures
            # both "/mcp" and "/mcp/" are routed into the MCP app.
            Mount("/", app=mcp_app),
        ],
        lifespan=lifespan,
        middleware=[
            Middleware(CorrelationIdMiddleware),
            Middleware(
                CORSMiddleware,
                allow_origins=[o for o in DEFAULT_ALLOWED_ORIGINS + _extra_origins if ":*" not in o],
                allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\\d+)?$",
                allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                allow_headers=["*"],
                expose_headers=["Mcp-Session-Id", "X-Correlation-Id"],
            ),
        ],
    )

    uvicorn.run(app, host=host, port=port, log_level=log_level.lower())


if __name__ == "__main__":
    main()
