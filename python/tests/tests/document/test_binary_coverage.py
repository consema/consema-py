"""Binary structural coverage tests.

Golden cases transcribed from conformance/vectors/source-v1.json:
- ``source.binary.empty-coverage`` (lines 101-106): source_len 0, no regions
  -> region_count 0;
- ``source.binary.region-coverage`` (lines 107-112): source_len 4, regions
  [0,1) header, [1,4) payload -> region_count 2;
- ``source.binary.reject-gap`` (lines 113-118): regions [0,1) and [2,4) leave
  a gap -> expected code "IncompleteStructuralCoverage".

Contract: RFC 0003 §7 (docs/rfcs/0003-source-syntax-query-and-patch-v1.md:
162-171) — binary coverage obeys the no-gap/no-overlap/final-length
invariant; empty source has an empty valid index; non-empty source requires
at least one non-empty region. Arbitration: crates/consema-document/
src/lib.rs:531-579 (BinaryStructuralIndex) and 582-604 (LocationError).
"""

from __future__ import annotations

import pytest

from consema.document import (
    BinaryRegion,
    BinaryStructuralIndex,
    DocumentAuthority,
    LocationError,
    LocationErrorKind,
    NodeRole,
)


def test_empty_coverage() -> None:
    """Vector case source.binary.empty-coverage."""
    authority = DocumentAuthority.fresh()
    index = BinaryStructuralIndex.new(authority.identity, 0, [])
    assert index.region_count() == 0


def test_region_coverage() -> None:
    """Vector case source.binary.region-coverage."""
    authority = DocumentAuthority.fresh()
    regions = [
        BinaryRegion(
            node=authority.node_ref(0, NodeRole.BINARY_REGION),
            span=authority.span(0, 1),
            kind="header",
        ),
        BinaryRegion(
            node=authority.node_ref(1, NodeRole.BINARY_REGION),
            span=authority.span(1, 4),
            kind="payload",
        ),
    ]
    index = BinaryStructuralIndex.new(authority.identity, 4, regions)
    assert index.region_count() == 2
    assert index.regions[0].kind == "header"
    assert index.regions[1].span.start_byte == 1


def test_reject_gap() -> None:
    """Vector case source.binary.reject-gap: expected code
    IncompleteStructuralCoverage."""
    authority = DocumentAuthority.fresh()
    regions = [
        BinaryRegion(
            node=authority.node_ref(0, NodeRole.BINARY_REGION),
            span=authority.span(0, 1),
            kind="header",
        ),
        BinaryRegion(
            node=authority.node_ref(1, NodeRole.BINARY_REGION),
            span=authority.span(2, 4),
            kind="payload",
        ),
    ]
    with pytest.raises(LocationError) as caught:
        BinaryStructuralIndex.new(authority.identity, 4, regions)
    assert caught.value.kind is LocationErrorKind.INCOMPLETE_STRUCTURAL_COVERAGE
    assert caught.value.name == "IncompleteStructuralCoverage"


def test_rejects_wrong_role_empty_kind_and_duplicate_identity() -> None:
    """BinaryStructuralIndex validation (lib.rs:538-572)."""
    authority = DocumentAuthority.fresh()
    wrong_role = BinaryRegion(
        node=authority.node_ref(0, NodeRole.TOKEN),
        span=authority.span(0, 1),
        kind="header",
    )
    with pytest.raises(LocationError) as caught:
        BinaryStructuralIndex.new(authority.identity, 1, [wrong_role])
    assert caught.value.kind is LocationErrorKind.WRONG_ROLE

    empty_kind = BinaryRegion(
        node=authority.node_ref(0, NodeRole.BINARY_REGION),
        span=authority.span(0, 1),
        kind="",
    )
    with pytest.raises(LocationError) as caught:
        BinaryStructuralIndex.new(authority.identity, 1, [empty_kind])
    assert caught.value.kind is LocationErrorKind.INVALID_BINARY_REGION_KIND

    duplicate = [
        BinaryRegion(
            node=authority.node_ref(0, NodeRole.BINARY_REGION),
            span=authority.span(0, 1),
            kind="a",
        ),
        BinaryRegion(
            node=authority.node_ref(0, NodeRole.BINARY_REGION),
            span=authority.span(1, 2),
            kind="b",
        ),
    ]
    with pytest.raises(LocationError) as caught:
        BinaryStructuralIndex.new(authority.identity, 2, duplicate)
    assert caught.value.kind is LocationErrorKind.DUPLICATE_STRUCTURAL_IDENTITY

    foreign = BinaryRegion(
        node=DocumentAuthority.fresh().node_ref(0, NodeRole.BINARY_REGION),
        span=authority.span(0, 1),
        kind="a",
    )
    with pytest.raises(LocationError) as caught:
        BinaryStructuralIndex.new(authority.identity, 1, [foreign])
    assert caught.value.kind is LocationErrorKind.WRONG_SNAPSHOT
