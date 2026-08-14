"""Core portable-value query executor unit tests.

Covers the ordered-result cursor semantics (RFC 0003) and the wave-4 R12
step accounting: the Rust lazy step counter (consema-core/src/query.rs
``LazyContext::step``) counts one step per pull with a saturating add, and
the pull that would exceed ``max_steps`` fails with ResourceLimitExceeded.
"""

from __future__ import annotations

import pytest

from consema.conformance.core_query import CoreCursor, CoreMatch
from consema.core.value import PortableValue
from consema.protocol import QueryFailure, QueryFailureKind


def _match(value: object, kind: str = "Value", ordinal: int = 0) -> CoreMatch:
    return CoreMatch(kind, ordinal, PortableValue.integer(value) if isinstance(value, int) else value)


def test_cursor_step_accounting_counts_pulls():
    cursor = CoreCursor(
        [_match(1), _match(2), _match(3)],
        max_steps=2,
    )
    assert cursor.next() is not None
    assert cursor.next() is not None
    # The third pull would exceed max_steps=2 -> ResourceLimitExceeded.
    with pytest.raises(QueryFailure) as caught:
        cursor.next()
    assert caught.value.kind is QueryFailureKind.RESOURCE_LIMIT


def test_cursor_step_accounting_does_not_limit_results_when_within_bounds():
    cursor = CoreCursor(
        [_match(1), _match(2), _match(3)],
        max_steps=10,
    )
    yielded = 0
    while cursor.next() is not None:
        yielded += 1
    assert yielded == 3
    assert cursor.terminal_state() == "Completed"


def test_cursor_step_accounting_with_max_results_interplay():
    # max_steps gates the number of pulls; max_results gates the yielded
    # results — a bound hit on results stops pulls before the step budget.
    cursor = CoreCursor(
        [_match(1), _match(2), _match(3)],
        max_results=2,
        max_steps=100,
    )
    yielded = 0
    while cursor.next() is not None:
        yielded += 1
    assert yielded == 2
    assert cursor.terminal_state() == "Failed"


def test_cursor_without_step_budget_is_unbounded():
    cursor = CoreCursor([_match(1), _match(2)])
    yielded = 0
    while cursor.next() is not None:
        yielded += 1
    assert yielded == 2
