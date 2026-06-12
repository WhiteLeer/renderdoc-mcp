"""Helpers for loading the historical MCP server code."""

from __future__ import annotations

import importlib.util
import sys
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def load_legacy_renderdoc_server():
    repo_root = Path(__file__).resolve().parents[3]
    mcp_root = repo_root / "mcp"
    legacy_path = mcp_root / "renderdoc_mcp_server.py"
    if not legacy_path.exists():
        raise FileNotFoundError(str(legacy_path))

    repo_root_resolved = repo_root.resolve()
    sys.path[:] = [p for p in sys.path if Path(p).resolve() != repo_root_resolved]
    mcp_root_str = str(mcp_root)
    if mcp_root_str not in sys.path:
        sys.path.insert(0, mcp_root_str)

    spec = importlib.util.spec_from_file_location("renderdoc_mcp_server_legacy", str(legacy_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load legacy server module from {legacy_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
