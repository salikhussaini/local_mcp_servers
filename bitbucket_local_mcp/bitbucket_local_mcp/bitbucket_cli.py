from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable
from urllib.parse import quote

import httpx

API_BASE_URL = "https://api.bitbucket.org/2.0"
MAX_FILE_BYTES = 64 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_DIFF_BYTES = 512 * 1024
MAX_ALLOWED_REPOS = 20
REQUEST_TIMEOUT_SECONDS = 20

_client: httpx.Client | None = None
_client_auth: tuple[str, str] | None = None

# Defense in depth only: this list cannot identify every sensitive file.
BLOCKED_FILE_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
}
BLOCKED_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks"}

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
REF_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
PULL_REQUEST_ID_PATTERN = re.compile(r"^[0-9]+$")


@dataclass(frozen=True)
class BitbucketConfig:
    email: str
    token: str
    allowed_repos: frozenset[str]


def get_configuration() -> BitbucketConfig:
    """Read credentials and repository allowlist from the environment."""
    email = os.environ.get("BITBUCKET_EMAIL", "").strip()
    token = os.environ.get("BITBUCKET_API_TOKEN", "").strip()
    raw_repos = os.environ.get("BITBUCKET_ALLOWED_REPOS", "")

    if not email or not token:
        raise ValueError(
            "Set BITBUCKET_EMAIL and BITBUCKET_API_TOKEN before starting the MCP server."
        )

    allowed_repos = {
        item.strip().lower() for item in raw_repos.split(",") if item.strip()
    }

    if not allowed_repos or len(allowed_repos) > MAX_ALLOWED_REPOS:
        raise ValueError(
            f"Set BITBUCKET_ALLOWED_REPOS to 1–{MAX_ALLOWED_REPOS} comma-separated workspace/repository pairs."
        )

    for repo in allowed_repos:
        parts = repo.split("/")
        if len(parts) != 2 or not all(
            IDENTIFIER_PATTERN.fullmatch(part) for part in parts
        ):
            raise ValueError(f"Invalid repository allowlist entry: {repo!r}")

    return BitbucketConfig(email=email, token=token, allowed_repos=frozenset(allowed_repos))


def validate_repository(config: BitbucketConfig, workspace: str, repository: str) -> None:
    """Reject repositories outside the configured allowlist."""
    if not IDENTIFIER_PATTERN.fullmatch(workspace):
        raise ValueError("Invalid workspace.")
    if not IDENTIFIER_PATTERN.fullmatch(repository):
        raise ValueError("Invalid repository.")

    if f"{workspace}/{repository}".lower() not in config.allowed_repos:
        raise ValueError("Repository is not in BITBUCKET_ALLOWED_REPOS.")


def validate_ref(ref: str) -> None:
    """Validate a branch, tag, or commit identifier."""
    if not REF_PATTERN.fullmatch(ref) or ref.startswith("/") or ".." in ref:
        raise ValueError("Invalid ref. Use a branch, tag, or commit identifier.")


def validate_pull_request_id(pull_request_id: int | str) -> int:
    """Validate and normalize a pull request id."""
    value = str(pull_request_id).strip()
    if not value or not PULL_REQUEST_ID_PATTERN.fullmatch(value):
        raise ValueError("Invalid pull_request_id.")
    normalized = int(value)
    if normalized <= 0:
        raise ValueError("Invalid pull_request_id.")
    return normalized


def validate_path(path: str, allow_empty: bool = False) -> None:
    """Validate a repository-relative path and block common secret filenames."""
    if not path and allow_empty:
        return

    if not path or path.startswith("/") or "\\" in path:
        raise ValueError("Use a non-empty repository-relative path.")

    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Path contains an invalid component.")

    if any(part.startswith(".") for part in parts):
        raise ValueError("Hidden files and directories are blocked.")

    name = PurePosixPath(path).name.lower()
    if (
        name in BLOCKED_FILE_NAMES
        or name.startswith(".env.")
        or PurePosixPath(name).suffix in BLOCKED_SUFFIXES
    ):
        raise ValueError("This file is blocked by the local safety policy.")


def build_source_url(
    config: BitbucketConfig,
    workspace: str,
    repository: str,
    ref: str,
    path: str = "",
) -> str:
    """Build a Bitbucket source endpoint URL."""
    validate_repository(config, workspace, repository)
    validate_ref(ref)
    validate_path(path, allow_empty=True)

    encoded_ref = quote(ref, safe="")
    encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
    base = f"{API_BASE_URL}/repositories/{workspace}/{repository}/src/{encoded_ref}"
    return f"{base}/{encoded_path}" if path else f"{base}/"


def _iter_bytes_bounded(response: httpx.Response, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total_bytes = 0
    for chunk in response.iter_bytes():
        total_bytes += len(chunk)
        if total_bytes > max_bytes:
            raise ValueError(f"Response exceeds the {max_bytes}-byte limit.")
        chunks.append(chunk)
    return b"".join(chunks)


def _get_client(config: BitbucketConfig) -> httpx.Client:
    global _client, _client_auth

    auth = (config.email, config.token)
    if _client is None or _client_auth != auth:
        if _client is not None:
            _client.close()
        _client = httpx.Client(
            auth=auth,
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
            headers={"Accept": "*/*"},
        )
        _client_auth = auth
    return _client


def request_bytes(config: BitbucketConfig, url: str, max_bytes: int) -> bytes:
    """Make a bounded, authenticated GET request without following redirects."""
    client = _get_client(config)

    try:
        with client.stream("GET", url) as response:
            if response.is_redirect:
                raise ValueError(
                    "Bitbucket returned a redirect; this server does not follow redirects."
                )
            response.raise_for_status()
            return _iter_bytes_bounded(response, max_bytes)

    except httpx.HTTPStatusError as exc:
        raise ValueError(
            f"Bitbucket request failed with HTTP {exc.response.status_code}."
        ) from None
    except httpx.RequestError:
        raise ValueError("Could not connect to Bitbucket Cloud.") from None


def request_json(config: BitbucketConfig, url: str) -> dict:
    """Get a bounded JSON response."""
    try:
        result = json.loads(request_bytes(config, url, MAX_JSON_BYTES))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("Bitbucket did not return valid JSON.") from None

    if not isinstance(result, dict):
        raise ValueError("Unexpected Bitbucket response.")
    return result


def normalize_allowed_repos(config: BitbucketConfig) -> list[tuple[str, str]]:
    """Return allowlisted repos as (workspace, repo) tuples."""
    pairs: list[tuple[str, str]] = []
    for repo in sorted(config.allowed_repos):
        workspace, repository = repo.split("/", 1)
        pairs.append((workspace, repository))
    return pairs


def validate_paths(paths: Iterable[str]) -> list[str]:
    """Filter a list of paths to those allowed by validate_path."""
    allowed: list[str] = []
    for path in paths:
        try:
            validate_path(path)
        except ValueError:
            continue
        allowed.append(path)
    return allowed


def close_client() -> None:
    """Close the shared HTTP client (used by server shutdown hooks/tests)."""
    global _client, _client_auth
    if _client is not None:
        _client.close()
    _client = None
    _client_auth = None

def mask_token(token: str) -> str:
    token = token.strip()
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]}"
