"""Entry point for ``python -m consema.conformance`` — the conformance
runner CLI (RFC 0015 §5; the invocation documented in python/README.md).

The package ``__init__`` imports the runner machinery eagerly, so running
``python -m consema.conformance.runner`` would re-execute the module as
``__main__`` after it is already imported (a CPython runpy RuntimeWarning —
the module is imported twice under two names, but the 18-suite inventory is
a single list on the canonical module object). Under the default warning
filters that form still works, but under ``-W error::RuntimeWarning`` /
``PYTHONWARNINGS=error`` the warning becomes a hard failure and the CLI
aborts before running anything — the canonical entry is
``python -m consema.conformance``. This bootstrap instead
imports the canonical module once and calls its CLI function directly: no
re-execution, no warning, and the 18-suite inventory registered by
``consema.conformance.suites`` is the one used.
"""

from __future__ import annotations

import sys

from consema.conformance.runner import run_argv


def main() -> None:
    sys.exit(run_argv())


if __name__ == "__main__":
    main()
