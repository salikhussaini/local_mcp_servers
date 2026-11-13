# Local MCP Servers (Databricks + Bitbucket)

This repo contains **two local MCP servers**:

- [databricks_local_mcp/](./databricks_local_mcp) — Databricks tools over MCP
- [bitbucket_local_mcp/](./bitbucket_local_mcp) — Bitbucket Cloud tools over MCP

This top-level README shows how to run **both at the same time** using **4 VS Code integrated terminals**.

## Open 4 terminals in VS Code

In VS Code:

1. Open the repo root folder: `__MCP/`
2. Open the integrated terminal
3. Create 4 terminals (Terminal → **Split Terminal** until you have 4 panes)
4. Name them (right-click tab) as:
   - `BB-MCP`
   - `BB-MCPO`
   - `DBX-MCP`
   - `DBX-MCPO`

## Terminal 1: Bitbucket MCP server (BB-MCP)

```bash
cd /Users/HUSSASX87/Documents/CDT/APPTLM/__MCP
source .venv/bin/activate

# Required Bitbucket env
export BITBUCKET_EMAIL="me@example.com"
export BITBUCKET_API_TOKEN="..."
export BITBUCKET_ALLOWED_REPOS="my-workspace/repo-one,my-workspace/repo-two"

# Run Bitbucket MCP on 8000
export MCP_PORT=8000
python -m bitbucket_local_mcp
```

Smoke test:

```bash
curl -s http://127.0.0.1:8000/health | cat
```

## Terminal 2: Bitbucket mcpo bridge (BB-MCPO)

This exposes the Bitbucket MCP endpoint as an OpenAPI tool server for Open WebUI.

```bash
uvx --python 3.12 --from "mcpo==0.0.17" --with "mcp==1.12.4" --with "pydantic==2.11.7" --with "fastapi==0.115.14" \
  mcpo --host 0.0.0.0 --port 8001 --server-type streamable-http -- "http://127.0.0.1:8000/mcp"
```

Smoke test:

```bash
curl -s http://127.0.0.1:8001/openapi.json | head -n 5 | cat
```

## Terminal 3: Databricks MCP server (DBX-MCP)

```bash
cd /Users/HUSSASX87/Documents/CDT/APPTLM/__MCP
source .venv/bin/activate

# Required Databricks env
export DATABRICKS_CONFIG_PROFILE="<your-profile>"

# Run Databricks MCP on 8002 (so it doesn't collide with Bitbucket)
export MCP_PORT=8002
python -m databricks_local_mcp
```

Smoke test:

```bash
curl -s http://127.0.0.1:8002/health | cat
```

## Terminal 4: Databricks mcpo bridge (DBX-MCPO)

```bash
uvx --python 3.12 --from "mcpo==0.0.17" --with "mcp==1.12.4" --with "pydantic==2.11.7" --with "fastapi==0.115.14" \
  mcpo --host 0.0.0.0 --port 8003 --server-type streamable-http -- "http://127.0.0.1:8002/mcp"
```

Smoke test:

```bash
curl -s http://127.0.0.1:8003/openapi.json | head -n 5 | cat
```

## Port map (recommended)

- Bitbucket MCP: `8000` → mcpo: `8001`
- Databricks MCP: `8002` → mcpo: `8003`

## Notes

- If you haven’t installed both packages into the repo venv yet, follow the install steps in:
  - [bitbucket_local_mcp/README.md](./bitbucket_local_mcp/README.md)
  - [databricks_local_mcp/README.md](./databricks_local_mcp/README.md)
- The MCP URL should be used **without** a trailing slash: `http://127.0.0.1:<port>/mcp`.
