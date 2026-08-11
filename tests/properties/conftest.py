"""Test bootstrap: make the src/ layout importable without installation.

Same pattern as tests/document/conftest.py and tests/json/conftest.py; lets
pytest import ``consema`` from a checkout before the toolchain/install gate
(docs/multi-language-implementation-plan.md section 3, section 7) is
closed. It touches no project files.
"""

from __future__ import annotations

import pathlib
import sys

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
