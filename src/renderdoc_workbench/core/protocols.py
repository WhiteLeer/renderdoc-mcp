"""Service protocols for adapters and workflows."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Protocol

from .models import AnalysisSummary, ExportResult, LaunchRequest, LaunchResult, RdcEntry, ShaderCatalogResult, ShaderTranspileResult


class TargetAdapter(Protocol):
    def discover_packages(self, target_root: Path) -> Iterable[str]:
        ...

    def launch_and_attach(self, request: LaunchRequest) -> LaunchResult:
        ...


class FilesystemAdapter(Protocol):
    def scan_rdc_entries(self, rdc_root: Path) -> Iterable[RdcEntry]:
        ...


class RenderDocAdapter(Protocol):
    def open_rdc(self, rdc_path: Path) -> None:
        ...

    def analyze_rdc(self, rdc_path: Path) -> AnalysisSummary:
        ...

    def collect_shader_catalog(
        self,
        rdc_root: Path,
        *,
        save_root_dir: Path | None = None,
        renderdoc_dir: str | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> ShaderCatalogResult:
        ...

    def transpile_shader_catalog(
        self,
        source_root: Path,
        *,
        save_root_dir: Path | None = None,
        renderdoc_dir: str | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> ShaderTranspileResult:
        ...


class ReportingAdapter(Protocol):
    def export_summary(self, summary: AnalysisSummary, output_root: Path) -> ExportResult:
        ...
