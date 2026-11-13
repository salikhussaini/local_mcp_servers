# Bitbucket Local MCP

A local, read-only MCP server for Bitbucket Cloud.

This mirrors the structure of `databricks_local_mcp`:

- `bitbucket_local_mcp/bitbucket_cli.py`: configuration, validation, and HTTP helpers
- `bitbucket_local_mcp/tools.py`: MCP tools
- `bitbucket_local_mcp/server.py`: Starlette HTTP wrapper exposing `/`, `/health`, and `/mcp`

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
- `MCP_LOG_DIR` (default `<repo>/logs`): base folder for log files
- `MCP_ALLOWED_ORIGINS`: comma-separated extra browser origins to allow (in addition to localhost)

## Logs

On startup, the server writes logs to:

- `logs/YYYY-MM-DD/bitbucket-local-mcp.log`

## Install

From the `__MCP` folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e bitbucket_local_mcp
```

## Run

Recommended (module form):

```bash
python -m bitbucket_local_mcp
```

Run on a specific port (example: 8002):

```bash
export MCP_PORT=8002
/Users/HUSSASX87/Documents/CDT/APPTLM/__MCP/.venv/bin/python -m bitbucket_local_mcp
```

Equivalent:

```bash
python -m bitbucket_local_mcp.server
```

Or via console script:

```bash
bitbucket-local-mcp
```

Endpoints:

- Home: `http://127.0.0.1:8000/`
- Health: `http://127.0.0.1:8000/health`
- Auth status: `http://127.0.0.1:8000/auth/status`
- Version: `http://127.0.0.1:8000/version`
- MCP: `http://127.0.0.1:8000/mcp`

## Expose to Open WebUI via `mcpo`

Run `bitbucket-local-mcp` in one terminal, then in another:

```bash
uvx --python 3.12 --from "mcpo==0.0.17" --with "mcp==1.12.4" --with "pydantic==2.11.7" --with "fastapi==0.115.14" \
  mcpo --host 0.0.0.0 --port 8001 --server-type streamable-http -- "http://127.0.0.1:8000/mcp"
```

If you started the MCP server on port 8002, point `mcpo` at 8002:

```bash
uvx --python 3.12 --from "mcpo==0.0.17" --with "mcp==1.12.4" --with "pydantic==2.11.7" --with "fastapi==0.115.14" \
  mcpo --host 0.0.0.0 --port 8001 --server-type streamable-http -- "http://127.0.0.1:8002/mcp"
```

Notes:

- Use the MCP URL **without** a trailing slash (`.../mcp`). Some clients follow redirects differently, and avoiding redirects is more reliable.
- The MCP endpoint is **streamable-http** (SSE). A simple `curl` smoke test must include an `Accept` header that allows both JSON and SSE:

```bash
curl -i -X POST http://127.0.0.1:8000/mcp \
  -H "content-type: application/json" \
  -H "accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
```

Then verify:

- OpenAPI spec: `http://127.0.0.1:8001/openapi.json`
- Docs: `http://127.0.0.1:8001/docs`

## Tools

- `bitbucket_list_repositories`: list repositories in the configured allowlist
- `bitbucket_list_files`: list files in a directory at a ref (branch/tag/commit)
- `bitbucket_read_file`: read a UTF-8 text file (bounded)
- `bitbucket_read_file_range`: read a line range from a UTF-8 text file (bounded)
- `bitbucket_list_pull_requests`: list pull requests for a repository
- `bitbucket_get_pull_request`: get details for a pull request by id
- `bitbucket_get_pull_request_bundle`: get PR details + files + comments + commits (bounded)
- `bitbucket_list_pull_request_files`: list changed files for a pull request (diffstat)
- `bitbucket_get_pull_request_diff`: fetch the unified diff for a pull request (bounded)
- `bitbucket_list_pull_request_comments`: list pull request comments (paginated)
- `bitbucket_list_pull_request_commits`: list commits for a pull request (paginated)
- `bitbucket_list_tags`: list tags for a repository
- `bitbucket_get_commit`: get commit metadata (no diff)
- `bitbucket_get_commit_diff`: fetch the unified diff for a commit (bounded)
- `bitbucket_crawl_repository`: crawl a repo tree and extract bounded text/code files for knowledge indexing

## Notes / safety

- This server is read-only.
- Hidden paths (anything with a `.` path segment) are blocked.
- Common secret filenames and key/cert suffixes are blocked as defense-in-depth.
- Responses are size-limited.
- Redirects are not followed.
