from __future__ import annotations

import contextlib
import logging
import os

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Mount, Route
import uvicorn

from .bitbucket_cli import get_configuration
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
    config = get_configuration()
    return JSONResponse(
        {
            "ok": True,
            "allowed_repos_count": len(config.allowed_repos),
            "allowed_origins": DEFAULT_ALLOWED_ORIGINS + _extra_origins,
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

    # Fail before starting the server if configuration is missing or invalid.
    config = get_configuration()
    logger.info(
        "Starting Bitbucket Local MCP on %s:%s (allowlisted repos=%s)",
        host,
        port,
        len(config.allowed_repos),
    )

    mcp_app = server.streamable_http_app()

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette):
        async with server.session_manager.run():
            yield

    app = Starlette(
        routes=[
            Route("/", index),
            Route("/health", health),
            Mount("/mcp", app=mcp_app),
        ],
        lifespan=lifespan,
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=[o for o in DEFAULT_ALLOWED_ORIGINS + _extra_origins if ":*" not in o],
                allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\\d+)?$",
                allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                allow_headers=["*"],
                expose_headers=["Mcp-Session-Id"],
            )
        ],
    )

    uvicorn.run(app, host=host, port=port, log_level=log_level.lower())


if __name__ == "__main__":
    main()
