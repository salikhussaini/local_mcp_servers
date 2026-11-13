from __future__ import annotations

import asyncio
import contextlib
import logging
import os

from .logging_utils import configure_file_logging
from .http_utils import CorrelationIdMiddleware, maybe_enable_json_logging

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Mount, Route
import uvicorn

import platform
from importlib.metadata import PackageNotFoundError, version

from .databricks_cli import (
    _load_profile_host,
    configure_process_tls,
    get_auth_source,
    get_config_profile,
    get_databricks_host,
    run_databricks_cli_current_user,
    ssl_verification_disabled,
)
from .tools import DEFAULT_ALLOWED_ORIGINS, _extra_origins, server

logger = logging.getLogger("databricks_local_mcp")


async def index(_: Request) -> HTMLResponse:
    insecure_mode = ssl_verification_disabled()
    warning_html = ""
    auth_source = get_auth_source()
    auth_html = "<p>No Databricks CLI profile configured yet.</p>"
    if insecure_mode:
        warning_html = (
            '<p style="color:#b45309;font-weight:600;">'
            "TLS verification is disabled for local testing only."
            "</p>"
        )
    if auth_source == "config_profile":
        auth_html = f"<p>Using Databricks CLI profile: <strong>{get_config_profile()}</strong></p>"

    return HTMLResponse(
        f"""
        <html>
          <head><title>Databricks Local MCP</title></head>
          <body>
            <h1>Databricks Local MCP</h1>
            <p>Server is running.</p>
            {auth_html}
            {warning_html}
            <ul>
              <li><a href=\"/health\">Health</a></li>
              <li><a href=\"/auth/status\">Auth status</a></li>
            </ul>
          </body>
        </html>
        """
    )


async def auth_status(_: Request) -> JSONResponse:
    return JSONResponse(
        {
            "auth_source": get_auth_source(),
            "workspace_host": get_databricks_host(),
            "config_profile": get_config_profile(),
            "ssl_verification_disabled": ssl_verification_disabled(),
        }
    )


async def auth_test(_: Request) -> JSONResponse:
    return await auth_test_cli(_)


async def health(_: Request) -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "auth_source": get_auth_source(),
            "config_profile": get_config_profile(),
            "workspace_host": get_databricks_host(),
            "ssl_verification_disabled": ssl_verification_disabled(),
            "allowed_origins": DEFAULT_ALLOWED_ORIGINS + _extra_origins,
        }
    )


async def version_info(_: Request) -> JSONResponse:
    try:
        pkg_version = version("databricks-local-mcp")
    except PackageNotFoundError:
        pkg_version = "unknown"

    return JSONResponse(
        {
            "name": "databricks-local-mcp",
            "version": pkg_version,
            "python": platform.python_version(),
        }
    )


async def auth_test_cli(_: Request) -> JSONResponse:
    profile = get_config_profile()
    if not profile:
        return JSONResponse(
            {
                "authenticated": False,
                "auth_source": get_auth_source(),
                "config_profile": None,
                "workspace_host": get_databricks_host(),
                "message": "DATABRICKS_CONFIG_PROFILE is not set.",
            },
            status_code=400,
        )

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(run_databricks_cli_current_user, profile),
            timeout=10,
        )
    except TimeoutError:
        logger.exception("Databricks CLI auth test timed out")
        return JSONResponse(
            {
                "authenticated": False,
                "auth_source": "config_profile_cli",
                "config_profile": profile,
                "workspace_host": _load_profile_host(profile),
                "message": "Databricks CLI auth test timed out after 10 seconds.",
            },
            status_code=504,
        )
    except Exception as exc:
        logger.exception("Databricks CLI auth test failed")
        return JSONResponse(
            {
                "authenticated": False,
                "auth_source": "config_profile_cli",
                "config_profile": profile,
                "workspace_host": _load_profile_host(profile),
                "message": str(exc),
            },
            status_code=500,
        )

    return JSONResponse(result)


def main() -> None:
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8000"))
    log_level = os.getenv("MCP_LOG_LEVEL", "INFO").upper()
    configure_process_tls()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    maybe_enable_json_logging()
    log_path = configure_file_logging(app_name="databricks-local-mcp", log_level=log_level)
    logger.info(
        "Starting Databricks Local MCP on %s:%s with auth_source=%s profile=%s ssl_verification_disabled=%s log_file=%s",
        host,
        port,
        get_auth_source(),
        get_config_profile(),
        ssl_verification_disabled(),
        log_path,
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
            Route("/version", version_info),
            Route("/auth/status", auth_status),
            Route("/auth/test", auth_test),
            Route("/auth/test-cli", auth_test_cli),
            # Mounting at "/mcp" ensures both "/mcp" and "/mcp/" work.
            Mount("/mcp", app=mcp_app),
        ],
        lifespan=lifespan,
        middleware=[
            Middleware(CorrelationIdMiddleware),
            Middleware(
                CORSMiddleware,
                allow_origins=[o for o in DEFAULT_ALLOWED_ORIGINS + _extra_origins if ":*" not in o],
                allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
                allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                allow_headers=["*"],
                expose_headers=["Mcp-Session-Id", "X-Correlation-Id"],
            ),
        ],
    )
    uvicorn.run(app, host=host, port=port, log_level=log_level.lower())


if __name__ == "__main__":
    main()
