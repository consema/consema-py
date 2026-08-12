"""FormationStatus closure and structural identity tests.

FormationStatus is a closed two-value enum (Complete, Recovered) per RFC 0016
§5.1 (docs/rfcs/0016-go-api-mapping-v1.md:171-176) and
crates/consema-document/src/lib.rs:405-411; the 0.13.0 review's F10
disposition pins this as the only formation-status surface (no alias).
"""

from __future__ import annotations

from consema.document import (
    DocumentAuthority,
    FormationStatus,
    LocationError,
    LocationErrorKind,
    LosslessStructuralIndex,
    NodeRef,
    NodeRole,
    StructuralPiece,
    StructuralPieceKind,
)


def test_formation_status_is_closed_two_value_enum() -> None:
    members = list(FormationStatus)
    assert members == [FormationStatus.COMPLETE, FormationStatus.RECOVERED]
    assert FormationStatus.COMPLETE.value == "Complete"
    assert FormationStatus.RECOVERED.value == "Recovered"
    assert FormationStatus.COMPLETE is not FormationStatus.RECOVERED


def test_snapshot_identities_are_fresh_and_distinct() -> None:
    first = DocumentAuthority.fresh()
    second = DocumentAuthority.fresh()
    assert first.identity != second.identity


def test_span_validation_and_properties() -> None:
    authority = DocumentAuthority.fresh()
    span = authority.span(2, 5)
    assert span.start_byte == 2
    assert span.end_byte == 5
    assert span.len() == 3
    assert not span.is_empty()
    assert authority.span(4, 4).is_empty()
    try:
        authority.span(5, 2)
        raise AssertionError("inverted span must be rejected")
    except LocationError as error:
        assert error.kind is LocationErrorKind.INVERTED_SPAN
        assert error.name == "InvertedSpan"


def test_node_refs_are_snapshot_bound() -> None:
    first = DocumentAuthority.fresh()
    second = DocumentAuthority.fresh()
    node = first.node_ref(0, NodeRole.VALUE)
    assert node.snapshot == first.identity
    assert node.role is NodeRole.VALUE
    assert node.index == 0
    try:
        second.verify(node)
        raise AssertionError("foreign node handle must be rejected")
    except LocationError as error:
        assert error.kind is LocationErrorKind.WRONG_SNAPSHOT
    first.verify(node)  # own handle verifies


def test_node_role_vocabulary_is_frozen() -> None:
    """The closed NodeRole vocabulary carries the exact Rust spellings
    (lib.rs:113-251); spot-check the L1-adjacent members."""
    assert NodeRole.BINARY_REGION.value == "BinaryRegion"
    assert NodeRole.JSON_SYNTAX_PIECE.value == "JsonSyntaxPiece"
    assert NodeRole.TOML_SYNTAX_PIECE.value == "TomlSyntaxPiece"
    assert NodeRole.VALUE.value == "Value"


def test_lossless_index_exact_coverage() -> None:
    """Exhaustive Token/Trivia/ErrorRegion coverage with the no-gap/
    no-overlap/final-length invariant (lib.rs:458-483; RFC 0003 §7)."""
    authority = DocumentAuthority.fresh()
    pieces = [
        StructuralPiece(authority.span(0, 1), StructuralPieceKind.TOKEN),
        StructuralPiece(authority.span(1, 2), StructuralPieceKind.TRIVIA),
        StructuralPiece(authority.span(2, 3), StructuralPieceKind.TOKEN),
    ]
    index = LosslessStructuralIndex.new(authority.identity, 3, pieces)
    assert len(index.pieces) == 3

    with_gap = [
        StructuralPiece(authority.span(0, 1), StructuralPieceKind.TOKEN),
        StructuralPiece(authority.span(2, 3), StructuralPieceKind.TOKEN),
    ]
    try:
        LosslessStructuralIndex.new(authority.identity, 3, with_gap)
        raise AssertionError("gap must be rejected")
    except LocationError as error:
        assert error.kind is LocationErrorKind.INCOMPLETE_STRUCTURAL_COVERAGE
        assert error.name == "IncompleteStructuralCoverage"
