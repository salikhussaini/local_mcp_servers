from __future__ import annotations

import configparser
import json
import os
import subprocess
import time
from urllib.parse import quote

import requests


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


def _load_profile_value(profile: str | None, key: str) -> str | None:
    if not profile:
        return None
    config_path = os.path.expanduser(os.getenv("DATABRICKS_CONFIG_FILE", "~/.databrickscfg"))
    parser = configparser.ConfigParser()
    parser.read(config_path)
    if parser.has_option(profile, key):
        return parser.get(profile, key)
    return None


def _load_profile_token(profile: str | None) -> str | None:
    return _load_profile_value(profile, "token")


def _oauth_client_credentials_token(profile: str) -> str | None:
    client_id = _load_profile_value(profile, "client_id")
    client_secret = _load_profile_value(profile, "client_secret")
    host = _load_profile_host(profile)
    if not client_id or not client_secret or not host:
        return None

    token_response = requests.post(
        f"{host.rstrip('/')}/oidc/v1/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=(
            "grant_type=client_credentials"
            f"&client_id={quote(client_id, safe='')}"
            f"&client_secret={quote(client_secret, safe='')}"
            "&scope=all-apis"
        ),
        timeout=30,
    )
    if token_response.status_code >= 400:
        raise RuntimeError(f"OAuth token request failed: {token_response.text}")
    payload = token_response.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise RuntimeError(f"OAuth token response did not include access_token: {json.dumps(payload)}")
    return access_token


def _sql_api_headers() -> dict[str, str]:
    profile = _require_profile()
    token = _load_profile_token(profile)
    if not token:
        token = _oauth_client_credentials_token(profile)
    if not token:
        raise ValueError(
            "The configured Databricks CLI profile must contain either a token or client_id/client_secret to use the SQL Statement Execution API."
        )
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _sql_api_url(path: str) -> str:
    host = get_databricks_host()
    if not host:
        raise ValueError("Unable to determine Databricks workspace host from DATABRICKS_CONFIG_PROFILE.")
    return f"{host.rstrip('/')}{path}"


def _execute_sql(statement: str, row_limit: int) -> dict:
    warehouse_id = os.getenv("DATABRICKS_SQL_WAREHOUSE_ID")
    if not warehouse_id:
        raise ValueError("DATABRICKS_SQL_WAREHOUSE_ID must be set to execute SQL statements.")

    execute_response = requests.post(
        _sql_api_url("/api/2.0/sql/statements/"),
        headers=_sql_api_headers(),
        json={
            "warehouse_id": warehouse_id,
            "statement": statement,
            "wait_timeout": "30s",
            "disposition": "INLINE",
            "format": "JSON_ARRAY",
            "row_limit": row_limit,
        },
        timeout=30,
    )
    if execute_response.status_code >= 400:
        raise RuntimeError(f"SQL Statement Execution API request failed: {execute_response.text}")

    payload = execute_response.json()
    statement_id = payload.get("statement_id")
    status = (payload.get("status") or {}).get("state")

    while status in {"PENDING", "RUNNING"}:
        if not statement_id:
            raise RuntimeError("SQL Statement Execution API did not return a statement_id for a running statement.")
        time.sleep(1)
        poll_response = requests.get(
            _sql_api_url(f"/api/2.0/sql/statements/{statement_id}"),
            headers=_sql_api_headers(),
            timeout=30,
        )
        if poll_response.status_code >= 400:
            raise RuntimeError(f"SQL Statement Execution API polling failed: {poll_response.text}")
        payload = poll_response.json()
        status = (payload.get("status") or {}).get("state")

    if status not in {"SUCCEEDED", "CLOSED"}:
        raise RuntimeError(f"SQL statement failed with status {status}: {json.dumps(payload)}")

    manifest = payload.get("manifest") or {}
    schema = manifest.get("schema") or {}
    columns = [col.get("name") for col in (schema.get("columns") or [])]
    result = payload.get("result") or {}
    data_array = result.get("data_array") or []
    rows = [dict(zip(columns, row, strict=False)) for row in data_array]
    return {
        "statement": statement,
        "warehouse_id": warehouse_id,
        "statement_id": statement_id,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": result.get("next_chunk_index") is not None,
        "status": status,
    }
