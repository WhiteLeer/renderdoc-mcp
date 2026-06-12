"""Domain models for the workbench."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass(frozen=True)
class LaunchRequest:
    target_root: Path
    package_name: Optional[str] = None


@dataclass(frozen=True)
class LaunchResult:
    process_id: Optional[int]
    attached: bool
    message: str


@dataclass(frozen=True)
class RdcEntry:
    path: Path
    display_name: str
    size_bytes: int = 0
    modified_unix_ts: float = 0.0


@dataclass(frozen=True)
class AnalysisSummary:
    rdc_path: Path
    title: str
    highlights: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExportResult:
    output_dir: Path
    written_files: List[Path] = field(default_factory=list)


@dataclass(frozen=True)
class ShaderCatalogResult:
    rdc_root: Path
    output_dir: Path
    rdc_count: int
    shader_count: int
    written_files: List[Path] = field(default_factory=list)
    top_shaders: List[dict] = field(default_factory=list)
    failed_rdc_files: List[dict] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ShaderTranspileResult:
    source_dir: Path
    output_dir: Path
    shader_count: int
    classification_output_dir: Path | None = None
    duplicate_shader_count: int = 0
    family_count: int = 0
    effect_count: int = 0
    role_count: int = 0
    core_count: int = 0
    summary_file: Path | None = None
    family_summary_file: Path | None = None
    effect_summary_file: Path | None = None
    role_summary_file: Path | None = None
    core_summary_file: Path | None = None
    written_files: List[Path] = field(default_factory=list)
    failed_shaders: List[dict] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class SessionState:
    target_root: Optional[Path] = None
    package_name: Optional[str] = None
    process_id: Optional[int] = None
    renderdoc_attached: bool = False
    selected_rdc: Optional[Path] = None
