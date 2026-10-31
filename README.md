# Databricks Local MCP

Minimal local MCP server in Python for Databricks.

This starter currently targets the MCP Python SDK 1.x API.

## What it provides

- A local MCP server over HTTP on localhost
- Tools:
  - `databricks_ping`: workspace identity
  - `databricks_list_catalogs()`: visible Unity Catalog catalogs
  - `databricks_list_schemas(catalog_name)`: schema names in a Unity Catalog catalog
  - `databricks_list_tables(catalog_name, schema_name)`: table names in a schema
  - `databricks_get_table_columns(catalog_name, schema_name, table_name)`: column metadata for a table
  - `databricks_describe_table(catalog_name, schema_name, table_name)`: detailed table metadata
  - `databricks_get_table_sample(catalog_name, schema_name, table_name, limit=10)`: sample rows from a table
  - `databricks_run_sql(statement, limit=100)`: run a read-only SQL statement against the configured warehouse
  - `databricks_search_tables(query, max_results=25)`: search visible tables by name or comment
  - `databricks_list_warehouses()`: visible Databricks SQL warehouses
- One starter resource: `config://databricks`
- Browser-based Databricks OAuth login endpoints

## Requirements

- Python 3.10+
- Databricks authentication available through either:
  - existing local Databricks CLI authentication that `databricks-sdk` can resolve
  - `DATABRICKS_HOST` and `DATABRICKS_TOKEN`

## Setup

Prerequisites:

- Python 3.10 or newer
- Databricks CLI (optional, needed for the profile-based login)

1. Go to the project folder:

```bash
cd /Users/HUSSASX87/Documents/CDT/APPTLM/__MCP
```

2. Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

3. Install the package and its dependencies (`mcp` and `databricks-sdk`):

```bash
python -m pip install -U pip
python -m pip install -e .
```

4. Confirm the server imports correctly:

```bash
python -c "import src.server"
```

The `.venv/` folder is local to your machine. Do not commit it. Recreate it with the steps above on any new machine.

## Run

Preferred flow with a Databricks CLI profile:

```bash
export DATABRICKS_CONFIG_PROFILE="<profile-name>"
python -m src.server
```

This uses the same local Databricks auth/profile resolution that `databricks-sdk` uses.

If you have not logged in yet, create or refresh the profile first:

```bash
databricks auth login --host https://<your-workspace>.cloud.databricks.com --profile <profile-name>
```

You can verify it with:

```bash
databricks auth profiles
databricks current-user me --profile <profile-name>
```

Other run options:

```bash
databricks-local-mcp
```

Or:

```bash
python -m src.server
```

By default this starts a local HTTP MCP server on `127.0.0.1:8000`.

You can override the bind address with environment variables:

```bash
export MCP_HOST="127.0.0.1"
export MCP_PORT="8000"
```

Then connect your MCP client to the local server URL on that host and port.

The MCP endpoint is `http://127.0.0.1:8000/mcp` (Streamable HTTP). For Open WebUI in Docker, use `http://host.docker.internal:8000/mcp`.

The server checks the `Origin` header (DNS rebinding protection). `http://127.0.0.1`, `http://localhost`, and `https://chat.adc-cyber.org` are allowed by default. To allow another front end, set a comma-separated list:

```bash
export MCP_ALLOWED_ORIGINS="https://my-webui.example.com"
```

## Token-based env

```bash
export DATABRICKS_HOST="https://<your-workspace>.cloud.databricks.com"
export DATABRICKS_TOKEN="<your-token>"
```

## Browser login

If you do not want to manage a personal access token, you can authenticate through your browser.

1. Start the server:

```bash
python -m src.server
```

2. Open this URL in your browser:

```text
http://127.0.0.1:8000/
```

The root page includes links for login and auth status.

3. Complete the Databricks login flow.

4. Check auth status:

```text
http://127.0.0.1:8000/auth/status
```

If OAuth initialization fails, the server now returns a JSON error response instead of appearing to hang or loop.

After the callback completes, the server keeps the OAuth access token in memory for the current process.

Optional environment variables:

```bash
export DATABRICKS_HOST="https://<your-workspace>.cloud.databricks.com"
export DATABRICKS_CLIENT_ID="<your-oauth-app-client-id>"
export DATABRICKS_CLIENT_SECRET="<optional-client-secret>"
export DATABRICKS_OAUTH_REDIRECT_URL="http://127.0.0.1:8000/oauth/callback"
```

For browser OAuth, the Databricks OAuth application must have the callback URL registered exactly as:

```text
http://127.0.0.1:8000/oauth/callback
```

The built-in `databricks-cli` client is not suitable for this local browser callback flow unless that redirect URI is registered for it in your workspace.

Temporary local-only SSL bypass:

```bash
export DATABRICKS_INSECURE_SKIP_VERIFY="true"
```

Use that only for local testing when Python cannot validate your Databricks workspace certificate chain. Do not use it in shared environments or production.

## Example prompts

### Catalog and schema discovery

- `List catalogs I can access.`
- `Show schemas in dev-adc-dmz-catalog-us-east-2.`
- `List tables in schema compatibility_json in catalog dev-adc-dmz-catalog-us-east-2.`

### Table inspection

- `Describe table dev-adc-dmz-catalog-us-east-2.compatibility_json.my_table.`
- `Show columns for my_table in schema compatibility_json and catalog dev-adc-dmz-catalog-us-east-2.`
- `Give me 5 sample rows from dev-adc-dmz-catalog-us-east-2.compatibility_json.my_table.`

### Search

- `Search for tables with segment in the name.`
- `Find tables related to orders.`
- `Search visible tables for compatibility.`

### Warehouses

- `List SQL warehouses.`
- `Show my available warehouses.`

### SQL examples

- `Run SQL: SELECT current_catalog(), current_schema()`
- `Run SQL: SHOW CATALOGS`
- `Run SQL: SHOW SCHEMAS IN \`dev-adc-dmz-catalog-us-east-2\``
- `Run SQL: SHOW TABLES IN \`dev-adc-dmz-catalog-us-east-2\`.\`compatibility_json\``
- `Run SQL: SELECT * FROM \`dev-adc-dmz-catalog-us-east-2\`.\`compatibility_json\`.\`my_table\` LIMIT 10`
- `Run SQL: SELECT count(*) AS row_count FROM \`dev-adc-dmz-catalog-us-east-2\`.\`compatibility_json\`.\`my_table\``

### Natural prompts

- `Find the schemas in my dev catalog.`
- `Show me what tables exist in compatibility_json.`
- `Inspect the columns of the customer table.`
- `Sample a few rows from the logs table.`

## Next steps

- Add tools for listing clusters, warehouses, jobs, or Unity Catalog objects
- Add profile selection support if you want to target multiple workspaces
- Add tests around tool behavior with mocked `WorkspaceClient`
