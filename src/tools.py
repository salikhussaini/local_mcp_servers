from __future__ import annotations

import logging
import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .databricks_cli import (
    _execute_sql,
    _load_profile_host,
    _normalize_table_type,
    _require_profile,
    _run_databricks_cli_with_profile,
)


SYSTEM_TABLES = {
    "query_history": "system.query.history",
    "billing_usage": "system.billing.usage",
    "access_audit": "system.access.audit",
}


def _quote_sql_string(value: str) -> str:
    return value.replace("'", "''")


def _coerce_days(days: int, *, default: int, maximum: int) -> int:
    return max(1, min(days or default, maximum))


def _coerce_limit(limit: int, *, default: int, maximum: int) -> int:
    return max(1, min(limit or default, maximum))


def _system_table_unavailable_error(table_name: str, exc: Exception) -> RuntimeError:
    return RuntimeError(
        f"Required Databricks system table '{table_name}' is unavailable or not enabled for this workspace: {exc}"
    )

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


@server.tool()
def databricks_query_history(days: int = 7, user_name: str | None = None, limit: int = 100) -> dict:
    """Return recent query history from Databricks system tables."""
    safe_days = _coerce_days(days, default=7, maximum=90)
    safe_limit = _coerce_limit(limit, default=100, maximum=500)
    filters = [f"start_time >= current_timestamp() - INTERVAL {safe_days} DAYS"]
    if user_name:
        filters.append(f"executed_by = '{_quote_sql_string(user_name.strip())}'")
    statement = f"""
        SELECT
            statement_id,
            executed_by,
            statement_text,
            statement_type,
            warehouse_id,
            compute_type,
            execution_status,
            total_duration_ms,
            rows_produced,
            start_time,
            end_time
        FROM {SYSTEM_TABLES['query_history']}
        WHERE {' AND '.join(filters)}
        ORDER BY start_time DESC
        LIMIT {safe_limit}
    """.strip()
    try:
        return _execute_sql(statement=statement, row_limit=safe_limit)
    except Exception as exc:
        raise _system_table_unavailable_error(SYSTEM_TABLES["query_history"], exc) from exc


@server.tool()
def databricks_user_activity_summary(days: int = 7, limit: int = 50) -> dict:
    """Summarize Databricks SQL activity by user over a time window."""
    safe_days = _coerce_days(days, default=7, maximum=90)
    safe_limit = _coerce_limit(limit, default=50, maximum=200)
    statement = f"""
        SELECT
            executed_by,
            COUNT(*) AS query_count,
            COUNT(DISTINCT warehouse_id) AS warehouse_count,
            SUM(COALESCE(total_duration_ms, 0)) AS total_duration_ms,
            AVG(COALESCE(total_duration_ms, 0)) AS avg_duration_ms,
            MAX(start_time) AS last_query_at
        FROM {SYSTEM_TABLES['query_history']}
        WHERE start_time >= current_timestamp() - INTERVAL {safe_days} DAYS
        GROUP BY executed_by
        ORDER BY query_count DESC, total_duration_ms DESC
        LIMIT {safe_limit}
    """.strip()
    try:
        return _execute_sql(statement=statement, row_limit=safe_limit)
    except Exception as exc:
        raise _system_table_unavailable_error(SYSTEM_TABLES["query_history"], exc) from exc


@server.tool()
def databricks_cost_summary(days: int = 30, group_by: str = "user", limit: int = 100) -> dict:
    """Summarize Databricks billing usage from system tables."""
    safe_days = _coerce_days(days, default=30, maximum=365)
    safe_limit = _coerce_limit(limit, default=100, maximum=500)
    group_key = (group_by or "user").strip().lower()
    group_columns = {
        "user": ("identity_metadata.run_as", "run_as"),
        "sku": ("sku_name", "sku_name"),
        "workspace": ("workspace_id", "workspace_id"),
        "warehouse": ("usage_metadata.warehouse_id", "warehouse_id"),
    }
    if group_key not in group_columns:
        raise ValueError("group_by must be one of: user, sku, workspace, warehouse")
    group_expr, alias = group_columns[group_key]
    statement = f"""
        SELECT
            {group_expr} AS {alias},
            COUNT(*) AS usage_records,
            SUM(COALESCE(usage_quantity, 0)) AS total_usage_quantity,
            MIN(usage_start_time) AS first_usage_at,
            MAX(usage_end_time) AS last_usage_at
        FROM {SYSTEM_TABLES['billing_usage']}
        WHERE usage_start_time >= current_timestamp() - INTERVAL {safe_days} DAYS
        GROUP BY {group_expr}
        ORDER BY total_usage_quantity DESC
        LIMIT {safe_limit}
    """.strip()
    try:
        return _execute_sql(statement=statement, row_limit=safe_limit)
    except Exception as exc:
        raise _system_table_unavailable_error(SYSTEM_TABLES["billing_usage"], exc) from exc


@server.tool()
def databricks_table_access_history(days: int = 7, table_name: str | None = None, limit: int = 100) -> dict:
    """Return recent table access patterns from Databricks audit logs."""
    safe_days = _coerce_days(days, default=7, maximum=90)
    safe_limit = _coerce_limit(limit, default=100, maximum=500)
    filters = [
        f"event_time >= current_timestamp() - INTERVAL {safe_days} DAYS",
        "service_name = 'unityCatalog'",
        "action_name IN ('getTable', 'getTables', 'listTables')",
    ]
    if table_name:
        filters.append(
            "lower(coalesce(request_params.full_name_arg, request_params.name, '')) "
            f"LIKE '%{_quote_sql_string(table_name.strip().lower())}%'"
        )
    statement = f"""
        SELECT
            event_time,
            user_identity.email AS user_email,
            action_name,
            request_params.full_name_arg AS full_name,
            request_params.name AS object_name,
            source_ip_address,
            user_agent
        FROM {SYSTEM_TABLES['access_audit']}
        WHERE {' AND '.join(filters)}
        ORDER BY event_time DESC
        LIMIT {safe_limit}
    """.strip()
    try:
        return _execute_sql(statement=statement, row_limit=safe_limit)
    except Exception as exc:
        raise _system_table_unavailable_error(SYSTEM_TABLES["access_audit"], exc) from exc


@server.resource("config://databricks")
def databricks_config() -> str:
    """Describe the Databricks auth expected by this local MCP server."""
    return (
        "Set DATABRICKS_CONFIG_PROFILE to a Databricks CLI profile. The server uses only the Databricks CLI "
        "for authentication and workspace operations. TLS certificate verification is disabled by default for "
        "this local server. Set DATABRICKS_INSECURE_SKIP_VERIFY=false to turn verification back on."
    )
