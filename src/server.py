from __future__ import annotations

import asyncio
import configparser
import contextlib
import json
import logging
import os
import subprocess
from dataclasses import dataclass
import requests
from databricks.sdk.core import Config
from databricks.sdk import WorkspaceClient
from databricks.sdk.oauth import OAuthClient, OidcEndpoints
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route, Mount
import uvicorn

DEFAULT_ALLOWED_ORIGINS = [
    "http://127.0.0.1:*",
    "http://localhost:*",
    "https://chat.adc-cyber.org",
]
# Extra origins can be added with MCP_ALLOWED_ORIGINS (comma-separated).
_extra_origins = [o.strip() for o in os.getenv("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()]

server = FastMCP(
    "databricks-local",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*"],
        allowed_origins=DEFAULT_ALLOWED_ORIGINS + _extra_origins,
    ),
)
logger = logging.getLogger("databricks_local_mcp")


@dataclass
class OAuthSession:
    host: str
    token: str


oauth_session: OAuthSession | None = None


def configure_process_tls() -> None:
    if not ssl_verification_disabled():
        return
    os.environ["PYTHONHTTPSVERIFY"] = "0"
    os.environ["CURL_CA_BUNDLE"] = ""
    os.environ["REQUESTS_CA_BUNDLE"] = ""
    os.environ["SSL_CERT_FILE"] = ""
    os.environ["SSL_CERT_DIR"] = ""


def get_config_profile() -> str | None:
    return os.getenv("DATABRICKS_CONFIG_PROFILE")


def browser_oauth_configured() -> bool:
    return bool(os.getenv("DATABRICKS_CLIENT_ID"))


def get_auth_source() -> str:
    if os.getenv("DATABRICKS_TOKEN"):
        return "token"
    if oauth_session is not None:
        return "oauth_session"
    if get_config_profile():
        return "config_profile"
    return "sdk_default"


def ssl_verification_disabled() -> bool:
    value = os.getenv("DATABRICKS_INSECURE_SKIP_VERIFY")
    if value is None:
        return True
    return value.lower() in {"1", "true", "yes", "on"}


def get_databricks_host() -> str:
    return os.getenv("DATABRICKS_HOST", "https://dbc-bfeefdc7-0d24.cloud.databricks.com/")


def get_oauth_redirect_url() -> str:
    return os.getenv("DATABRICKS_OAUTH_REDIRECT_URL", "http://127.0.0.1:8000/oauth/callback")


def get_oidc_endpoints() -> OidcEndpoints:
    response = requests.get(
        f"{get_databricks_host().rstrip('/')}/oidc/.well-known/oauth-authorization-server",
        timeout=5,
        verify=not ssl_verification_disabled(),
    )
    response.raise_for_status()
    return OidcEndpoints.from_dict(response.json())


def get_oauth_client() -> OAuthClient:
    client_id = os.getenv("DATABRICKS_CLIENT_ID")
    client_secret = os.getenv("DATABRICKS_CLIENT_SECRET")
    if not client_id:
        raise ValueError(
            "Browser OAuth requires DATABRICKS_CLIENT_ID for an OAuth app that has "
            f"{get_oauth_redirect_url()} registered as a redirect URI."
        )
    return OAuthClient(
        oidc_endpoints=get_oidc_endpoints(),
        redirect_url=get_oauth_redirect_url(),
        client_id=client_id,
        client_secret=client_secret,
    )


def _load_profile_host(profile: str | None) -> str:
    if not profile:
        return get_databricks_host()
    config_path = os.path.expanduser(os.getenv("DATABRICKS_CONFIG_FILE", "~/.databrickscfg"))
    parser = configparser.ConfigParser()
    parser.read(config_path)
    if parser.has_option(profile, "host"):
        return parser.get(profile, "host")
    return get_databricks_host()


def _get_cli_profile_token(profile: str | None, host: str) -> str:
    cmd = ["databricks", "auth", "token"]
    if profile:
        cmd.extend(["--profile", profile])
    else:
        cmd.extend(["--host", host])
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout)
    return payload["access_token"]


