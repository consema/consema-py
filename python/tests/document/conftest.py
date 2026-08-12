"""Test bootstrap: make the src/ layout importable without installation.

The package uses a src/ layout (python/pyproject.toml:27-28,
packages = ["src/consema"]); this conftest lets pytest import ``consema``
from a checkout before the toolchain/install gate (docs/multi-language-
implementation-plan.md §3, §7) is closed. It touches no project files.
"""

from __future__ import annotations

import pathlib
import sys

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
