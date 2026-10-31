from __future__ import annotations

import asyncio
import configparser
import contextlib
import json
import logging
import os
import subprocess
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


def get_auth_source() -> str:
    if get_config_profile():
        return "config_profile"
    return "unconfigured"


def ssl_verification_disabled() -> bool:
    value = os.getenv("DATABRICKS_INSECURE_SKIP_VERIFY")
    if value is None:
        return True
    return value.lower() in {"1", "true", "yes", "on"}


def get_databricks_host() -> str | None:
    profile = get_config_profile()
    if profile:
        return _load_profile_host(profile)
    return None


def _load_profile_host(profile: str | None) -> str | None:
    if not profile:
        return None
    config_path = os.path.expanduser(os.getenv("DATABRICKS_CONFIG_FILE", "~/.databrickscfg"))
    parser = configparser.ConfigParser()
    parser.read(config_path)
    if parser.has_option(profile, "host"):
        return parser.get(profile, "host")
    return None


def _require_profile() -> str:
    profile = get_config_profile()
    if not profile:
        raise ValueError("DATABRICKS_CONFIG_PROFILE must be set.")
    return profile


def _run_databricks_cli(command: list[str], timeout: int = 10) -> dict | list:
    env = os.environ.copy()
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        raise RuntimeError(stderr or stdout or f"Databricks CLI exited with code {completed.returncode}")
    stdout = completed.stdout.strip()
    if not stdout:
        return {}
    return json.loads(stdout)


def _run_databricks_cli_with_profile(args: list[str], timeout: int = 10) -> dict | list:
    profile = _require_profile()
    return _run_databricks_cli(["databricks", *args, "--profile", profile, "--output", "json"], timeout=timeout)


def _normalize_table_type(value: str | None) -> str | None:
    return value.upper() if value else None


