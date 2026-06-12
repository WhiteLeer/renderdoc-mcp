"""Reporting adapter placeholder."""

from __future__ import annotations

from pathlib import Path

from ...core.models import AnalysisSummary, ExportResult


class ReportingService:
    """Exports analysis outputs without owning analysis logic."""

    def export_summary(self, summary: AnalysisSummary, output_root: Path) -> ExportResult:
        output_root.mkdir(parents=True, exist_ok=True)
        report_path = output_root / f"{summary.rdc_path.stem}_summary.txt"
        report_path.write_text("\n".join([summary.title, *summary.highlights]), encoding="utf-8")
        return ExportResult(output_dir=output_root, written_files=[report_path])

