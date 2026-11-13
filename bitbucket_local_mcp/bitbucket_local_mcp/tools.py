from __future__ import annotations

from urllib.parse import quote, urlsplit
from pathlib import PurePosixPath

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .bitbucket_cli import (
    API_BASE_URL,
    MAX_DIFF_BYTES,
    MAX_FILE_BYTES,
    build_source_url,
    get_configuration,
    request_bytes,
    request_json,
    validate_path,
    validate_pull_request_id,
    validate_ref,
    validate_repository,
)

_MAX_RANGE_LINES = 400
_MAX_CRAWL_FILES = 200
_MAX_CRAWL_BYTES_TOTAL = 256 * 1024
_MAX_CRAWL_FILE_BYTES = 32 * 1024

_ALLOWED_CRAWL_SUFFIXES = {
    ".md",
    ".txt",
    ".rst",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".go",
    ".rs",
    ".cs",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".sql",
    ".sh",
    ".dockerfile",
}

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


@server.tool(name="bitbucket_read_file_range")
def read_file_range(
    workspace: str,
    repository: str,
    ref: str,
    path: str,
    start_line: int,
    end_line: int,
) -> dict:
    """Read a line range from a UTF-8 text file (bounded).

    Lines are 1-based and inclusive.
    The returned range is capped to prevent large responses.
    """
    start = int(start_line or 0)
    end = int(end_line or 0)
    if start <= 0 or end <= 0 or end < start:
        raise ValueError("start_line and end_line must be positive, and end_line must be >= start_line.")

    if (end - start + 1) > _MAX_RANGE_LINES:
        raise ValueError(f"Requested range is too large. Max lines per request: {_MAX_RANGE_LINES}.")

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

    lines = text.splitlines()
    total_lines = len(lines)

    start_idx = min(start - 1, total_lines)
    end_idx = min(end, total_lines)
    selected = lines[start_idx:end_idx]

    return {
        "workspace": workspace,
        "repository": repository,
        "ref": ref,
        "path": path,
        "start_line": start,
        "end_line": end,
        "returned_start_line": start if start <= total_lines else None,
        "returned_end_line": (start + len(selected) - 1) if selected else None,
        "total_lines": total_lines,
        "content": "\n".join(selected),
        "truncated": end > total_lines,
    }


@server.tool(name="bitbucket_workspace_identity")
def workspace_identity() -> dict:
    """Return basic identity information for the configured Bitbucket credentials."""
    config = get_configuration()
    data = request_json(config, f"{API_BASE_URL}/user")
    return {
        "username": data.get("username"),
        "display_name": data.get("display_name"),
        "uuid": data.get("uuid"),
        "account_id": data.get("account_id"),
    }


@server.tool(name="bitbucket_list_branches")
def list_branches(workspace: str, repository: str, limit: int = 50) -> dict:
    """List branches for an allowlisted repository."""
    config = get_configuration()
    validate_repository(config, workspace, repository)
    safe_limit = max(1, min(limit, 100))
    url = f"{API_BASE_URL}/repositories/{workspace}/{repository}/refs/branches?pagelen={safe_limit}"
    data = request_json(config, url)

    branches = []
    for item in data.get("values", []):
        target = item.get("target") or {}
        branches.append(
            {
                "name": item.get("name"),
                "type": item.get("type"),
                "target_hash": target.get("hash"),
                "target_date": target.get("date"),
            }
        )

    return {"workspace": workspace, "repository": repository, "branches": branches, "next_cursor": data.get("next")}


@server.tool(name="bitbucket_list_tags")
def list_tags(workspace: str, repository: str, limit: int = 50) -> dict:
    """List tags for an allowlisted repository."""
    config = get_configuration()
    validate_repository(config, workspace, repository)
    safe_limit = max(1, min(limit, 100))
    url = f"{API_BASE_URL}/repositories/{workspace}/{repository}/refs/tags?pagelen={safe_limit}"
    data = request_json(config, url)

    tags = []
    for item in data.get("values", []):
        target = item.get("target") or {}
        tags.append(
            {
                "name": item.get("name"),
                "type": item.get("type"),
                "target_hash": target.get("hash"),
                "target_date": target.get("date"),
            }
        )

    return {"workspace": workspace, "repository": repository, "tags": tags, "next_cursor": data.get("next")}


