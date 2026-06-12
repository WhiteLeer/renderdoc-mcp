"""Target launch and attach workflow."""

from __future__ import annotations

from pathlib import Path

from ..core.models import LaunchRequest, LaunchResult
from ..core.services import ServiceRegistry


class LaunchWorkflow:
    def __init__(self, services: ServiceRegistry) -> None:
        self._services = services

    def run(self, target_root: Path, package_name: str | None = None) -> LaunchResult:
        request = LaunchRequest(target_root=target_root, package_name=package_name)
        return self._services.targets.launch_and_attach(request)
