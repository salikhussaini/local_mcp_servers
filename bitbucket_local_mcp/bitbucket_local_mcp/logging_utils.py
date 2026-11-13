from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path


def configure_file_logging(*, app_name: str, log_level: str) -> Path:
    """Configure root logging to write to a dated log folder.

    Layout:
      <repo_root>/logs/YYYY-MM-DD/<app_name>.log

    The console handler is preserved (so `uvicorn` output still shows in the terminal),
    and a file handler is added for persistence.

    Environment variables:
      - MCP_LOG_DIR: override base log directory (default: <repo_root>/logs)
    """

    level = getattr(logging, (log_level or "INFO").upper(), logging.INFO)

    # Resolve repo root as: <package_dir>/.. (package_dir is .../<mcp_pkg>/src)
    package_dir = Path(__file__).resolve().parent
    repo_root = package_dir.parent.parent

    base_dir = Path(os.getenv("MCP_LOG_DIR", str(repo_root / "logs"))).expanduser().resolve()
    date_dir = base_dir / datetime.now().strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)

    log_path = date_dir / f"{app_name}.log"

    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Avoid duplicate file handlers (e.g., when running under a reloader).
    for handler in root.handlers:
        if isinstance(handler, logging.FileHandler) and getattr(handler, "baseFilename", None) == str(log_path):
            return log_path

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    return log_path