@server.tool(name="bitbucket_get_commit")
def get_commit(workspace: str, repository: str, commit_hash: str) -> dict:
    """Get commit metadata (no diff) for an allowlisted repository."""
    config = get_configuration()
    validate_repository(config, workspace, repository)

    normalized_hash = (commit_hash or "").strip()
    if not normalized_hash:
        raise ValueError("commit_hash must not be empty.")

    url = f"{API_BASE_URL}/repositories/{workspace}/{repository}/commit/{quote(normalized_hash, safe='')}"
    data = request_json(config, url)

    author = data.get("author") or {}
    summary = data.get("summary") or {}

    return {
        "workspace": workspace,
        "repository": repository,
        "hash": data.get("hash", normalized_hash),
        "date": data.get("date"),
        "message": summary.get("raw") or summary.get("html") or data.get("message"),
        "author": author.get("raw") or author.get("user"),
        "parents": [p.get("hash") for p in (data.get("parents") or []) if isinstance(p, dict)],
        "links": data.get("links"),
        "raw": data,
    }


def _is_crawl_candidate(path: str) -> bool:
    name = PurePosixPath(path).name
    suffix = PurePosixPath(name.lower()).suffix
    if name.lower() == "dockerfile":
        return True
    return suffix in _ALLOWED_CRAWL_SUFFIXES


