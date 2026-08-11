"""Native HCL semantic model (RFC 0014 §6).

The model is the schema-free HCL body tree — not a JSON Object tree, not a
Terraform typed object, and not an evaluated value. The root document owns
one body; body items preserve source order; attribute and block identity
are per-occurrence, never merged. Unlike the plist value model, there is no
shared-identity arena: every body item is an independent ordered tree node
with its own exact spans.

Frozen semantics (RFC 0014 §6): duplicate attributes are excluded at
formation and never enter the native model (RFC 0014 §3); duplicate
object-constructor keys, duplicate block occurrences, and
attribute/block name sharing are preserved as ordered native facts with
independent spans; the model contains syntax, never computed values — no
variable binding, function table, template expansion, or iteration exists;
and no application types exist (no variable declaration, resource,
provider, schema, or type-checking role, hard gate 2).

Every node carries a deterministic pre-order ``ordinal`` assigned at
formation (root body first, then each item in source order; an attribute
consumes one ordinal for itself and then every node of its expression
subtree in ``children()`` source order; a block consumes one ordinal for
itself, one per label, and then its nested body's items —
crates/consema-hcl/src/projection.rs:124-130). Query and projection issue
snapshot-bound NodeRefs from these ordinals.

Authority: crates/consema-hcl/src/native.rs (types and accessors,
native.rs:28-325); HclSyntaxKind spellings are frozen in
consema.hcl.kinds (RFC 0014 §7.2).
"""

from __future__ import annotations

from dataclasses import dataclass

from consema.document.structural import Span
from consema.hcl.expression import HclExpression


@dataclass(frozen=True, slots=True)
class HclBody:
    """Ordered body item container (RFC 0014 §6; native.rs:67-103).

    A body holds attributes and blocks interleaved in source order; the
    root body of a document and every nested block body share this
    container.
    """

    items: tuple[HclBodyItem, ...]
    ordinal: int = 0


@dataclass(frozen=True, slots=True)
class HclBodyItem:
    """One body item: an attribute or a block occurrence (RFC 0014 §4.2,
    §6; native.rs:105-136).

    Identity is per-occurrence: an attribute and a block may share a name
    in one body, blocks of the same type and labels may repeat, and every
    occurrence keeps its own spans; nothing is merged or resolved.
    """

    attribute: HclAttribute | None = None
    block: HclBlock | None = None

    @classmethod
    def of_attribute(cls, attribute: HclAttribute) -> HclBodyItem:
        return cls(attribute=attribute)

    @classmethod
    def of_block(cls, block: HclBlock) -> HclBodyItem:
        return cls(block=block)

    def as_attribute(self) -> HclAttribute | None:
        return self.attribute

    def as_block(self) -> HclBlock | None:
        return self.block


@dataclass(frozen=True, slots=True)
class HclAttribute:
    """One attribute occurrence: name, equals sign, and expression (RFC
    0014 §4.2, §6; native.rs:138-193).

    The expression is a first-class native role with its own exact span;
    the attribute's full source range is the union of the name, equals,
    and expression spans.
    """

    name: str
    name_span: Span
    equals_span: Span
    expression: HclExpression
    ordinal: int = 0


@dataclass(frozen=True, slots=True)
class HclBlock:
    """One block occurrence: type, ordered labels, and nested body (RFC
    0014 §4.2, §6; native.rs:195-251).

    A one-line block is the same native shape with at most one attribute
    and no nested blocks. Keyword spellings are valid block types, and
    blocks of the same type and labels may repeat with per-occurrence
    identity.
    """

    block_type: str
    labels: tuple[HclBlockLabel, ...]
    body: HclBody
    span: Span
    ordinal: int = 0


@dataclass(frozen=True, slots=True)
class HclBlockLabel:
    """One block label with its quote/naked fact (RFC 0014 §4.2, §6;
    native.rs:253-291).

    A label is either a naked identifier or a quoted literal string
    without interpolation; the ``quoted`` fact and the exact span preserve
    the source form.
    """

    text: str
    span: Span
    quoted: bool
    ordinal: int = 0


@dataclass(frozen=True, slots=True)
class HclErrorRegion:
    """One recovered HCL error region with its stable diagnostic code (RFC
    0014 §3, §7.2; native.rs:293-325)."""

    span: Span
    code: str
