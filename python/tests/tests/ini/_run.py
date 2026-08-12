"""Minimal standalone runner for the ini tests (pytest-free environment).

Emulates the small pytest surface the tests use (raises, mark.parametrize,
mark.skip) and executes every test_* function, expanding parametrized
cases. This is a verification harness for the blind-write period only; the
tests are written for pytest and must run under pytest once the toolchain
is installed.
"""

from __future__ import annotations

import importlib
import inspect
import sys
import traceback
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "python" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _Raises:
    def __init__(self, exc):
        self.exc = exc
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            raise AssertionError(f"DID NOT RAISE {self.exc}")
        if not issubclass(exc_type, self.exc):
            return False
        self.value = exc
        return True


class _Mark:
    @staticmethod
    def skip(reason=""):
        def deco(fn):
            fn._skip_reason = reason
            return fn
        return deco

    @staticmethod
    def parametrize(argnames, argvalues):
        def deco(fn):
            fn._parametrize = (argnames, argvalues)
            return fn
        return deco


fake = types.ModuleType("pytest")
fake.raises = _Raises
fake.mark = _Mark()
sys.modules["pytest"] = fake

MODULES = [
    "ini.test_formation",
    "ini.test_query",
    "ini.test_projection",
    "ini.test_materialization",
    "ini.test_edit",
    "ini.test_registry",
]

passed = 0
failed = 0
skipped = 0
failures = []
for module_name in MODULES:
    module = importlib.import_module(module_name)
    for name, fn in sorted(vars(module).items()):
        if not name.startswith("test_"):
            continue
        reason = getattr(fn, "_skip_reason", None)
        parametrize = getattr(fn, "_parametrize", None)
        if parametrize is not None:
            argnames, argvalues = parametrize
            if isinstance(argnames, str):
                names = [part.strip() for part in argnames.split(",")]
            else:
                names = list(argnames)
            for values in argvalues:
                if not isinstance(values, (tuple, list)):
                    values = (values,)
                kwargs = dict(zip(names, values))
                case = f"{module_name}.{name}{kwargs}"
                try:
                    if reason:
                        skipped += 1
                        continue
                    fn(**kwargs)
                    passed += 1
                    print(f"PASS {case}")
                except Exception:
                    failed += 1
                    failures.append(case)
                    print(f"FAIL {case}")
                    traceback.print_exc()
        else:
            case = f"{module_name}.{name}"
            try:
                if reason:
                    skipped += 1
                    print(f"SKIP {case}: {reason[:80]}")
                    continue
                fn()
                passed += 1
                print(f"PASS {case}")
            except Exception:
                failed += 1
                failures.append(case)
                print(f"FAIL {case}")
                traceback.print_exc()

print()
print(f"passed={passed} failed={failed} skipped={skipped}")
if failures:
    print("FAILED:", *failures, sep="\n  ")
    sys.exit(1)
