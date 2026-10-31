# Databricks Local MCP

A local MCP server for Databricks that is meant to sit behind Open WebUI.

It starts an MCP endpoint on your machine, then `mcpo` can expose that endpoint to Open WebUI as an OpenAPI tool server.

## What this repo does

- Connects to Databricks with a Databricks CLI profile only
- Exposes Databricks tools over MCP at `http://127.0.0.1:8000/mcp`
- Lets Open WebUI use those tools through `mcpo`

## Tools included

- `databricks_ping`
- `databricks_list_catalogs`
- `databricks_list_schemas`
- `databricks_list_tables`
- `databricks_get_table_columns`
- `databricks_describe_table`
- `databricks_get_table_sample`
- `databricks_run_sql`
- `databricks_search_tables`
- `databricks_list_warehouses`

## Before you start

- Python 3.10+
- Databricks CLI installed if you want to use a CLI profile
- Open WebUI if you want to use this through chat

## Setup

1. Go to the project folder.

```bash
cd /path/to/__MCP
```

2. Create and activate a virtual environment.

```bash
python3 -m venv .venv
source .venv/bin/activate
```

3. Install the project.

```bash
python -m pip install -U pip
python -m pip install -e .
```

4. Confirm the server imports.

```bash
python -c "import src.server; print('import ok')"
```

## Choose Databricks auth

### Databricks CLI profile

This server now uses the Databricks CLI only. It reads the workspace URL from the selected CLI profile.

```bash
databricks auth login --host https://<your-workspace>.cloud.databricks.com --profile <profile-name>
export DATABRICKS_CONFIG_PROFILE="<profile-name>"
```

Example with a profile named `my-user`:

```bash
databricks auth login --host https://<your-workspace>.cloud.databricks.com --profile my-user
export DATABRICKS_CONFIG_PROFILE="my-user"
databricks current-user me --profile my-user
```

## Start the MCP server

Run the server in one terminal:

```bash
databricks-local-mcp
```

You can also run:

```bash
python -m src.server
```

Default server URLs:

- Home page: `http://127.0.0.1:8000/`
- MCP endpoint: `http://127.0.0.1:8000/mcp`
- Auth status: `http://127.0.0.1:8000/auth/status`
- Auth test: `http://127.0.0.1:8000/auth/test`
- CLI auth test: `http://127.0.0.1:8000/auth/test-cli`

## Connect it to Open WebUI

Make sure the MCP server from the previous step is running, then run `mcpo` in a second terminal.

Set the MCP endpoint as an environment variable:

```bash
export MCP_SERVER_URL="http://127.0.0.1:8000/mcp"
```

`--host 0.0.0.0` makes `mcpo` listen on all interfaces so Open WebUI can reach it:

```bash
uvx --python 3.12 --from "mcpo==0.0.17" --with "mcp==1.12.4" --with "pydantic==2.11.7" --with "fastapi==0.115.14" mcpo --host 0.0.0.0 --port 8001 --server-type streamable-http -- "$MCP_SERVER_URL"
```

Then in Open WebUI add the tool server:

1. Open **Settings**.
2. Open **Tools** or **Connections**.
3. Choose **Add connection** or **Add tool server**.
4. Select **OpenAPI**. Open WebUI fetches the spec from `<base URL>/openapi.json`, so enter only the base URL, without `/openapi.json`.
5. Enter the base URL that matches where Open WebUI runs:
   - Open WebUI on the same machine as `mcpo`: `http://127.0.0.1:8001`
   - Open WebUI in Docker on the same Mac: `http://host.docker.internal:8001`
   - Open WebUI on another machine: `http://<Mac-LAN-IP>:8001` (find the IP with `ipconfig getifaddr en0`)
6. Save the connection.
7. Open a new chat and enable the added tool server if Open WebUI asks.

### Import the connection from `example.json`

Instead of filling in the form by hand, you can import [example.json](./example.json):

1. Open the **Add connection** or **Edit Connection** dialog as described above.
2. Click **Import** and select `example.json`.
3. Confirm the URL is `http://127.0.0.1:8001` and the spec path is `openapi.json`, then save.