@server.tool(name="bitbucket_crawl_repository")
def crawl_repository(
    workspace: str,
    repository: str,
    ref: str,
    root_path: str = "",
    max_files: int = 50,
    max_total_bytes: int = _MAX_CRAWL_BYTES_TOTAL,
    include_suffixes: list[str] | None = None,
    exclude_paths: list[str] | None = None,
    include_paths: list[str] | None = None,
) -> dict:
    """Crawl a repository tree and extract bounded text snippets for knowledge indexing.

    - Walks the Bitbucket /src/{ref}/{path} listing recursively (paginated).
    - Downloads only safe, allowlisted text/code files.
    - Enforces strict caps on file count and total bytes returned.

    Optional filters:
    - include_suffixes: override/extend the default suffix allowlist (e.g. [".md", ".py"]).
    - include_paths: only include files under these path prefixes.
    - exclude_paths: skip files under these path prefixes.

    This is read-only and respects the existing hidden/blocked path policy.
    """
    config = get_configuration()
    validate_repository(config, workspace, repository)
    validate_ref(ref)
    validate_path(root_path, allow_empty=True)

    safe_max_files = max(1, min(int(max_files or 50), _MAX_CRAWL_FILES))
    safe_max_total = max(1024, min(int(max_total_bytes or _MAX_CRAWL_BYTES_TOTAL), _MAX_CRAWL_BYTES_TOTAL))

    suffix_allowlist = None
    if include_suffixes:
        suffix_allowlist = {s.lower() for s in include_suffixes if isinstance(s, str) and s.strip()}

    include_prefixes = [p.strip().strip("/") for p in (include_paths or []) if isinstance(p, str) and p.strip()]
    exclude_prefixes = [p.strip().strip("/") for p in (exclude_paths or []) if isinstance(p, str) and p.strip()]

    def _prefix_match(prefixes: list[str], path: str) -> bool:
        normalized = path.strip("/")
        return any(normalized == p or normalized.startswith(p + "/") for p in prefixes)

    def _should_consider(path: str) -> bool:
        if include_prefixes and not _prefix_match(include_prefixes, path):
            return False
        if exclude_prefixes and _prefix_match(exclude_prefixes, path):
            return False
        return True

    def _is_candidate(path: str) -> bool:
        if not _should_consider(path):
            return False
        name = PurePosixPath(path).name
        if name.lower() == "dockerfile":
            return True
        suffix = PurePosixPath(name.lower()).suffix
        if suffix_allowlist is not None:
            return suffix in suffix_allowlist
        return suffix in _ALLOWED_CRAWL_SUFFIXES

    queue: list[str] = [root_path]
    visited: set[str] = set()

    extracted: list[dict] = []
    skipped: list[dict] = []
    total_bytes = 0
    files_considered = 0

    while queue and len(extracted) < safe_max_files and total_bytes < safe_max_total:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)

        cursor = ""
        while len(extracted) < safe_max_files and total_bytes < safe_max_total:
            listing = list_files(workspace, repository, ref, path=current, cursor=cursor)
            cursor = listing.get("next_cursor") or ""

            for entry in listing.get("entries", []):
                entry_path = entry.get("path") or ""
                entry_type = entry.get("type")

                if not entry_path:
                    continue

                if entry_type == "commit_directory":
                    if entry_path not in visited:
                        queue.append(entry_path)
                    continue

                if entry_type != "commit_file":
                    continue

                files_considered += 1

                if not _is_candidate(entry_path):
                    skipped.append({"path": entry_path, "reason": "filtered_or_unsupported"})
                    continue

                remaining = safe_max_total - total_bytes
                if remaining <= 0:
                    break

                per_file_cap = min(_MAX_CRAWL_FILE_BYTES, remaining)

                try:
                    url = build_source_url(config, workspace, repository, ref, entry_path)
                    content = request_bytes(config, url, per_file_cap)
                    text = content.decode("utf-8")
                    if "\x00" in text:
                        skipped.append({"path": entry_path, "reason": "binary"})
                        continue
                except UnicodeDecodeError:
                    skipped.append({"path": entry_path, "reason": "non_utf8"})
                    continue
                except ValueError as exc:
                    skipped.append({"path": entry_path, "reason": str(exc)})
                    continue

                extracted.append(
                    {
                        "path": entry_path,
                        "bytes": len(content),
                        "content": text,
                    }
                )
                total_bytes += len(content)

                if len(extracted) >= safe_max_files or total_bytes >= safe_max_total:
                    break

            if not cursor:
                break

    return {
        "workspace": workspace,
        "repository": repository,
        "ref": ref,
        "root_path": root_path,
        "extracted_files": extracted,
        "extracted_count": len(extracted),
        "skipped": skipped[:200],
        "files_considered": files_considered,
        "filters": {
            "include_suffixes": sorted(suffix_allowlist) if suffix_allowlist is not None else None,
            "include_paths": include_prefixes or None,
            "exclude_paths": exclude_prefixes or None,
        },
        "limits": {
            "max_files": safe_max_files,
            "max_total_bytes": safe_max_total,
            "max_file_bytes": _MAX_CRAWL_FILE_BYTES,
        },
        "truncated": len(extracted) >= safe_max_files or total_bytes >= safe_max_total,
    }


@server.tool(name="bitbucket_list_pull_requests")
def list_pull_requests(
    workspace: str,
    repository: str,
    state: str = "OPEN",
    limit: int = 50,
) -> dict:
    """List pull requests for an allowlisted repository."""
    config = get_configuration()
    validate_repository(config, workspace, repository)

    normalized_state = state.strip().upper() if state else "OPEN"
    allowed_states = {"OPEN", "MERGED", "DECLINED", "SUPERSEDED"}
    if normalized_state not in allowed_states:
        raise ValueError(f"Invalid state. Use one of: {', '.join(sorted(allowed_states))}.")

    safe_limit = max(1, min(limit, 100))
    url = (
        f"{API_BASE_URL}/repositories/{workspace}/{repository}/pullrequests"
        f"?state={normalized_state}&pagelen={safe_limit}"
    )
    data = request_json(config, url)

    prs = []
    for item in data.get("values", []):
        author = (item.get("author") or {}).get("display_name")
        source = (item.get("source") or {}).get("branch") or {}
        dest = (item.get("destination") or {}).get("branch") or {}
        prs.append(
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "state": item.get("state"),
                "author": author,
                "source_branch": source.get("name"),
                "destination_branch": dest.get("name"),
                "updated_on": item.get("updated_on"),
                "links": item.get("links"),
            }
        )

    return {"workspace": workspace, "repository": repository, "pull_requests": prs, "next_cursor": data.get("next")}


