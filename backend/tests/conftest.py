"""Make ``app`` importable when running pytest from anywhere.

These tests exercise the pure position -> count logic in
``app.vision.schmitt_counter`` and deliberately avoid importing the FastAPI app,
DB, or OpenCV, so they run fast with no camera / service dependencies.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
