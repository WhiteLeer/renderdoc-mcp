"""CLI entrypoint for renderdoc_workbench."""

from __future__ import annotations

import argparse

from ..adapters.mcp import legacy_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="renderdoc-workbench")
    parser.add_argument("--legacy-mcp-server", action="store_true", help="Run the historical MCP server entrypoint.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.legacy_mcp_server:
        legacy_server.main()
        return 0
    from ..gui import app as gui_app

    return gui_app.main()


if __name__ == "__main__":
    raise SystemExit(main())
