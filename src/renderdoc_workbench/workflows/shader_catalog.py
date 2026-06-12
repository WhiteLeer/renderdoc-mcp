"""Directory-level shader catalog collection workflow."""

from __future__ import annotations

from typing import Callable
from pathlib import Path

from ..core.models import ShaderCatalogResult
from ..core.services import ServiceRegistry


class ShaderCatalogWorkflow:
    def __init__(self, services: ServiceRegistry) -> None:
        self._services = services

    def run(
        self,
        rdc_root: Path,
        output_root: Path | None = None,
        *,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> ShaderCatalogResult:
        return self._services.renderdoc.collect_shader_catalog(
            rdc_root,
            save_root_dir=output_root,
            progress_callback=progress_callback,
        )
