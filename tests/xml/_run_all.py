"""Minimal pytest-free runner for the xml intent tests.

The tests are intent documents written before the Python toolchain
verification gate (docs/multi-language-implementation-plan.md §3/§7);
this runner executes every test function directly (injecting the
session fixture) so the suite can be sanity-checked before pytest
lands. No gate is claimed to have passed.
"""

from __future__ import annotations

import inspect
import json
import sys

sys.path.insert(0, r"C:\Users\franck\Documents\consema\python\src")
sys.path.insert(0, r"C:\Users\franck\Documents\consema\python\tests\xml")

VECTOR_PATH = r"C:\Users\franck\Documents\consema\conformance\vectors\xml-1-0-safe-v1.json"


class _ExpectationFailed(Exception):
    pass


class _PytestStub:
    """Minimal pytest stand-in so the intent tests import without pytest."""

    def fixture(self, *args, **kwargs):
        def decorator(function):
            return function

        return decorator

    def raises(self, expected):
        return _RaisesContext(expected)


class _RaisesContext:
    def __init__(self, expected):
        self.expected = expected
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            raise _ExpectationFailed(
                f"expected {self.expected} to be raised, nothing raised"
            )
        if isinstance(self.expected, tuple) and all(
            isinstance(item, type) and issubclass(item, BaseException)
            for item in self.expected
        ):
            if any(issubclass(exc_type, item) for item in self.expected):
                self.value = exc_value
                return True
            return False
        if isinstance(self.expected, type) and issubclass(self.expected, BaseException):
            if issubclass(exc_type, self.expected):
                self.value = exc_value
                return True
            return False
        actual = getattr(exc_value, "kind", None)
        if actual is self.expected or (
            isinstance(self.expected, tuple) and actual in self.expected
        ):
            self.value = exc_value
            return True
        return False


sys.modules.setdefault("pytest", _PytestStub())


def _raises_ok(expected_kind, fn):
    """Minimal pytest.raises stand-in."""
    try:
        fn()
    except Exception as error:  # noqa: BLE001 - the assertion inspects the kind
        actual = getattr(error, "kind", None)
        if actual is not None and actual is expected_kind:
            return error
        if isinstance(expected_kind, tuple) and actual in expected_kind:
            return error
        raise _ExpectationFailed(
            f"expected {expected_kind}, got {type(error).__name__}: {error}"
        ) from error
    raise _ExpectationFailed(f"expected {expected_kind} to be raised, nothing raised")


def main():
    import test_edit
    import test_formation
    import test_materialization
    import test_projection
    import test_query
    import test_registry

    with open(VECTOR_PATH, encoding="utf-8") as handle:
        vectors = json.load(handle)
    modules = [
        test_formation,
        test_query,
        test_projection,
        test_materialization,
        test_edit,
        test_registry,
    ]
    passed = 0
    failed = 0
    for module in modules:
        for name, function in inspect.getmembers(module, inspect.isfunction):
            if not name.startswith("test_"):
                continue
            signature = inspect.signature(function)
            kwargs = {}
            if "xml_vectors" in signature.parameters:
                kwargs["xml_vectors"] = vectors
            try:
                function(**kwargs)
                print(f"PASS {module.__name__}.{name}")
                passed += 1
            except Exception as error:  # noqa: BLE001
                print(f"FAIL {module.__name__}.{name}: {type(error).__name__}: {error}")
                failed += 1
    print(f"intent tests: {passed} passed, {failed} failed")


if __name__ == "__main__":
    main()