def _apply_tls_settings(client: WorkspaceClient) -> WorkspaceClient:
    api_client = getattr(client, "api_client", None) or getattr(client, "_api_client", None)
    base_client = getattr(api_client, "_api_client", None) if api_client else None
    session = getattr(base_client, "_session", None) if base_client else None
    if session is not None and ssl_verification_disabled():
        session.verify = False
    return client


def get_workspace_client() -> WorkspaceClient:
    global oauth_session

    host = get_databricks_host()
    token = os.getenv("DATABRICKS_TOKEN")
    profile = get_config_profile()

    if host and token:
        logger.info("Using Databricks token auth for host %s", host)
        return _apply_tls_settings(
            WorkspaceClient(config=Config(host=host, token=token, skip_verify=ssl_verification_disabled()))
        )

    if oauth_session:
        logger.info("Using in-memory OAuth session for host %s", oauth_session.host)
        return _apply_tls_settings(
            WorkspaceClient(
                config=Config(
                    host=oauth_session.host,
                    token=oauth_session.token,
                    skip_verify=ssl_verification_disabled(),
                )
            )
        )

    resolved_host = _load_profile_host(profile)
    logger.info(
        "Using Databricks CLI token auth with source=%s host=%s profile=%s skip_verify=%s",
        get_auth_source(),
        resolved_host,
        profile,
        ssl_verification_disabled(),
    )
    return _apply_tls_settings(
        WorkspaceClient(
            config=Config(
                host=resolved_host,
                token=_get_cli_profile_token(profile, resolved_host),
                skip_verify=ssl_verification_disabled(),
                http_timeout_seconds=10,
                retry_timeout_seconds=10,
            ),
            product="databricks-local-mcp",
            product_version="0.1.0",
        )
    )


def get_oauth_consent():
    return get_oauth_client().initiate_consent()


