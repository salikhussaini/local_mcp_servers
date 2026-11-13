from __future__ import annotations

from urllib.parse import urlsplit

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .bitbucket_cli import (
    API_BASE_URL,
    MAX_FILE_BYTES,
    build_source_url,
    get_configuration,
    request_bytes,
    request_json,
    validate_path,
    validate_ref,
    validate_repository,
)

DEFAULT_ALLOWED_ORIGINS = [
    "http://127.0.0.1:*",
    "http://localhost:*",
]

import os

# Extra origins can be added with MCP_ALLOWED_ORIGINS (comma-separated).
_extra_origins = [o.strip() for o in os.getenv("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()]

server = FastMCP(
    "bitbucket-local",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*"],
        allowed_origins=DEFAULT_ALLOWED_ORIGINS + _extra_origins,
    ),
)


@server.tool(name="bitbucket_list_repositories")
def list_repositories() -> dict:
    """List accessible repositories in the configured allowlist only."""
    config = get_configuration()
    repositories = []

    for repo in sorted(config.allowed_repos):
        workspace, repository = repo.split("/", 1)
        url = f"{API_BASE_URL}/repositories/{workspace}/{repository}"

        try:
            data = request_json(config, url)
            repositories.append(
                {
                    "full_name": data.get("full_name", repo),
                    "name": data.get("name"),
                    "main_branch": (data.get("mainbranch") or {}).get("name"),
                }
            )
        except ValueError:
            repositories.append(
                {"full_name": repo, "error": "Unavailable or inaccessible"}
            )

    return {
        "repositories": repositories,
        "note": "Only configured allowlisted repositories are checked.",
    }


@server.tool(name="bitbucket_list_files")
def list_files(
    workspace: str,
    repository: str,
    ref: str,
    path: str = "",
    cursor: str = "",
) -> dict:
    """List one page of files in a repository directory at a branch/tag/commit.

    Pass the returned next_cursor to retrieve the next page.
    Hidden paths are blocked.
    """
    config = get_configuration()
    expected_url = build_source_url(config, workspace, repository, ref, path)

    if cursor:
        expected = urlsplit(expected_url)
        supplied = urlsplit(cursor)

        if (
            supplied.scheme != "https"
            or supplied.netloc != expected.netloc
            or supplied.path != expected.path
            or supplied.fragment
            or not supplied.query
        ):
            raise ValueError("Invalid pagination cursor.")
        request_url = cursor
    else:
        request_url = f"{expected_url}?pagelen=100"

    data = request_json(config, request_url)
    entries = []

    for item in data.get("values", []):
        item_path = item.get("path", "")
        try:
            validate_path(item_path)
        except ValueError:
            continue

        entries.append(
            {
                "path": item_path,
                "type": item.get("type"),
                "size": item.get("size"),
            }
        )

    return {
        "workspace": workspace,
        "repository": repository,
        "ref": ref,
        "path": path,
        "entries": entries,
        "next_cursor": data.get("next"),
    }


@server.tool(name="bitbucket_read_file")
def read_file(
    workspace: str,
    repository: str,
    ref: str,
    path: str,
) -> dict:
    """Read one UTF-8 text file, up to 64 KiB, from an allowed repository."""
    config = get_configuration()
    validate_path(path)
    url = build_source_url(config, workspace, repository, ref, path)
    content = request_bytes(config, url, MAX_FILE_BYTES)

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("File is not UTF-8 text.") from None

    if "\x00" in text:
        raise ValueError("File appears to be binary.")

    return {
        "workspace": workspace,
        "repository": repository,
        "ref": ref,
        "path": path,
        "content": text,
    }
