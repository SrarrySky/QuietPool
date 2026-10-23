"""Offline AMM surveillance experiments."""

import datetime
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# Bound thread parallelism before importing NumPy; explicit user settings prevail.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
# The frozen vendor subset is intentionally never rewritten by the harness.
sys.path.insert(0, str(ROOT / "vendor" / "backend"))


def pinax_date():
    """Return today's date as a compact "YYYYMMDD" snapshot suffix.

    Returns:
        String of the current local date in "%Y%m%d" format.
    """
    return datetime.date.today().strftime("%Y%m%d")
