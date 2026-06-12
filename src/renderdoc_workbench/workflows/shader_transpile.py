"""Directory-level shader transpile workflow."""

from __future__ import annotations

from typing import Callable
from pathlib import Path

from ..core.models import ShaderTranspileResult
from ..core.services import ServiceRegistry


class ShaderTranspileWorkflow:
    def __init__(self, services: ServiceRegistry) -> None:
        self._services = services

    def run(
        self,
        source_root: Path,
        output_root: Path | None = None,
        *,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> ShaderTranspileResult:
        return self._services.renderdoc.transpile_shader_catalog(
            source_root,
            save_root_dir=output_root,
            progress_callback=progress_callback,
        )
