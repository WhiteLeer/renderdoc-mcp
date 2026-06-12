"""RDC analysis workflow."""

from __future__ import annotations

from pathlib import Path

from ..core.models import AnalysisSummary
from ..core.services import ServiceRegistry


class AnalysisWorkflow:
    def __init__(self, services: ServiceRegistry) -> None:
        self._services = services

    def run(self, rdc_path: Path) -> AnalysisSummary:
        return self._services.renderdoc.analyze_rdc(rdc_path)

