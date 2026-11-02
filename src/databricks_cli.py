from __future__ import annotations

import configparser
import json
import os
import subprocess


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


def configure_process_tls() -> None:
    if not ssl_verification_disabled():
        return
    os.environ["PYTHONHTTPSVERIFY"] = "0"
    os.environ["CURL_CA_BUNDLE"] = ""
    os.environ["REQUESTS_CA_BUNDLE"] = ""
    os.environ["SSL_CERT_FILE"] = ""
    os.environ["SSL_CERT_DIR"] = ""


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
