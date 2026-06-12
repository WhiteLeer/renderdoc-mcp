"""Filesystem adapter for RDC discovery and output directories."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

from ...core.models import RdcEntry


class FilesystemService:
    """Provides path normalization and RDC scanning."""

    def scan_rdc_entries(self, rdc_root: Path) -> Iterable[RdcEntry]:
        if not rdc_root.exists():
            return []
        results: List[RdcEntry] = []
        for path in sorted(rdc_root.glob("*.rdc"), key=lambda p: p.stat().st_mtime, reverse=True):
            stat = path.stat()
            results.append(
                RdcEntry(
                    path=path,
                    display_name=path.name,
                    size_bytes=stat.st_size,
                    modified_unix_ts=stat.st_mtime,
                )
            )
        return results