@server.tool(name="bitbucket_get_pull_request")
def get_pull_request(workspace: str, repository: str, pull_request_id: int) -> dict:
    """Get details for a single pull request by id."""
    config = get_configuration()
    validate_repository(config, workspace, repository)
    pr_id = validate_pull_request_id(pull_request_id)

    url = f"{API_BASE_URL}/repositories/{workspace}/{repository}/pullrequests/{pr_id}"
    data = request_json(config, url)

    author = (data.get("author") or {}).get("display_name")
    source = (data.get("source") or {}).get("branch") or {}
    dest = (data.get("destination") or {}).get("branch") or {}

    return {
        "workspace": workspace,
        "repository": repository,
        "id": data.get("id", pr_id),
        "title": data.get("title"),
        "state": data.get("state"),
        "description": data.get("description"),
        "author": author,
        "source_branch": source.get("name"),
        "destination_branch": dest.get("name"),
        "created_on": data.get("created_on"),
        "updated_on": data.get("updated_on"),
        "links": data.get("links"),
        "raw": data,
    }


@server.tool(name="bitbucket_get_pull_request_bundle")
def get_pull_request_bundle(
    workspace: str,
    repository: str,
    pull_request_id: int,
    files_limit: int = 100,
    comments_limit: int = 50,
    commits_limit: int = 50,
) -> dict:
    """Get a bounded bundle of pull request context.

    Returns pull request details plus:
    - diffstat files (first page)
    - recent comments (first page)
    - commits (first page)

    This reduces round-trips for chat agents.
    """
    config = get_configuration()
    validate_repository(config, workspace, repository)
    pr_id = validate_pull_request_id(pull_request_id)

    safe_files = max(1, min(files_limit, 100))
    safe_comments = max(1, min(comments_limit, 100))
    safe_commits = max(1, min(commits_limit, 100))

    pr = get_pull_request(workspace, repository, pr_id)
    files = list_pull_request_files(workspace, repository, pr_id, limit=safe_files, cursor="")
    comments = list_pull_request_comments(workspace, repository, pr_id, limit=safe_comments, cursor="")
    commits = list_pull_request_commits(workspace, repository, pr_id, limit=safe_commits, cursor="")

    return {
        "workspace": workspace,
        "repository": repository,
        "pull_request_id": pr_id,
        "pull_request": pr,
        "files": files.get("files"),
        "files_next_cursor": files.get("next_cursor"),
        "comments": comments.get("comments"),
        "comments_next_cursor": comments.get("next_cursor"),
        "commits": commits.get("commits"),
        "commits_next_cursor": commits.get("next_cursor"),
        "limits": {
            "files": safe_files,
            "comments": safe_comments,
            "commits": safe_commits,
        },
    }


@server.tool(name="bitbucket_list_pull_request_files")
def list_pull_request_files(
    workspace: str,
    repository: str,
    pull_request_id: int,
    limit: int = 100,
    cursor: str = "",
) -> dict:
    """List changed files for a pull request using the diffstat endpoint.

    Pass the returned next_cursor to retrieve the next page.
    """
    config = get_configuration()
    validate_repository(config, workspace, repository)
    pr_id = validate_pull_request_id(pull_request_id)

    safe_limit = max(1, min(limit, 100))
    expected_base = f"{API_BASE_URL}/repositories/{workspace}/{repository}/pullrequests/{pr_id}/diffstat"

    if cursor:
        expected = urlsplit(expected_base)
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
        request_url = f"{expected_base}?pagelen={safe_limit}"

    data = request_json(config, request_url)

    files: list[dict] = []
    for item in data.get("values", []):
        file_info = item.get("file") or {}
        path = file_info.get("path") or ""
        try:
            validate_path(path)
        except ValueError:
            continue

        files.append(
            {
                "path": path,
                "type": file_info.get("type"),
                "lines_added": item.get("lines_added"),
                "lines_removed": item.get("lines_removed"),
                "status": item.get("status"),
            }
        )

    return {
        "workspace": workspace,
        "repository": repository,
        "pull_request_id": pr_id,
        "files": files,
        "next_cursor": data.get("next"),
    }


