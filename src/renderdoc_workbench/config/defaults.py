"""Default paths and names for the workbench."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "analysis"
DEFAULT_RDC_DIR = Path(r"C:\Program Files\NetEase\captures")
