"""Workflow-friendly service containers."""

from __future__ import annotations

from dataclasses import dataclass

from .protocols import FilesystemAdapter, RenderDocAdapter, ReportingAdapter, TargetAdapter


@dataclass(frozen=True)
class ServiceRegistry:
    targets: TargetAdapter
    filesystem: FilesystemAdapter
    renderdoc: RenderDocAdapter
    reporting: ReportingAdapter