async def index(_: Request) -> HTMLResponse:
    insecure_mode = ssl_verification_disabled()
    warning_html = ""
    auth_source = get_auth_source()
    auth_html = "<p>No Databricks CLI profile configured yet.</p>"
    if insecure_mode:
        warning_html = (
            "<p style=\"color:#b45309;font-weight:600;\">"
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


def run_databricks_cli_current_user(profile: str) -> dict:
    payload = _run_databricks_cli_with_profile(["current-user", "me"], timeout=8)
    return {
        "authenticated": True,
        "auth_source": "config_profile_cli",
        "config_profile": profile,
        "workspace_host": _load_profile_host(profile),
        "user_name": payload.get("userName"),
        "display_name": payload.get("displayName"),
        "active": payload.get("active"),
        "raw": payload,
    }


@server.tool()
def databricks_ping():
    """Return basic Databricks workspace identity information."""
    profile = _require_profile()
    current_user = _run_databricks_cli_with_profile(["current-user", "me"], timeout=8)
    return {
        "workspace_host": _load_profile_host(profile),
        "user_name": current_user.get("userName"),
        "display_name": current_user.get("displayName"),
        "active": current_user.get("active"),
    }


@server.tool()
def databricks_list_catalogs() -> list[dict]:
    """List Unity Catalog catalogs visible to the configured identity."""
    payload = _run_databricks_cli_with_profile(["catalogs", "list"])
    return [
        {
            "catalog_name": catalog.get("name"),
            "full_name": catalog.get("full_name"),
            "comment": catalog.get("comment"),
            "owner": catalog.get("owner"),
        }
        for catalog in payload
    ]


@server.tool()
def databricks_list_schemas(catalog_name: str) -> list[dict]:
    """List schema names in a Unity Catalog catalog."""
    payload = _run_databricks_cli_with_profile(["schemas", "list", catalog_name])
    return [
        {
            "schema_name": schema.get("name"),
            "full_name": schema.get("full_name"),
            "comment": schema.get("comment"),
        }
        for schema in payload
    ]


@server.tool()
def databricks_list_tables(catalog_name: str, schema_name: str) -> list[dict]:
    """List table names in a Unity Catalog schema (catalog_name.schema_name)."""
    payload = _run_databricks_cli_with_profile(["tables", "list", catalog_name, schema_name])
    return [
        {
            "table_name": table.get("name"),
            "full_name": table.get("full_name"),
            "table_type": _normalize_table_type(table.get("table_type")),
            "comment": table.get("comment"),
        }
        for table in payload
    ]


@server.tool()
def databricks_get_table_columns(catalog_name: str, schema_name: str, table_name: str) -> list[dict]:
    """Return column metadata for a Unity Catalog table."""
    table = _run_databricks_cli_with_profile(["tables", "get", f"{catalog_name}.{schema_name}.{table_name}"])
    columns = table.get("columns") or []
    return [
        {
            "name": column.get("name"),
            "type_name": _normalize_table_type(column.get("type_name")),
            "type_text": column.get("type_text"),
            "position": column.get("position"),
            "nullable": column.get("nullable"),
            "comment": column.get("comment"),
        }
        for column in columns
    ]


@server.tool()
def databricks_describe_table(catalog_name: str, schema_name: str, table_name: str) -> dict:
    """Return detailed metadata for a Unity Catalog table."""
    table = _run_databricks_cli_with_profile(["tables", "get", f"{catalog_name}.{schema_name}.{table_name}"])
    return {
        "name": table.get("name"),
        "full_name": table.get("full_name"),
        "catalog_name": table.get("catalog_name"),
        "schema_name": table.get("schema_name"),
        "table_type": _normalize_table_type(table.get("table_type")),
        "data_source_format": _normalize_table_type(table.get("data_source_format")),
        "storage_location": table.get("storage_location"),
        "comment": table.get("comment"),
        "owner": table.get("owner"),
        "created_at": table.get("created_at"),
        "created_by": table.get("created_by"),
        "updated_at": table.get("updated_at"),
        "updated_by": table.get("updated_by"),
        "view_definition": table.get("view_definition"),
        "properties": table.get("properties"),
        "columns": [
            {
                "name": column.get("name"),
                "type_name": _normalize_table_type(column.get("type_name")),
                "type_text": column.get("type_text"),
                "position": column.get("position"),
                "nullable": column.get("nullable"),
                "comment": column.get("comment"),
            }
            for column in (table.get("columns") or [])
        ],
    }


def _execute_sql(statement: str, row_limit: int) -> dict:
    warehouse_id = os.getenv("DATABRICKS_SQL_WAREHOUSE_ID")
    if not warehouse_id:
        raise ValueError("DATABRICKS_SQL_WAREHOUSE_ID must be set to execute SQL statements.")
    payload = _run_databricks_cli_with_profile(
        ["sql", "statement", "execute", "--warehouse-id", warehouse_id, "--statement", statement, "--row-limit", str(row_limit)],
        timeout=30,
    )
    manifest = payload.get("manifest") or {}
    schema = manifest.get("schema") or {}
    columns = [col.get("name") for col in (schema.get("columns") or [])]
    result = payload.get("result") or {}
    data_array = result.get("data_array") or []
    rows = [dict(zip(columns, row, strict=False)) for row in data_array]
    status = payload.get("status") or {}
    return {
        "statement": statement,
        "warehouse_id": warehouse_id,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": result.get("next_chunk_index") is not None,
        "status": status.get("state"),
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
    needle = query.strip().lower()
    if not needle:
        return []
    limit = max(1, min(max_results, 100))
    matches: list[dict] = []
    for catalog in databricks_list_catalogs():
        catalog_name = catalog.get("catalog_name")
        if not catalog_name:
            continue
        for schema in databricks_list_schemas(catalog_name):
            schema_name = schema.get("schema_name")
            if not schema_name:
                continue
            for table in databricks_list_tables(catalog_name, schema_name):
                haystacks = [table.get("table_name") or "", table.get("full_name") or "", table.get("comment") or ""]
                if any(needle in value.lower() for value in haystacks):
                    matches.append(
                        {
                            "catalog_name": catalog_name,
                            "schema_name": schema_name,
                            "table_name": table.get("table_name"),
                            "full_name": table.get("full_name"),
                            "table_type": table.get("table_type"),
                            "comment": table.get("comment"),
                        }
                    )
                    if len(matches) >= limit:
                        return matches
    return matches


@server.tool()
def databricks_list_warehouses() -> list[dict]:
    """List SQL warehouses visible to the configured identity."""
    payload = _run_databricks_cli_with_profile(["warehouses", "list"])
    return [
        {
            "id": warehouse.get("id"),
            "name": warehouse.get("name"),
            "state": warehouse.get("state"),
            "size": warehouse.get("cluster_size"),
            "auto_stop_mins": warehouse.get("auto_stop_mins"),
            "enable_serverless_compute": warehouse.get("enable_serverless_compute"),
        }
        for warehouse in payload
    ]


@server.resource("config://databricks")
def databricks_config() -> str:
    """Describe the Databricks auth expected by this local MCP server."""
    return (
        "Set DATABRICKS_CONFIG_PROFILE to a Databricks CLI profile. The server uses only the Databricks CLI "
        "for authentication and workspace operations. TLS certificate verification is disabled by default for "
        "this local server. Set DATABRICKS_INSECURE_SKIP_VERIFY=false to turn verification back on."
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