@server.tool(name="bitbucket_get_file_metadata")
def get_file_metadata(workspace: str, repository: str, ref: str, path: str) -> dict:
    """Return metadata for a file without downloading its full contents."""
    config = get_configuration()
    validate_path(path)
    validate_ref(ref)
    validate_repository(config, workspace, repository)

    encoded_ref = quote(ref, safe="")
    encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
    url = f"{API_BASE_URL}/repositories/{workspace}/{repository}/src/{encoded_ref}/{encoded_path}?format=meta"
    data = request_json(config, url)

    commit = data.get("commit") or {}
    return {
        "workspace": workspace,
        "repository": repository,
        "ref": ref,
        "path": data.get("path", path),
        "size": data.get("size"),
        "type": data.get("type"),
        "mimetype": data.get("mimetype"),
        "commit_hash": commit.get("hash"),
        "commit_date": commit.get("date"),
    }


@server.tool(name="bitbucket_search_code")
def search_code(
    workspace: str,
    repository: str,
    query: str,
    limit: int = 25,
) -> dict:
    """Search code in a repository if the Bitbucket API supports it for your plan.

    If the API endpoint is unavailable, returns a clear error.
    """
    config = get_configuration()
    validate_repository(config, workspace, repository)

    q = (query or "").strip()
    if not q:
        raise ValueError("query must not be empty.")

    safe_limit = max(1, min(limit, 50))
    url = (
        f"{API_BASE_URL}/repositories/{workspace}/{repository}/search/code"
        f"?search_query={quote(q, safe='')}&pagelen={safe_limit}"
    )

    try:
        data = request_json(config, url)
    except ValueError as exc:
        message = str(exc)
        if "HTTP 404" in message or "HTTP 405" in message:
            raise ValueError(
                "Bitbucket code search endpoint is unavailable for this workspace/repository or plan."
            ) from None
        raise

    matches = []
    for item in data.get("values", []):
        file_info = item.get("file") or {}
        matches.append(
            {
                "path": file_info.get("path"),
                "type": item.get("type"),
                "content_match_count": item.get("content_match_count"),
            }
        )

    return {
        "workspace": workspace,
        "repository": repository,
        "query": q,
        "matches": matches,
        "next_cursor": data.get("next"),
    }


@server.tool(name="bitbucket_get_pull_request_diff")
def get_pull_request_diff(
    workspace: str,
    repository: str,
    pull_request_id: int,
    max_bytes: int = MAX_DIFF_BYTES,
) -> dict:
    """Fetch the unified diff for a pull request (bounded text).

    Uses the Bitbucket Cloud pull request diff endpoint.
    """
    config = get_configuration()
    validate_repository(config, workspace, repository)
    pr_id = validate_pull_request_id(pull_request_id)

    safe_max = max(1, min(int(max_bytes or MAX_DIFF_BYTES), MAX_DIFF_BYTES))
    url = f"{API_BASE_URL}/repositories/{workspace}/{repository}/pullrequests/{pr_id}/diff"
    content = request_bytes(config, url, safe_max)

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Diff response is not UTF-8 text.") from None

    if "\x00" in text:
        raise ValueError("Diff response appears to be binary.")

    return {
        "workspace": workspace,
        "repository": repository,
        "pull_request_id": pr_id,
        "max_bytes": safe_max,
        "diff": text,
        "truncated": len(content) >= safe_max,
    }


