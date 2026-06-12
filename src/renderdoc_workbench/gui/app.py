"""GUI application launcher."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ..adapters.filesystem.service import FilesystemService
from ..adapters.renderdoc.service import RenderDocService
from ..adapters.reporting.service import ReportingService
from ..adapters.targets.service import TargetSessionAdapter
from ..core.services import ServiceRegistry
from .main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    services = ServiceRegistry(
        targets=TargetSessionAdapter(),
        filesystem=FilesystemService(),
        renderdoc=RenderDocService(),
        reporting=ReportingService(),
    )
    window = MainWindow(services)
    window.show()
    return app.exec()