Edit `url` in the file first if Open WebUI does not run on the same machine as `mcpo`. The same dialog has an **Export** button, which saves a working connection in the same format.

### Open WebUI hosted remotely (for example `chat.adc-cyber.org`)

A remote Open WebUI cannot reach `127.0.0.1` or a private `192.168.x.x` address on your Mac. Use one of these:

1. **Add it as a user tool server (recommended).** In Open WebUI go to your profile **Settings → Tools** (not **Admin Settings**) and add `http://127.0.0.1:8001`. User tool servers are called from your browser, so `127.0.0.1` points at your Mac. Do not use the LAN IP here, because browsers block `http://` calls from an `https://` page to a non-localhost address.
2. **Use a tunnel** so the Open WebUI backend can reach it. Install `cloudflared` (`brew install cloudflared`), then run:

   ```bash
   cloudflared tunnel --url http://127.0.0.1:8001
   ```

   Use the printed `https://...trycloudflare.com` URL as the base URL under **Admin Settings → External Tools**. `mcpo` has no API key by default, so anyone with that URL can call your Databricks tools. Stop the tunnel when you are done, or start `mcpo` with `--api-key <secret>` and enter the same key in Open WebUI.

If the user tool server shows "Failed to connect" even though `curl http://127.0.0.1:8001/openapi.json` works on your Mac, the browser is blocking the call from the `https://` page to `127.0.0.1`. Allow the site to reach your device, then reload the page:

- **Firefox:** when the prompt "Allow *site* to access other apps and services on this device?" appears, choose **Allow**. To change it later, click the padlock in the address bar → **Connection secure → More information → Permissions**, or open **Settings → Privacy & Security → Permissions**, and set **Access other apps and services on this device** to **Allow** for the Open WebUI site.
- **Chrome / Edge:** allow **Local network access** for the site under the padlock → **Site settings**.

If your browser has no such option, use a different browser or the tunnel option above.

To confirm `mcpo` is reachable from the Open WebUI host, run this there:

```bash
curl http://<base-URL-host>:8001/openapi.json
```

If it fails from another machine, allow incoming connections for Python or `uvx` in the macOS firewall, and confirm both machines are on the same network.

`127.0.0.1` always means the machine making the request. For a remote Open WebUI, it will not reach your Mac.

To verify `mcpo` locally, open:

```text
http://127.0.0.1:8001/docs
```

## SQL tools requirement

These tools need a SQL warehouse ID:

- `databricks_run_sql`
- `databricks_get_table_sample`
- usually any workflow that reads table rows through SQL

Set it before starting the server:

```bash
export DATABRICKS_SQL_WAREHOUSE_ID="<warehouse-id>"
```

## Important behavior

- TLS certificate verification is disabled by default in this server.
- To turn TLS verification back on, set:

```bash
export DATABRICKS_INSECURE_SKIP_VERIFY="false"
```

- The server allows browser origins from `127.0.0.1`, `localhost`, and `https://chat.adc-cyber.org` by default.
- To allow another frontend origin, set:

```bash
export MCP_ALLOWED_ORIGINS="https://my-webui.example.com"
```

- To change the bind host or port, set:

```bash
export MCP_HOST="127.0.0.1"
export MCP_PORT="8000"
```

## Quick test flow

1. Start `databricks-local-mcp`.
2. Open `http://127.0.0.1:8000/auth/status`.
3. Open `http://127.0.0.1:8000/auth/test`.
4. Start `mcpo`.
5. Open `http://127.0.0.1:8001/docs`.
6. Add the `mcpo` URL to Open WebUI.

## Example prompts

- `List catalogs I can access.`
- `Show schemas in dev-adc-dmz-catalog-us-east-2.`
- `List tables in schema compatibility_json in catalog dev-adc-dmz-catalog-us-east-2.`
- `Describe table dev-adc-dmz-catalog-us-east-2.compatibility_json.my_table.`
- `Give me 5 sample rows from dev-adc-dmz-catalog-us-east-2.compatibility_json.my_table.`
- `Search for tables with segment in the name.`
- `List SQL warehouses.`
- `Run SQL: SHOW CATALOGS`
