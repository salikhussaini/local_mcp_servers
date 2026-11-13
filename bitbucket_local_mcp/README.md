# Bitbucket Local MCP

A local, read-only MCP server for Bitbucket Cloud.

This mirrors the structure of `databricks_local_mcp`:

- `src/bitbucket_cli.py`: configuration, validation, and HTTP helpers
- `src/tools.py`: MCP tools
- `src/server.py`: Starlette HTTP wrapper exposing `/`, `/health`, and `/mcp`

## Requirements

- Python 3.10+
- A Bitbucket Cloud API token with read access to the repositories you want to expose

## Configuration

Set these environment variables before starting:

- `BITBUCKET_EMAIL`: Bitbucket account email
- `BITBUCKET_API_TOKEN`: Bitbucket API token
- `BITBUCKET_ALLOWED_REPOS`: comma-separated allowlist of `workspace/repo` pairs

Example:

```bash
export BITBUCKET_EMAIL="me@example.com"
export BITBUCKET_API_TOKEN="..."
export BITBUCKET_ALLOWED_REPOS="my-workspace/repo-one,my-workspace/repo-two"
```

Optional server settings:

- `MCP_HOST` (default `127.0.0.1`)
- `MCP_PORT` (default `8000`)
- `MCP_LOG_LEVEL` (default `INFO`)
- `MCP_ALLOWED_ORIGINS`: comma-separated extra browser origins to allow (in addition to localhost)

## Install

From the `__MCP` folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e bitbucket_local_mcp
```

## Run

```bash
bitbucket-local-mcp
```

Endpoints:

- Home: `http://127.0.0.1:8000/`
- Health: `http://127.0.0.1:8000/health`
- MCP: `http://127.0.0.1:8000/mcp`

## Tools

- `list_repositories`: list repositories in the configured allowlist
- `list_files`: list files in a directory at a ref (branch/tag/commit)
- `read_file`: read a UTF-8 text file (bounded)

## Notes / safety

- This server is read-only.
- Hidden paths (anything with a `.` path segment) are blocked.
- Common secret filenames and key/cert suffixes are blocked as defense-in-depth.
- Responses are size-limited.
- Redirects are not followed.