async def index(_: Request) -> HTMLResponse:
    insecure_mode = ssl_verification_disabled()
    warning_html = ""
    auth_source = get_auth_source()
    auth_html = "<p>No Databricks auth configured yet.</p>"
    login_link_html = ""
    if insecure_mode:
        warning_html = (
            "<p style=\"color:#b45309;font-weight:600;\">"
            "TLS verification is disabled for local testing only."
            "</p>"
        )
    if auth_source == "config_profile":
        auth_html = f"<p>Using Databricks CLI profile: <strong>{get_config_profile()}</strong></p>"
    elif auth_source == "token":
        auth_html = "<p>Using Databricks token from environment.</p>"
    elif auth_source == "oauth_session":
        auth_html = "<p>Using in-memory browser OAuth session.</p>"
    else:
        auth_html = "<p>Using default Databricks SDK auth discovery.</p>"

    if browser_oauth_configured():
        login_link_html = '<li><a href="/oauth/login">Login with Databricks</a></li>'
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
              {login_link_html}
              <li><a href=\"/auth/status\">Auth status</a></li>
            </ul>
          </body>
        </html>
        """
    )


async def oauth_login(_: Request) -> RedirectResponse:
    if not browser_oauth_configured():
        return JSONResponse(
            {
                "authenticated": False,
                "error": "browser_oauth_not_configured",
                "message": (
                    "Browser OAuth is not configured for this server. "
                    "Use DATABRICKS_CONFIG_PROFILE or DATABRICKS_TOKEN instead, "
                    "or set DATABRICKS_CLIENT_ID to enable browser OAuth."
                ),
                "auth_source": get_auth_source(),
                "config_profile": get_config_profile(),
                "workspace_host": get_databricks_host(),
            },
            status_code=400,
        )
    try:
        logger.info("Starting browser OAuth login")
        consent = get_oauth_consent()
    except Exception as exc:
        logger.exception("Browser OAuth initialization failed")
        message = str(exc)
        if "CERTIFICATE_VERIFY_FAILED" in message and not ssl_verification_disabled():
            message = (
                f"{message} Set DATABRICKS_INSECURE_SKIP_VERIFY=true for local testing only, "
                "then restart the server."
            )
        return JSONResponse(
            {
                "authenticated": False,
                "error": "oauth_init_failed",
                "message": message,
                "workspace_host": get_databricks_host(),
                "ssl_verification_disabled": ssl_verification_disabled(),
            },
            status_code=500,
        )
    return RedirectResponse(consent.authorization_url)


async def oauth_callback(request: Request) -> JSONResponse:
    global oauth_session

    try:
        logger.info("Handling browser OAuth callback")
        consent = get_oauth_consent()
        credentials = consent.exchange_callback_parameters(dict(request.query_params))
    except Exception as exc:
        logger.exception("Browser OAuth callback failed")
        return JSONResponse(
            {
                "authenticated": False,
                "error": "oauth_callback_failed",
                "message": str(exc),
                "workspace_host": get_databricks_host(),
            },
            status_code=500,
        )

    oauth_session = OAuthSession(
        host=get_databricks_host(),
        token=credentials.token().access_token,
    )
    logger.info("Browser OAuth login completed for host %s", oauth_session.host)

    return JSONResponse(
        {
            "authenticated": True,
            "workspace_host": oauth_session.host,
            "message": "Databricks OAuth login completed. You can now use MCP tools with this server.",
        }
    )


async def auth_status(_: Request) -> JSONResponse:
    return JSONResponse(
        {
            "auth_source": get_auth_source(),
            "workspace_host": get_databricks_host(),
            "config_profile": get_config_profile(),
            "has_env_token": bool(os.getenv("DATABRICKS_TOKEN")),
            "has_oauth_session": oauth_session is not None,
            "browser_oauth_configured": browser_oauth_configured(),
            "ssl_verification_disabled": ssl_verification_disabled(),
            "oauth_login_url": "/oauth/login" if browser_oauth_configured() else None,
            "oauth_callback_url": get_oauth_redirect_url(),
        }
    )


async def auth_test(_: Request) -> JSONResponse:
    if get_auth_source() == "config_profile":
        return await auth_test_cli(_)

    try:
        client, current_user = await asyncio.wait_for(
            asyncio.to_thread(get_current_user),
            timeout=10,
        )
    except TimeoutError:
        logger.exception("Databricks auth test timed out")
        return JSONResponse(
            {
                "authenticated": False,
                "auth_source": get_auth_source(),
                "config_profile": get_config_profile(),
                "workspace_host": get_databricks_host(),
                "message": "Databricks auth test timed out after 10 seconds.",
            },
            status_code=504,
        )
    except Exception as exc:
        logger.exception("Databricks auth test failed")
        return JSONResponse(
            {
                "authenticated": False,
                "auth_source": get_auth_source(),
                "config_profile": get_config_profile(),
                "workspace_host": get_databricks_host(),
                "message": str(exc),
            },
            status_code=500,
        )

    return JSONResponse(
        {
            "authenticated": True,
            "auth_source": get_auth_source(),
            "config_profile": get_config_profile(),
            "workspace_host": getattr(client.config, "host", None),
            "user_name": current_user.user_name,
            "display_name": current_user.display_name,
            "active": current_user.active,
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
                "workspace_host": get_databricks_host(),
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
                "workspace_host": get_databricks_host(),
                "message": str(exc),
            },
            status_code=500,
        )

    return JSONResponse(result)


def get_current_user():
    client = get_workspace_client()
    current_user = client.current_user.me()
    return client, current_user


def run_databricks_cli_current_user(profile: str) -> dict:
    command = [
        "databricks",
        "current-user",
        "me",
        "--profile",
        profile,
        "--output",
        "json",
    ]
    env = os.environ.copy()
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=8,
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        raise RuntimeError(stderr or stdout or f"Databricks CLI exited with code {completed.returncode}")

    payload = json.loads(completed.stdout)
    return {
        "authenticated": True,
        "auth_source": "config_profile_cli",
        "config_profile": profile,
        "workspace_host": get_databricks_host(),
        "user_name": payload.get("userName"),
        "display_name": payload.get("displayName"),
        "active": payload.get("active"),
        "raw": payload,
    }


@server.tool()
def databricks_ping():
    """Return basic Databricks workspace identity information."""
    client, current_user = get_current_user()

    return {
        "workspace_host": getattr(client.config, "host", None),
        "user_name": current_user.user_name,
        "display_name": current_user.display_name,
        "active": current_user.active,
    }


@server.tool()
def databricks_list_catalogs() -> list[dict]:
    """List Unity Catalog catalogs visible to the configured identity."""
    client = get_workspace_client()
    return [
        {
            "catalog_name": catalog.name,
            "full_name": catalog.full_name,
            "comment": catalog.comment,
            "owner": catalog.owner,
        }
        for catalog in client.catalogs.list()
    ]


@server.tool()
def databricks_list_schemas(catalog_name: str) -> list[dict]:
    """List schema names in a Unity Catalog catalog."""
    client = get_workspace_client()
    return [
        {"schema_name": schema.name, "full_name": schema.full_name, "comment": schema.comment}
        for schema in client.schemas.list(catalog_name=catalog_name)
    ]


@server.tool()
def databricks_list_tables(catalog_name: str, schema_name: str) -> list[dict]:
    """List table names in a Unity Catalog schema (catalog_name.schema_name)."""
    client = get_workspace_client()
    return [
        {
            "table_name": table.name,
            "full_name": table.full_name,
            "table_type": table.table_type.value if table.table_type else None,
            "comment": table.comment,
        }
        for table in client.tables.list(catalog_name=catalog_name, schema_name=schema_name)
    ]


@server.tool()
def databricks_get_table_columns(catalog_name: str, schema_name: str, table_name: str) -> list[dict]:
    """Return column metadata for a Unity Catalog table."""
    client = get_workspace_client()
    table = client.tables.get(full_name=f"{catalog_name}.{schema_name}.{table_name}")
    columns = table.columns or []
    return [
        {
            "name": column.name,
            "type_name": column.type_name.value if column.type_name else None,
            "type_text": column.type_text,
            "position": column.position,
            "nullable": column.nullable,
            "comment": column.comment,
        }
        for column in columns
    ]


@server.tool()
def databricks_describe_table(catalog_name: str, schema_name: str, table_name: str) -> dict:
    """Return detailed metadata for a Unity Catalog table."""
    client = get_workspace_client()
    table = client.tables.get(full_name=f"{catalog_name}.{schema_name}.{table_name}")
    return {
        "name": table.name,
        "full_name": table.full_name,
        "catalog_name": table.catalog_name,
        "schema_name": table.schema_name,
        "table_type": table.table_type.value if table.table_type else None,
        "data_source_format": table.data_source_format.value if table.data_source_format else None,
        "storage_location": table.storage_location,
        "comment": table.comment,
        "owner": table.owner,
        "created_at": table.created_at,
        "created_by": table.created_by,
        "updated_at": table.updated_at,
        "updated_by": table.updated_by,
        "view_definition": table.view_definition,
        "properties": table.properties,
        "columns": [
            {
                "name": column.name,
                "type_name": column.type_name.value if column.type_name else None,
                "type_text": column.type_text,
                "position": column.position,
                "nullable": column.nullable,
                "comment": column.comment,
            }
            for column in (table.columns or [])
        ],
    }


def _execute_sql(statement: str, row_limit: int, wait_timeout: str = "30s") -> dict:
    client = get_workspace_client()
    warehouse_id = os.getenv("DATABRICKS_SQL_WAREHOUSE_ID")
    if not warehouse_id:
        raise ValueError("DATABRICKS_SQL_WAREHOUSE_ID must be set to execute SQL statements.")
    response = client.statement_execution.execute_statement(
        statement=statement,
        warehouse_id=warehouse_id,
        wait_timeout=wait_timeout,
        row_limit=row_limit,
    )
    manifest = response.manifest
    result = response.result
    columns = [col.name for col in (manifest.schema.columns if manifest and manifest.schema else [])]
    rows = []
    if result and result.data_array:
        rows = [dict(zip(columns, row, strict=False)) for row in result.data_array]
    return {
        "statement": statement,
        "warehouse_id": warehouse_id,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": bool(result and result.next_chunk_index is not None),
        "status": response.status.state.value if response.status and response.status.state else None,
    }


@server.tool()
def databricks_get_table_sample(catalog_name: str, schema_name: str, table_name: str, limit: int = 10) -> dict:
    """Return a small sample of rows from a Unity Catalog table."""
    safe_limit = max(1, min(limit, 100))
    statement = f"SELECT * FROM `{catalog_name}`.`{schema_name}`.`{table_name}` LIMIT {safe_limit}"
    return _execute_sql(statement=statement, row_limit=safe_limit)


@server.tool()
def databricks_run_sql(statement: str, limit: int = 100) -> dict:
    """Run a read-only SQL statement against the configured Databricks SQL warehouse."""
    normalized = statement.strip().rstrip(";")
    if not normalized:
        raise ValueError("SQL statement must not be empty.")
    first_token = normalized.split(None, 1)[0].upper()
    allowed = {"SELECT", "SHOW", "DESCRIBE", "WITH"}
    if first_token not in allowed:
        raise ValueError(
            "Only read-only SQL statements are allowed. Start the statement with SELECT, SHOW, DESCRIBE, or WITH."
        )
    safe_limit = max(1, min(limit, 1000))
    return _execute_sql(statement=normalized, row_limit=safe_limit)


@server.tool()
def databricks_search_tables(query: str, max_results: int = 25) -> list[dict]:
    """Search Unity Catalog tables by name across visible catalogs and schemas."""
    client = get_workspace_client()
    needle = query.strip().lower()
    if not needle:
        return []
    limit = max(1, min(max_results, 100))
    matches: list[dict] = []
    for catalog in client.catalogs.list():
        for schema in client.schemas.list(catalog_name=catalog.name):
            for table in client.tables.list(catalog_name=catalog.name, schema_name=schema.name):
                haystacks = [table.name or "", table.full_name or "", table.comment or ""]
                if any(needle in value.lower() for value in haystacks):
                    matches.append(
                        {
                            "catalog_name": catalog.name,
                            "schema_name": schema.name,
                            "table_name": table.name,
                            "full_name": table.full_name,
                            "table_type": table.table_type.value if table.table_type else None,
                            "comment": table.comment,
                        }
                    )
                    if len(matches) >= limit:
                        return matches
    return matches


@server.tool()
def databricks_list_warehouses() -> list[dict]:
    """List SQL warehouses visible to the configured identity."""
    client = get_workspace_client()
    return [
        {
            "id": warehouse.id,
            "name": warehouse.name,
            "state": warehouse.state.value if warehouse.state else None,
            "size": warehouse.cluster_size,
            "auto_stop_mins": warehouse.auto_stop_mins,
            "enable_serverless_compute": warehouse.enable_serverless_compute,
        }
        for warehouse in client.warehouses.list()
    ]


@server.resource("config://databricks")
def databricks_config() -> str:
    """Describe the Databricks auth expected by this local MCP server."""
    return (
        "Set DATABRICKS_HOST and DATABRICKS_TOKEN, or set DATABRICKS_CONFIG_PROFILE to a Databricks CLI "
        "profile that databricks-sdk can discover locally. TLS certificate verification is disabled by "
        "default for this local server. Set DATABRICKS_INSECURE_SKIP_VERIFY=false to turn verification "
        "back on."
    )


def main() -> None:
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8000"))
    log_level = os.getenv("MCP_LOG_LEVEL", "INFO").upper()
    configure_process_tls()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info(
        "Starting Databricks Local MCP on %s:%s with auth_source=%s profile=%s ssl_verification_disabled=%s",
        host,
        port,
        get_auth_source(),
        get_config_profile(),
        ssl_verification_disabled(),
    )
    mcp_app = server.streamable_http_app()

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette):
        # FastMCP's session manager must be running before handling /mcp requests.
        async with server.session_manager.run():
            yield

    app = Starlette(
        routes=[
            Route("/", index),
            Route("/oauth/login", oauth_login),
            Route("/oauth/callback", oauth_callback),
            Route("/auth/status", auth_status),
            Route("/auth/test", auth_test),
            Route("/auth/test-cli", auth_test_cli),
            Mount("/", app=mcp_app),
        ],
        lifespan=lifespan,
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=[o for o in DEFAULT_ALLOWED_ORIGINS + _extra_origins if ":*" not in o],
                allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
                allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                allow_headers=["*"],
                expose_headers=["Mcp-Session-Id"],
            )
        ],
    )
    uvicorn.run(app, host=host, port=port, log_level=log_level.lower())


if __name__ == "__main__":
    main()
