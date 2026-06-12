"""Root launcher for the new GUI workbench.

This launcher prefers the repository's bundled Python runtime instead of the
user's desktop/default Python installation.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


_RUNTIME_SENTINEL = "RENDERDOC_WORKBENCH_RUNTIME_OK"


def _preferred_python(repo_root: Path) -> Path | None:
    candidates = [
        repo_root / "_ext_renderdoc_trial" / "python313" / "python.exe",
        repo_root / "_ext_renderdoc_trial" / ".venv313" / "Scripts" / "python.exe",
        repo_root / "_ext_renderdoc_trial" / ".venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _ensure_repo_runtime() -> int | None:
    repo_root = Path(__file__).resolve().parent
    preferred = _preferred_python(repo_root)
    if preferred is None:
        return None

    current = Path(sys.executable).resolve()
    if current == preferred.resolve():
        return None

    if os.environ.get(_RUNTIME_SENTINEL) == "1":
        return None

    env = os.environ.copy()
    env[_RUNTIME_SENTINEL] = "1"
    args = [str(preferred), str(Path(__file__).resolve()), *sys.argv[1:]]
    return subprocess.call(args, env=env, cwd=str(repo_root))


def _bootstrap() -> None:
    repo_root = Path(__file__).resolve().parent
    src_root = repo_root / "src"
    repo_root_resolved = repo_root.resolve()
    sys.path[:] = [p for p in sys.path if Path(p).resolve() != repo_root_resolved]
    sys.path.insert(0, str(src_root))


def main() -> int:
    handoff = _ensure_repo_runtime()
    if handoff is not None:
        return handoff

    _bootstrap()
    from renderdoc_workbench.cli.main import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
