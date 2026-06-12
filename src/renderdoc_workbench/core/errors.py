"""Domain errors for renderdoc_workbench."""


class WorkbenchError(Exception):
    """Base error for workbench domain failures."""


class ConfigurationError(WorkbenchError):
    """Raised when required paths or settings are missing."""


class SessionError(WorkbenchError):
    """Raised when target launch or RenderDoc attachment fails."""


class AnalysisError(WorkbenchError):
    """Raised when RDC analysis cannot proceed."""