@server.tool(name="bitbucket_list_pull_request_comments")
def list_pull_request_comments(
    workspace: str,
    repository: str,
    pull_request_id: int,
    limit: int = 50,
    cursor: str = "",
) -> dict:
    """List pull request comments (paginated)."""
    config = get_configuration()
    validate_repository(config, workspace, repository)
    pr_id = validate_pull_request_id(pull_request_id)

    safe_limit = max(1, min(limit, 100))
    expected_base = f"{API_BASE_URL}/repositories/{workspace}/{repository}/pullrequests/{pr_id}/comments"

    if cursor:
        expected = urlsplit(expected_base)
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
        request_url = f"{expected_base}?pagelen={safe_limit}"

    data = request_json(config, request_url)

    comments: list[dict] = []
    for item in data.get("values", []):
        user = item.get("user") or {}
        inline = item.get("inline") or {}
        comments.append(
            {
                "id": item.get("id"),
                "created_on": item.get("created_on"),
                "updated_on": item.get("updated_on"),
                "content": (item.get("content") or {}).get("raw"),
                "user": user.get("display_name") or user.get("nickname") or user.get("username"),
                "inline": {
                    "path": inline.get("path"),
                    "from": inline.get("from"),
                    "to": inline.get("to"),
                }
                if inline
                else None,
                "deleted": item.get("deleted"),
            }
        )

    return {
        "workspace": workspace,
        "repository": repository,
        "pull_request_id": pr_id,
        "comments": comments,
        "next_cursor": data.get("next"),
    }


@server.tool(name="bitbucket_list_pull_request_commits")
def list_pull_request_commits(
    workspace: str,
    repository: str,
    pull_request_id: int,
    limit: int = 50,
    cursor: str = "",
) -> dict:
    """List commits for a pull request (paginated)."""
    config = get_configuration()
    validate_repository(config, workspace, repository)
    pr_id = validate_pull_request_id(pull_request_id)

    safe_limit = max(1, min(limit, 100))
    expected_base = f"{API_BASE_URL}/repositories/{workspace}/{repository}/pullrequests/{pr_id}/commits"

    if cursor:
        expected = urlsplit(expected_base)
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
        request_url = f"{expected_base}?pagelen={safe_limit}"

    data = request_json(config, request_url)

    commits: list[dict] = []
    for item in data.get("values", []):
        author = item.get("author") or {}
        commits.append(
            {
                "hash": item.get("hash"),
                "date": item.get("date"),
                "message": item.get("message"),
                "author": author.get("raw") or author.get("user"),
                "links": item.get("links"),
            }
        )

    return {
        "workspace": workspace,
        "repository": repository,
        "pull_request_id": pr_id,
        "commits": commits,
        "next_cursor": data.get("next"),
    }


@server.tool(name="bitbucket_get_commit_diff")
def get_commit_diff(
    workspace: str,
    repository: str,
    commit_hash: str,
    max_bytes: int = MAX_DIFF_BYTES,
) -> dict:
    """Fetch the unified diff for a commit (bounded text)."""
    config = get_configuration()
    validate_repository(config, workspace, repository)

    normalized_hash = (commit_hash or "").strip()
    if not normalized_hash:
        raise ValueError("commit_hash must not be empty.")

    safe_max = max(1, min(int(max_bytes or MAX_DIFF_BYTES), MAX_DIFF_BYTES))
    url = f"{API_BASE_URL}/repositories/{workspace}/{repository}/diff/{quote(normalized_hash, safe='')}"
    content = request_bytes(config, url, safe_max)

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Diff response is not UTF-8 text.") from None

    if "\x00" in text:
        raise ValueError("Diff response appears to be binary.")

    return {
        "workspace": workspace,
        "repository": repository,
        "commit_hash": normalized_hash,
        "max_bytes": safe_max,
        "diff": text,
        "truncated": len(content) >= safe_max,
    }
