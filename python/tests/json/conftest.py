"""Test bootstrap: make the src/ layout importable without installation.

Same pattern as tests/document/conftest.py; lets pytest import ``consema``
from a checkout; the editable install (pip install -e '.[dev]') makes
this bootstrap redundant, but it keeps plain pytest workable too. It touches
no project files.
"""

from __future__ import annotations

import pathlib
import sys

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
