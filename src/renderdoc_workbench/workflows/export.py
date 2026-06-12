"""Analysis export workflow."""

from __future__ import annotations

from pathlib import Path

from ..core.models import AnalysisSummary, ExportResult
from ..core.services import ServiceRegistry


class ExportWorkflow:
    def __init__(self, services: ServiceRegistry) -> None:
        self._services = services

    def run(self, summary: AnalysisSummary, output_root: Path) -> ExportResult:
        return self._services.reporting.export_summary(summary, output_root)

