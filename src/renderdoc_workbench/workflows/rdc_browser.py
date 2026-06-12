"""RDC discovery and open workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ..core.models import RdcEntry
from ..core.services import ServiceRegistry


class RdcBrowserWorkflow:
    def __init__(self, services: ServiceRegistry) -> None:
        self._services = services

    def refresh(self, rdc_root: Path) -> Iterable[RdcEntry]:
        return self._services.filesystem.scan_rdc_entries(rdc_root)

    def open_selected(self, rdc_path: Path) -> None:
        self._services.renderdoc.open_rdc(rdc_path)

