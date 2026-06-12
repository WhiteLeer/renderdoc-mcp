"""Compatibility wrapper for the historical MCP server entrypoint."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_legacy_module():
    repo_root = Path(__file__).resolve().parents[4]
    legacy_path = repo_root / "mcp" / "renderdoc_mcp_server.py"
    spec = importlib.util.spec_from_file_location("renderdoc_mcp_server", str(legacy_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load legacy server module from {legacy_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    module = _load_legacy_module()
    module.main()

