"""HCL structural edit transactions: the six frozen operations with atomic
commit, dry-run plans, untouched-byte proofs, and SourcePatch derivation
(RFC 0014 §10).

Both profiles publish the same snapshot-bound operations, typed per
profile: `hcl.native@1` publishes all six; `hcl.tfvars@1` publishes the
four attribute operations only. Values are supplied as typed native facts
or validated literal-complete values, never as raw markup and never as
unevaluated expression text (RFC 0014 §10, §14).

Edits operate on the source like RFC 0012: they replace text only within
operation-owned spans, keep every untouched byte, reparse the target after
every operation of the sequential transaction, and verify the promised HCL
semantics. Conflict validation covers wrong profile/role/snapshot, missing
or duplicate target, stale anchors, overlapping source ownership,
duplicate-attribute creation, `hcl.tfvars@1` block insertion,
unrepresentable values, limit failure, and reparse failure. Success returns
the new Document, ChangeSet, UntouchedByteProof, and a replayable
SourcePatch; failure returns none. Dry-run and commit have identical
replacement sets and target digest. No operation writes a filesystem path,
and none evaluates anything (hard gate 1).

Authority (language-neutral first; Rust only for byte/registry
arbitration): crates/consema-hcl/src/edit.rs �� the address model
edit.rs:95-226, the value model edit.rs:228-325, the operations
edit.rs:327-532, failure algebra edit.rs:547-612, the sequential commit
edit.rs:614-682, byte-level layout edit.rs:886-1248, operation preparation
edit.rs:1254-1416, value checks and rendering edit.rs:1418-1610,
post-application verification edit.rs:1612-1777, commit assembly
edit.rs:1779-1953.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.document.change_set import (
    ChangeSet,
    NodeMapping,
    NodeMappingStatus,
    SourceEdit,
)
from consema.document.edit_plan import (
    EditOperationSummary,
    EditPlan,
    EditPlanSourceId,
)
from consema.document.ids import FormatOperationId
from consema.document.source import SourceLimits
from consema.document.source_patch import SourcePatch, SourcePatchLimits
from consema.document.structural import (
    FormationStatus,
    NodeRef,
    Span,
)
from consema.document.untouched_proof import UntouchedByteProof
from consema.hcl.document import HclDocument, parse as parse_document
from consema.hcl.errors import HclEditFailure, HclEditFailureKind
from consema.hcl.expression import (
    HclExpression,
    HclLiteralValue,
    canonical_decimal,
    is_literal_complete,
    literal_value,
)
from consema.hcl.kinds import (
    HclProfile,
    HclSyntaxKind,
    is_identifier_continue,
    is_identifier_start,
)
from consema.hcl.limits import HclParseLimits
from consema.hcl.native import HclAttribute, HclBlock, HclBody, HclBodyItem


# ---------------------------------------------------------------------------
# Address model (edit.rs:95-226)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BodyPathStep:
    """A step selects one block occurrence by exact type, exact label
    sequence, and 0-based source position among the blocks with the same
    type and labels (edit.rs:95-138)."""

    block_type: str
    labels: tuple[str, ...] = ()
    occurrence: int = 0


@dataclass(frozen=True, slots=True)
class BodyPath:
    """A root-relative path to one body (RFC 0014 §10; edit.rs:140-174).

    The empty path denotes the root body. A step that meets an attribute
    instead of a block is a role failure; a step that does not exist in
    the current document state is a missing-target failure.
    """

    steps: tuple[BodyPathStep, ...] = ()

    @classmethod
    def root(cls) -> BodyPath:
        return cls()

    @classmethod
    def of_steps(cls, steps: list[BodyPathStep]) -> BodyPath:
        return cls(tuple(steps))

    def child(self, step: BodyPathStep) -> BodyPath:
        return BodyPath(self.steps + (step,))


@dataclass(frozen=True, slots=True)
class NodeRef:
    """One exact body item address (RFC 0014 §10; edit.rs:176-212).

    An attribute is addressed by owning body and name — unique per body in
    a Complete document. A block is addressed by owning body, type, exact
    label sequence, and occurrence, because blocks with the same type and
    labels may repeat.
    """

    kind: str  # "attribute" | "block"
    body: BodyPath
    name: str = ""
    block_type: str = ""
    labels: tuple[str, ...] = ()
    occurrence: int = 0

    def body_path(self) -> BodyPath:
        return self.body

    @classmethod
    def attribute(cls, body: BodyPath, name: str) -> NodeRef:
        return cls(kind="attribute", body=body, name=name)

    @classmethod
    def block(cls, body: BodyPath, block_type: str, labels: tuple[str, ...], occurrence: int = 0) -> NodeRef:
        return cls(
            kind="block",
            body=body,
            block_type=block_type,
            labels=labels,
            occurrence=occurrence,
        )


class BodyPlacement:
    """Attribute insertion placement inside one body (RFC 0014 §10;
    edit.rs:214-226)."""

    __slots__ = ("kind", "anchor")

    def __init__(self, kind: str, anchor: NodeRef | None = None) -> None:
        self.kind = kind  # "First" | "Last" | "After"
        self.anchor = anchor

    @classmethod
    def first(cls) -> BodyPlacement:
        return cls("First")

    @classmethod
    def last(cls) -> BodyPlacement:
        return cls("Last")

    @classmethod
    def after(cls, node: NodeRef) -> BodyPlacement:
        return cls("After", node)


# ---------------------------------------------------------------------------
# Typed edit values (edit.rs:228-325)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EditKey:
    """One object-constructor literal key (RFC 0014 §4.6, §8.1; edit.rs:
    310-325).

    ``kind`` is "identifier" | "number" | "string". An identifier key
    spelled `for` is refused, because the for-expression interpretation
    has priority in an object constructor.
    """

    kind: str
    payload: object

    @classmethod
    def identifier(cls, name: str) -> EditKey:
        return cls("identifier", name)

    @classmethod
    def number(cls, value: int) -> EditKey:
        return cls("number", value)

    @classmethod
    def string(cls, text: str) -> EditKey:
        return cls("string", text)


@dataclass(frozen=True, slots=True)
class EditValue:
    """One typed literal-complete HCL value supplied to an edit (RFC 0014
    §10; edit.rs:228-308).

    ``kind`` is "integer" | "real" | "string" | "boolean" | "null" |
    "tuple" | "object" | "expression". Values are typed native facts,
    never raw markup and never unevaluated expression text. The
    "expression" variant exists so that derived-expression insertion is
    refused explicitly with `hcl.edit.unrepresentable@1`; no commit ever
    renders it.
    """

    kind: str
    payload: object

    @classmethod
    def integer(cls, value: int) -> EditValue:
        return cls("integer", value)

    @classmethod
    def real(cls, value: float) -> EditValue:
        return cls("real", value)

    @classmethod
    def string(cls, text: str) -> EditValue:
        return cls("string", text)

    @classmethod
    def boolean(cls, value: bool) -> EditValue:
        return cls("boolean", value)

    @classmethod
    def null(cls) -> EditValue:
        return cls("null", None)

    @classmethod
    def tuple(cls, elements: tuple[EditValue, ...]) -> EditValue:
        return cls("tuple", elements)

    @classmethod
    def object(cls, entries: tuple[tuple[EditKey, EditValue], ...]) -> EditValue:
        return cls("object", entries)

    @classmethod
    def expression(cls, kind: str, text: str) -> EditValue:
        return cls("expression", (kind, text))

    def kind_name(self) -> str:
        return self.kind


# ---------------------------------------------------------------------------
# Operations and transactions (edit.rs:327-532)
# ---------------------------------------------------------------------------


class EditOperationKind(enum.Enum):
    """Typed edit operation kinds (edit.rs:327-397)."""

    SET_ATTRIBUTE_VALUE = "SetAttributeValue"
    INSERT_ATTRIBUTE = "InsertAttribute"
    REMOVE_ATTRIBUTE = "RemoveAttribute"
    RENAME_ATTRIBUTE = "RenameAttribute"
    INSERT_BLOCK = "InsertBlock"
    REMOVE_BLOCK = "RemoveBlock"


@dataclass(frozen=True, slots=True)
class EditOperation:
    """One snapshot-bound HCL structural operation (RFC 0014 §10;
    edit.rs:327-397).

    Every body path, name, and occurrence refers to the document state as
    of the operation's own application: operations of one transaction
    apply sequentially, so a later operation may target content an earlier
    insertion created.
    """

    kind: EditOperationKind
    body: BodyPath
    attribute: str = ""
    value: EditValue | None = None
    name: str = ""
    placement: BodyPlacement | None = None
    block_type: str = ""
    labels: tuple[str, ...] = ()
    attributes: tuple[tuple[str, EditValue], ...] = ()
    occurrence: int = 0


@dataclass(frozen=True, slots=True)
class EditTransaction:
    """Immutable snapshot-bound transaction (edit.rs:399-418)."""

    base: object
    operations: tuple[EditOperation, ...] = ()


class EditTransactionBuilder:
    """Builds one transaction against one immutable snapshot
    (edit.rs:420-532)."""

    def __init__(self, document: HclDocument) -> None:
        self._base = document.snapshot_identity()
        self._operations: list[EditOperation] = []

    def set_attribute_value(
        self, body: BodyPath, attribute: str, value: EditValue
    ) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(
                kind=EditOperationKind.SET_ATTRIBUTE_VALUE,
                body=body,
                attribute=attribute,
                value=value,
            )
        )
        return self

    def insert_attribute(
        self, body: BodyPath, name: str, value: EditValue, placement: BodyPlacement
    ) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(
                kind=EditOperationKind.INSERT_ATTRIBUTE,
                body=body,
                name=name,
                value=value,
                placement=placement,
            )
        )
        return self

    def remove_attribute(self, body: BodyPath, attribute: str) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(
                kind=EditOperationKind.REMOVE_ATTRIBUTE,
                body=body,
                attribute=attribute,
            )
        )
        return self

    def rename_attribute(
        self, body: BodyPath, attribute: str, name: str
    ) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(
                kind=EditOperationKind.RENAME_ATTRIBUTE,
                body=body,
                attribute=attribute,
                name=name,
            )
        )
        return self

    def insert_block(
        self,
        body: BodyPath,
        block_type: str,
        labels: list[str],
        attributes: list[tuple[str, EditValue]],
        placement: BodyPlacement,
    ) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(
                kind=EditOperationKind.INSERT_BLOCK,
                body=body,
                block_type=block_type,
                labels=tuple(labels),
                attributes=tuple(attributes),
                placement=placement,
            )
        )
        return self

    def remove_block(
        self,
        body: BodyPath,
        block_type: str,
        labels: list[str],
        occurrence: int = 0,
    ) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(
                kind=EditOperationKind.REMOVE_BLOCK,
                body=body,
                block_type=block_type,
                labels=tuple(labels),
                occurrence=occurrence,
            )
        )
        return self

    def build(self) -> EditTransaction:
        return EditTransaction(base=self._base, operations=tuple(self._operations))


@dataclass(frozen=True, slots=True)
class EditCommit:
    """One complete committed edit (edit.rs:534-545)."""

    document: HclDocument
    change_set: ChangeSet
    source_patch: SourcePatch
    untouched_proof: UntouchedByteProof


# ---------------------------------------------------------------------------
# Piece index and byte-level layout (edit.rs:886-1248)
# ---------------------------------------------------------------------------


class _PieceIndex:
    """Lossless piece facts of one formed document, indexed for boundary
    walks (edit.rs:891-935)."""

    def __init__(self, document: HclDocument) -> None:
        pieces = document.lossless_structural_index().pieces
        kinds = document.lossless_syntax_kinds()
        self.starts = [piece.span.start_byte for piece in pieces]
        self.ends = [piece.span.end_byte for piece in pieces]
        self.kinds = kinds

    def piece_starting_at(self, pos: int) -> int | None:
        for index, start in enumerate(self.starts):
            if start >= pos:
                if start == pos:
                    return index
                return None
        return None

    def piece_ending_at(self, pos: int) -> int | None:
        if pos == 0:
            return None
        for index in range(len(self.starts) - 1, -1, -1):
            if self.starts[index] < pos:
                if self.ends[index] == pos:
                    return index
                return None
        return None


def _resolve_body(
    document: HclDocument, path: BodyPath
) -> tuple[HclBody, HclBlock | None]:
    """Resolves one body path against one native document; the empty path
    is the root body (edit.rs:940-960)."""
    body = document.body
    parent = None
    for step in path.steps:
        block = _find_block(body, step.block_type, step.labels, step.occurrence)
        if block is None:
            for item in body.items:
                attribute = item.as_attribute()
                if attribute is not None and attribute.name == step.block_type:
                    raise HclEditFailure(HclEditFailureKind.WRONG_ROLE)
            raise HclEditFailure(HclEditFailureKind.INCOMPLETE_TARGET)
        parent = block
        body = block.body
    return body, parent


def _find_attribute(body: HclBody, name: str) -> HclAttribute | None:
    for item in body.items:
        attribute = item.as_attribute()
        if attribute is not None and attribute.name == name:
            return attribute
    return None


def _find_block(
    body: HclBody, block_type: str, labels: tuple[str, ...], occurrence: int
) -> HclBlock | None:
    seen = 0
    for item in body.items:
        block = item.as_block()
        if block is not None and block.block_type == block_type:
            block_labels = tuple(label.text for label in block.labels)
            if block_labels == labels:
                if seen == occurrence:
                    return block
                seen += 1
    return None


def _block_position(
    body: HclBody, block_type: str, labels: tuple[str, ...], occurrence: int
) -> int | None:
    seen = 0
    for position, item in enumerate(body.items):
        block = item.as_block()
        if block is not None and block.block_type == block_type:
            block_labels = tuple(label.text for label in block.labels)
            if block_labels == labels:
                if seen == occurrence:
                    return position
                seen += 1
    return None


def _resolve_node(document: HclDocument, node: NodeRef) -> HclBodyItem:
    target_body, _ = _resolve_body(document, node.body)
    if node.kind == "attribute":
        for item in target_body.items:
            attribute = item.as_attribute()
            if attribute is not None and attribute.name == node.name:
                return item
        raise HclEditFailure(HclEditFailureKind.INCOMPLETE_TARGET)
    position = _block_position(target_body, node.block_type, node.labels, node.occurrence)
    if position is None:
        raise HclEditFailure(HclEditFailureKind.INCOMPLETE_TARGET)
    return target_body.items[position]


def _item_span_start(item: HclBodyItem) -> int:
    attribute = item.as_attribute()
    if attribute is not None:
        return attribute.name_span.start_byte
    return item.as_block().span.start_byte


def _item_span_end(item: HclBodyItem) -> int:
    attribute = item.as_attribute()
    if attribute is not None:
        return attribute.expression.span.end_byte
    return item.as_block().span.end_byte


def _item_line_end(index: _PieceIndex, from_: int) -> int:
    """End of the line that terminates the item ending at `from` (edit.rs:
    1071-1098)."""
    pos = from_
    while True:
        piece = index.piece_starting_at(pos)
        if piece is None:
            return pos
        kind = index.kinds[piece]
        if kind in (
            HclSyntaxKind.WHITESPACE,
            HclSyntaxKind.LINE_COMMENT,
            HclSyntaxKind.INLINE_COMMENT,
        ):
            pos = index.ends[piece]
        elif kind is HclSyntaxKind.LINE_BREAK:
            return index.ends[piece]
        else:
            raise HclEditFailure(HclEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)


def _item_line_start(index: _PieceIndex, item_start: int) -> int:
    """Start of the line that begins at `item_start` (edit.rs:1100-1112)."""
    pos = item_start
    while True:
        piece = index.piece_ending_at(pos)
        if piece is None or index.kinds[piece] is not HclSyntaxKind.WHITESPACE:
            return pos
        pos = index.starts[piece]


def _item_indent(index: _PieceIndex, document: HclDocument, item_start: int) -> str:
    """Leading whitespace run of the line that starts an item (edit.rs:
    1114-1138)."""
    source = document.source.bytes()
    pos = item_start
    chunks: list[bytes] = []
    while True:
        piece = index.piece_ending_at(pos)
        if piece is None or index.kinds[piece] is not HclSyntaxKind.WHITESPACE:
            break
        chunks.append(source[index.starts[piece] : index.ends[piece]])
        pos = index.starts[piece]
    chunks.reverse()
    return b"".join(chunks).decode("utf-8")


def _block_brace_positions(index: _PieceIndex, block_span: tuple[int, int]) -> tuple[int, int]:
    """Byte positions of one block's own braces (edit.rs:1140-1171)."""
    open_end = None
    close_start = None
    for position, start in enumerate(index.starts):
        if start >= block_span[1]:
            break
        if start < block_span[0]:
            continue
        kind = index.kinds[position]
        if kind is HclSyntaxKind.BRACE_OPEN and open_end is None:
            open_end = index.ends[position]
        elif kind is HclSyntaxKind.BRACE_CLOSE:
            close_start = start
    if open_end is None or close_start is None:
        raise HclEditFailure(HclEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
    return open_end, close_start


def _empty_body_point(
    index: _PieceIndex,
    document: HclDocument,
    body_path: BodyPath,
    parent: HclBlock | None,
) -> tuple[int, str]:
    """Insertion point facts of an empty target body (edit.rs:1173-1190)."""
    if parent is None:
        return len(document.source.bytes()), ""
    _, close_start = _block_brace_positions(
        index, (parent.span.start_byte, parent.span.end_byte)
    )
    return close_start, "  " * len(body_path.steps)


def _insertion_point(
    index: _PieceIndex,
    document: HclDocument,
    body_path: BodyPath,
    body: HclBody,
    parent: HclBlock | None,
    placement: BodyPlacement,
) -> tuple[int, str, bool]:
    """Computes the insertion point, markup indentation, and whether the
    markup needs a separating leading newline (edit.rs:1192-1248)."""
    items = body.items
    if placement.kind == "First":
        if items:
            start = _item_span_start(items[0])
            return _item_line_start(index, start), _item_indent(index, document, start), False
        point, indent = _empty_body_point(index, document, body_path, parent)
        return point, indent, False
    if placement.kind == "Last":
        if items:
            end = _item_span_end(items[-1])
            line_end = _item_line_end(index, end)
            return (
                line_end,
                _item_indent(index, document, _item_span_start(items[-1])),
                line_end == end,
            )
        point, indent = _empty_body_point(index, document, body_path, parent)
        return point, indent, False
    anchor_ref = placement.anchor
    if anchor_ref is None or anchor_ref.body_path() != body_path:
        raise HclEditFailure(HclEditFailureKind.INCOMPLETE_TARGET)
    anchor = _resolve_node(document, anchor_ref)
    end = _item_span_end(anchor)
    line_end = _item_line_end(index, end)
    return (
        line_end,
        _item_indent(index, document, _item_span_start(anchor)),
        line_end == end,
    )


# ---------------------------------------------------------------------------
# Value checks and rendering (edit.rs:1418-1610)
# ---------------------------------------------------------------------------


def _is_valid_identifier(name: str) -> bool:
    """Whether one spelling is a valid UAX #31 identifier without a leading
    underscore, matching the frozen lexer rule (RFC 0014 §4.1, §12 D-4;
    edit.rs:1450-1460)."""
    if not name:
        return False
    first = name[0]
    if first == "_" or not is_identifier_start(first):
        return False
    for character in name[1:]:
        if not (is_identifier_continue(character) or character == "-"):
            return False
    return True


def _check_value(value: EditValue) -> None:
    """Rejects one typed value that cannot be expressed as literal-complete
    HCL (RFC 0014 §8.1, §10, §14; edit.rs:1418-1439)."""
    if value.kind == "real" and not _is_finite(value.payload):
        raise HclEditFailure(HclEditFailureKind.UNREPRESENTABLE_VALUE, detail="real")
    if value.kind in ("integer", "boolean", "null", "string", "real"):
        return
    if value.kind == "tuple":
        for element in value.payload:
            _check_value(element)
        return
    if value.kind == "object":
        for key, entry_value in value.payload:
            _check_key(key)
            _check_value(entry_value)
        return
    raise HclEditFailure(HclEditFailureKind.UNREPRESENTABLE_VALUE, detail="expression")


def _is_finite(real: float) -> bool:
    return real == real and real not in (float("inf"), float("-inf"))


def _check_key(key: EditKey) -> None:
    if key.kind == "identifier":
        name = key.payload
        if _is_valid_identifier(name) and name != "for":
            return
        raise HclEditFailure(HclEditFailureKind.UNREPRESENTABLE_VALUE, detail="object-key")
    if key.kind in ("number", "string"):
        return
    raise HclEditFailure(HclEditFailureKind.UNREPRESENTABLE_VALUE, detail="object-key")


def _quote_escape(text: str) -> str:
    """Minimal deterministic quoted-template spelling of one string (RFC
    0014 §9; edit.rs:1462-1492)."""
    out = ['"']
    index = 0
    while index < len(text):
        character = text[index]
        if character == '"':
            out.append('\\"')
        elif character == "\\":
            out.append("\\\\")
        elif character == "\n":
            out.append("\\n")
        elif character == "\r":
            out.append("\\r")
        elif character == "\t":
            out.append("\\t")
        elif character == "$" and index + 1 < len(text) and text[index + 1] == "{":
            out.append("$${")
            index += 1
        elif character == "%" and index + 1 < len(text) and text[index + 1] == "{":
            out.append("%%{")
            index += 1
        elif ord(character) < 0x20 or ord(character) == 0x7F:
            out.append(f"\\u{ord(character):04X}")
        else:
            out.append(character)
        index += 1
    out.append('"')
    return "".join(out)


def _canonical_real(value: float) -> str | None:
    """Canonical decimal spelling of one finite real, by pure decimal
    string arithmetic over its shortest-round-trip spelling (hard gate 1;
    edit.rs:1494-1512)."""
    if not _is_finite(value):
        return None
    text = repr(value)
    if text.startswith("-"):
        canonical = canonical_decimal(text[1:])
        if canonical is None:
            return None
        return canonical if canonical == "0" else "-" + canonical
    return canonical_decimal(text)


def _render_value(value: EditValue, indent: str) -> str:
    """Canonical expression text of one typed literal value at one base
    indentation (RFC 0014 §9; edit.rs:1514-1570)."""
    if value.kind == "integer":
        return str(value.payload)
    if value.kind == "real":
        canonical = _canonical_real(value.payload)
        if canonical is None:
            raise HclEditFailure(HclEditFailureKind.UNREPRESENTABLE_VALUE, detail="real")
        return canonical
    if value.kind == "string":
        return _quote_escape(value.payload)
    if value.kind == "boolean":
        return "true" if value.payload else "false"
    if value.kind == "null":
        return "null"
    if value.kind == "tuple":
        elements = value.payload
        if not elements:
            return "[]"
        inner = indent + "  "
        out = ["[\n"]
        for position, element in enumerate(elements):
            if position > 0:
                out.append(",\n")
            out.append(inner)
            out.append(_render_value(element, inner))
        out.append("\n")
        out.append(indent)
        out.append("]")
        return "".join(out)
    if value.kind == "object":
        entries = value.payload
        if not entries:
            return "{}"
        inner = indent + "  "
        out = ["{\n"]
        for position, (key, entry_value) in enumerate(entries):
            if position > 0:
                out.append(",\n")
            out.append(inner)
            out.append(_render_key(key))
            out.append(" = ")
            out.append(_render_value(entry_value, inner))
        out.append("\n")
        out.append(indent)
        out.append("}")
        return "".join(out)
    raise HclEditFailure(HclEditFailureKind.UNREPRESENTABLE_VALUE, detail="expression")


def _render_key(key: EditKey) -> str:
    """Bare spelling of one object key; validity is pre-checked by
    _check_key (edit.rs:1572-1580)."""
    if key.kind == "identifier":
        return key.payload
    if key.kind == "number":
        return str(key.payload)
    return _quote_escape(key.payload)


def _block_markup(
    indent: str,
    block_type: str,
    labels: tuple[str, ...],
    attributes: tuple[tuple[str, EditValue], ...],
) -> str:
    """Canonical block text at one base indentation: `type "label" {`
    header, two-space-indented nested attributes, closing brace, and a
    trailing newline; labels always render quoted (RFC 0014 §9; edit.rs:
    1582-1610)."""
    out = [indent, block_type]
    for label in labels:
        out.append(" ")
        out.append(_quote_escape(label))
    out.append(" {\n")
    inner = indent + "  "
    for name, value in attributes:
        out.append(inner)
        out.append(name)
        out.append(" = ")
        out.append(_render_value(value, inner))
        out.append("\n")
    out.append(indent)
    out.append("}\n")
    return "".join(out)


# ---------------------------------------------------------------------------
# Operation preparation and verification (edit.rs:1254-1777)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _AppliedEdit:
    """One applied raw-byte splice, recorded for base-coordinate
    translation (edit.rs:694-707)."""

    pre_start: int
    pre_len: int
    replacement: bytes
    structural: bool = False


class _VerifyData:
    """Per-operation data the post-application verification needs
    (edit.rs:686-691)."""

    __slots__ = ("rename_kind",)

    def __init__(self, rename_kind=None) -> None:
        self.rename_kind = rename_kind


def _unmap_in(edits: list[_AppliedEdit], pos: int) -> int:
    """Maps one position from the final state back to the base snapshot
    through the applied edits in reverse application order (edit.rs:712-
    729)."""
    for index in range(len(edits) - 1, -1, -1):
        edit = edits[index]
        if pos <= edit.pre_start:
            continue
        if pos < edit.pre_start + len(edit.replacement):
            base_start = _unmap_in(edits[:index], edit.pre_start)
            return base_start + (pos - edit.pre_start)
        pos = pos - len(edit.replacement) + edit.pre_len
    return pos


def _map_in(edits: list[_AppliedEdit], pos: int) -> int:
    """Maps one position from one pre-state to the final state through the
    applied edits in application order (edit.rs:731-744)."""
    for edit in edits:
        if pos <= edit.pre_start:
            continue
        if pos < edit.pre_start + edit.pre_len:
            raise HclEditFailure(HclEditFailureKind.OVERLAPPING_OWNERSHIP)
        pos = pos + len(edit.replacement) - edit.pre_len
    return pos


def _record_edit(
    edits: list[_AppliedEdit],
    pre_start: int,
    pre_len: int,
    replacement: bytes,
) -> None:
    """Records one splice and rejects two insertions that map to the same
    base position; an operation whose span lies inside an earlier
    replacement folds into that replacement (edit.rs:746-812)."""
    if pre_len == 0 and not replacement:
        return
    for index in range(len(edits) - 1, -1, -1):
        if edits[index].structural:
            continue
        region_start = _map_in(edits[index + 1 :], edits[index].pre_start)
        region_end = region_start + len(edits[index].replacement)
        if (
            pre_start >= region_start
            and pre_start + pre_len <= region_end
            and not (pre_len == 0 and pre_start == region_end)
        ):
            offset = pre_start - region_start
            merged = (
                edits[index].replacement[:offset]
                + replacement
                + edits[index].replacement[offset + pre_len :]
            )
            delta = len(merged) - len(edits[index].replacement)
            target_start = edits[index].pre_start
            for later in edits[index + 1 :]:
                if later.pre_start > target_start:
                    later.pre_start = _shifted(later.pre_start, delta)
            edits[index] = _AppliedEdit(
                pre_start=edits[index].pre_start,
                pre_len=edits[index].pre_len,
                replacement=merged,
            )
            return
    base_start = _unmap_in(edits, pre_start)
    base_end = _unmap_in(edits, pre_start + pre_len)
    for index, previous in enumerate(edits):
        if previous.pre_len == 0 and base_start == base_end:
            previous_base = _unmap_in(edits[:index], previous.pre_start)
            if previous_base == base_start:
                raise HclEditFailure(HclEditFailureKind.CONFLICTING_EDITS)
    edits.append(_AppliedEdit(pre_start=pre_start, pre_len=pre_len, replacement=replacement))


def _shifted(base: int, delta: int) -> int:
    shifted_value = base + delta
    if shifted_value < 0:
        raise HclEditFailure(HclEditFailureKind.RESOURCE_LIMIT, resource_name="target-bytes")
    return shifted_value


def _apply_step(
    edits: list[_AppliedEdit],
    bytes_: bytearray,
    limits: HclParseLimits,
    splices: list[_AppliedEdit],
) -> None:
    """Applies one step's splices: validates the target length against the
    source bound first, records every splice against the base coordinates,
    then builds the new bytes in one pass (edit.rs:814-843)."""
    target_len = len(bytes_)
    for splice in splices:
        target_len = target_len - splice.pre_len + len(splice.replacement)
        if target_len < 0:
            raise HclEditFailure(HclEditFailureKind.RESOURCE_LIMIT, resource_name="target-bytes")
    if target_len > limits.common.max_source_bytes:
        raise HclEditFailure(HclEditFailureKind.RESOURCE_LIMIT, resource_name="target-bytes")
    for splice in splices:
        _record_edit(edits, splice.pre_start, splice.pre_len, splice.replacement)
    _apply_splices(bytes_, splices)


def _apply_splices(bytes_: bytearray, splices: list[_AppliedEdit]) -> None:
    """Builds the new bytes by applying the splices sequentially against a
    working buffer (edit.rs:845-861)."""
    for splice in splices:
        end = splice.pre_start + splice.pre_len
        if end > len(bytes_):
            raise HclEditFailure(HclEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        bytes_[splice.pre_start:end] = splice.replacement


def _splice(pre_start: int, pre_len: int, replacement: bytes) -> _AppliedEdit:
    return _AppliedEdit(pre_start=pre_start, pre_len=pre_len, replacement=replacement)


def _prepare_operation(
    current: HclDocument, operation: EditOperation
) -> tuple[list[_AppliedEdit], _VerifyData]:
    """Resolves one operation against the current state and computes its
    splices in the current state's coordinates (edit.rs:1257-1416)."""
    index = _PieceIndex(current)
    kind = operation.kind
    if kind is EditOperationKind.SET_ATTRIBUTE_VALUE:
        _check_value(operation.value)
        target_body, _ = _resolve_body(current, operation.body)
        attribute_ref = _find_attribute(target_body, operation.attribute)
        if attribute_ref is None:
            raise HclEditFailure(HclEditFailureKind.INCOMPLETE_TARGET)
        indent = _item_indent(index, current, attribute_ref.name_span.start_byte)
        rendered = _render_value(operation.value, indent)
        start = attribute_ref.expression.span.start_byte
        end = attribute_ref.expression.span.end_byte
        return [_splice(start, end - start, rendered.encode("utf-8"))], _VerifyData()
    if kind is EditOperationKind.INSERT_ATTRIBUTE:
        target_body, parent = _resolve_body(current, operation.body)
        if not _is_valid_identifier(operation.name):
            raise HclEditFailure(HclEditFailureKind.UNREPRESENTABLE_VALUE, detail="identifier")
        if _find_attribute(target_body, operation.name) is not None:
            raise HclEditFailure(HclEditFailureKind.DUPLICATE_ATTRIBUTE)
        _check_value(operation.value)
        point, indent, leading_newline = _insertion_point(
            index, current, operation.body, target_body, parent, operation.placement
        )
        markup = ("\n" if leading_newline else "") + indent + operation.name + " = "
        markup += _render_value(operation.value, indent) + "\n"
        return [_splice(point, 0, markup.encode("utf-8"))], _VerifyData()
    if kind is EditOperationKind.REMOVE_ATTRIBUTE:
        target_body, _ = _resolve_body(current, operation.body)
        attribute_ref = _find_attribute(target_body, operation.attribute)
        if attribute_ref is None:
            raise HclEditFailure(HclEditFailureKind.INCOMPLETE_TARGET)
        start = _item_line_start(index, attribute_ref.name_span.start_byte)
        end = _item_line_end(index, attribute_ref.expression.span.end_byte)
        return [_splice(start, end - start, b"")], _VerifyData()
    if kind is EditOperationKind.RENAME_ATTRIBUTE:
        target_body, _ = _resolve_body(current, operation.body)
        attribute_ref = _find_attribute(target_body, operation.attribute)
        if attribute_ref is None:
            raise HclEditFailure(HclEditFailureKind.INCOMPLETE_TARGET)
        if not _is_valid_identifier(operation.name):
            raise HclEditFailure(HclEditFailureKind.UNREPRESENTABLE_VALUE, detail="identifier")
        rename_kind = attribute_ref.expression.kind
        if operation.attribute == operation.name:
            return [], _VerifyData(rename_kind=rename_kind)
        if _find_attribute(target_body, operation.name) is not None:
            raise HclEditFailure(HclEditFailureKind.DUPLICATE_ATTRIBUTE)
        start = attribute_ref.name_span.start_byte
        end = attribute_ref.name_span.end_byte
        return [_splice(start, end - start, operation.name.encode("utf-8"))], _VerifyData(
            rename_kind=rename_kind
        )
    if kind is EditOperationKind.INSERT_BLOCK:
        if current.profile is HclProfile.TFVARS_V1:
            raise HclEditFailure(HclEditFailureKind.BLOCK_IN_TFVARS)
        if not _is_valid_identifier(operation.block_type):
            raise HclEditFailure(HclEditFailureKind.UNREPRESENTABLE_VALUE, detail="identifier")
        seen = set()
        for name, value in operation.attributes:
            if not _is_valid_identifier(name):
                raise HclEditFailure(HclEditFailureKind.UNREPRESENTABLE_VALUE, detail="identifier")
            if name in seen:
                raise HclEditFailure(HclEditFailureKind.DUPLICATE_ATTRIBUTE)
            seen.add(name)
            _check_value(value)
        target_body, parent = _resolve_body(current, operation.body)
        point, indent, leading_newline = _insertion_point(
            index, current, operation.body, target_body, parent, operation.placement
        )
        markup = ("\n" if leading_newline else "") + _block_markup(
            indent, operation.block_type, operation.labels, operation.attributes
        )
        return [_splice(point, 0, markup.encode("utf-8"))], _VerifyData()
    # REMOVE_BLOCK
    target_body, _ = _resolve_body(current, operation.body)
    block_ref = _find_block_in_body(
        target_body, operation.block_type, operation.labels, operation.occurrence
    )
    if block_ref is None:
        for item in target_body.items:
            attribute = item.as_attribute()
            if attribute is not None and attribute.name == operation.block_type:
                raise HclEditFailure(HclEditFailureKind.WRONG_ROLE)
        raise HclEditFailure(HclEditFailureKind.INCOMPLETE_TARGET)
    start = _item_line_start(index, block_ref.span.start_byte)
    end = _item_line_end(index, block_ref.span.end_byte)
    return [_splice(start, end - start, b"")], _VerifyData()


def _find_block_in_body(
    body: HclBody, block_type: str, labels: tuple[str, ...], occurrence: int
) -> HclBlock | None:
    seen = 0
    for item in body.items:
        block = item.as_block()
        if block is not None and block.block_type == block_type:
            block_labels = tuple(label.text for label in block.labels)
            if block_labels == labels:
                if seen == occurrence:
                    return block
                seen += 1
    return None


def _verify_operation(
    formed: HclDocument, operation: EditOperation, data: _VerifyData
) -> None:
    """Verifies the promised HCL semantics of one operation against the
    reparse of the state immediately after its application (edit.rs:1617-
    1777)."""
    kind = operation.kind
    if kind in (EditOperationKind.SET_ATTRIBUTE_VALUE, EditOperationKind.INSERT_ATTRIBUTE):
        target_body, _ = _resolve_body(formed, operation.body)
        name = operation.attribute if kind is EditOperationKind.SET_ATTRIBUTE_VALUE else operation.name
        attribute_ref = _find_attribute(target_body, name)
        if attribute_ref is None:
            raise HclEditFailure(HclEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        try:
            literal = literal_value(attribute_ref.expression)
        except Exception:
            raise HclEditFailure(HclEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED) from None
        if not _edit_value_matches_literal(operation.value, literal):
            raise HclEditFailure(HclEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        return
    if kind is EditOperationKind.REMOVE_ATTRIBUTE:
        target_body, _ = _resolve_body(formed, operation.body)
        if _find_attribute(target_body, operation.attribute) is not None:
            raise HclEditFailure(HclEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        return
    if kind is EditOperationKind.RENAME_ATTRIBUTE:
        target_body, _ = _resolve_body(formed, operation.body)
        attribute_ref = _find_attribute(target_body, operation.name)
        if attribute_ref is None:
            raise HclEditFailure(HclEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        if data.rename_kind is None or attribute_ref.expression.kind != data.rename_kind:
            raise HclEditFailure(HclEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        return
    if kind is EditOperationKind.INSERT_BLOCK:
        target_body, _ = _resolve_body(formed, operation.body)
        for item in target_body.items:
            block = item.as_block()
            if (
                block is not None
                and block.block_type == operation.block_type
                and tuple(label.text for label in block.labels) == operation.labels
                and _block_body_matches(block, operation.attributes)
            ):
                return
        raise HclEditFailure(HclEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
    target_body, _ = _resolve_body(formed, operation.body)
    if _find_block_in_body(target_body, operation.block_type, operation.labels, operation.occurrence) is not None:
        raise HclEditFailure(HclEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)


def _block_body_matches(
    block: HclBlock, attributes: tuple[tuple[str, EditValue], ...]
) -> bool:
    items = block.body.items
    if sum(1 for item in items if item.as_attribute() is not None) != len(attributes):
        return False
    for name, value in attributes:
        attribute_ref = _find_attribute(block.body, name)
        if attribute_ref is None:
            return False
        try:
            literal = literal_value(attribute_ref.expression)
        except Exception:
            raise HclEditFailure(HclEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED) from None
        if not _edit_value_matches_literal(value, literal):
            return False
    return True


def _edit_value_matches_literal(value: EditValue, literal: HclLiteralValue) -> bool:
    """Whether one typed edit value equals one reparsed literal; numbers
    compare by canonical-decimal value equality across the integer/real
    kind boundary (RFC 0014 §6; edit.rs:1731-1777)."""
    if value.kind == "integer" and literal.kind == "integer":
        return str(value.payload) == literal.text
    if value.kind == "real" and literal.kind in ("integer", "real"):
        return _canonical_real(value.payload) == literal.text
    if value.kind == "string" and literal.kind == "string":
        return value.payload == literal.text
    if value.kind == "boolean" and literal.kind == "boolean":
        return value.payload == literal.flag
    if value.kind == "null" and literal.kind == "null":
        return True
    if value.kind == "tuple" and literal.kind == "tuple":
        return len(value.payload) == len(literal.elements) and all(
            _edit_value_matches_literal(element, decoded)
            for element, decoded in zip(value.payload, literal.elements)
        )
    if value.kind == "object" and literal.kind == "object":
        return len(value.payload) == len(literal.entries) and all(
            _edit_key_matches_literal(key, entry.key)
            and _edit_value_matches_literal(entry_value, entry.value)
            for (key, entry_value), entry in zip(value.payload, literal.entries)
        )
    return False


def _edit_key_matches_literal(key: EditKey, literal) -> bool:
    if key.kind == "identifier" and literal.kind == "identifier":
        return key.payload == literal.text
    if key.kind == "number" and literal.kind == "number":
        return str(key.payload) == literal.text
    if key.kind == "string" and literal.kind == "string":
        return key.payload == literal.text
    return False


# ---------------------------------------------------------------------------
# Commit and dry-run (edit.rs:614-682, 1779-1953)
# ---------------------------------------------------------------------------


def commit(document: HclDocument, transaction: EditTransaction) -> EditCommit:
    """Atomically commits structural operations; on failure the base
    document remains unchanged (edit.rs:614-682)."""
    if transaction.base != document.snapshot_identity():
        raise HclEditFailure(HclEditFailureKind.WRONG_SNAPSHOT)
    if document.formation_status() is not FormationStatus.COMPLETE:
        raise HclEditFailure(HclEditFailureKind.INCOMPLETE_TARGET)
    limits = document.parse_limits
    if len(transaction.operations) > limits.max_report_events:
        raise HclEditFailure(
            HclEditFailureKind.RESOURCE_LIMIT, resource_name="report-events"
        )
    profile = document.profile
    bytes_ = bytearray(document.render())
    edits: list[_AppliedEdit] = []
    current = document
    for operation in transaction.operations:
        splices, verify = _prepare_operation(current, operation)
        _apply_step(edits, bytes_, limits, splices)
        try:
            formed = parse_document(bytes(bytes_), profile, limits=limits)
        except Exception:
            raise HclEditFailure(
                HclEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED
            ) from None
        if formed.formation_status() is not FormationStatus.COMPLETE:
            raise HclEditFailure(HclEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        _verify_operation(formed, operation, verify)
        current = formed
    if not transaction.operations:
        try:
            formed = parse_document(bytes(bytes_), profile, limits=limits)
        except Exception:
            raise HclEditFailure(
                HclEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED
            ) from None
        if formed.formation_status() is not FormationStatus.COMPLETE:
            raise HclEditFailure(HclEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        current = formed
    return _build_commit(document, transaction, current, edits)


def dry_run(
    document: HclDocument,
    transaction: EditTransaction,
    source_id: EditPlanSourceId,
) -> EditPlan:
    """Fully validates and plans an edit without returning a new Document
    (edit.rs:623-637)."""
    commit_result = commit(document, transaction)
    try:
        return EditPlan.new(
            source_id,
            document.profile_id(),
            operation_summaries(transaction),
            commit_result.source_patch,
            list(commit_result.change_set.diagnostics),
        )
    except Exception:
        raise HclEditFailure(HclEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED) from None


def _build_commit(
    base: HclDocument,
    transaction: EditTransaction,
    final_document: HclDocument,
    edits: list[_AppliedEdit],
) -> EditCommit:
    """Builds the commit facts: ChangeSet, replayable SourcePatch, and the
    untouched-byte proof (edit.rs:1785-1882)."""
    limits = base.parse_limits
    if len(edits) > limits.max_report_events:
        raise HclEditFailure(HclEditFailureKind.RESOURCE_LIMIT, resource_name="report-events")
    spans: list[tuple[int, int, int]] = []
    for index, edit in enumerate(edits):
        old_start = _unmap_in(edits[:index], edit.pre_start)
        old_end = _unmap_in(edits[:index], edit.pre_start + edit.pre_len)
        spans.append((old_start, old_end, len(edit.replacement) - edit.pre_len))
    spans.sort(key=lambda span: (span[0], span[1]))
    runs: list[tuple[int, int, int]] = []
    for start, end, delta in spans:
        if runs:
            run_start, run_end, run_delta = runs[-1]
            if start <= run_end:
                runs[-1] = (run_start, max(run_end, end), run_delta + delta)
                continue
        runs.append((start, end, delta))
    before_delta = 0
    target_bytes = final_document.render()
    source_edits: list[SourceEdit] = []
    for start, end, run_delta in runs:
        target_start = _shifted(start, before_delta)
        run_len = (end - start) + run_delta
        if run_len < 0:
            raise HclEditFailure(HclEditFailureKind.RESOURCE_LIMIT, resource_name="target-bytes")
        target_end = target_start + run_len
        if target_end > len(target_bytes):
            raise HclEditFailure(HclEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        source_edits.append(
            SourceEdit(
                old_span=base.authority.span(start, end),
                new_span=final_document.authority.span(target_start, target_end),
                replacement=target_bytes[target_start:target_end],
            )
        )
        before_delta += run_delta
    change_set = ChangeSet(
        old_snapshot=base.snapshot_identity(),
        new_snapshot=final_document.snapshot_identity(),
        source_edits=tuple(source_edits),
        node_mappings=tuple(_build_mappings(base, transaction, final_document)),
        diagnostics=(),
    )
    patch_limits = _source_patch_limits(limits, len(source_edits))
    try:
        source_patch = SourcePatch.derive(
            base.source,
            final_document.source,
            change_set,
            operation_metadata(transaction),
            patch_limits,
        )
    except Exception:
        raise HclEditFailure(HclEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED) from None
    try:
        untouched_proof = UntouchedByteProof.create(
            base.source, final_document.source, list(source_patch.replacements)
        )
    except Exception:
        raise HclEditFailure(HclEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED) from None
    return EditCommit(
        document=final_document,
        change_set=change_set,
        source_patch=source_patch,
        untouched_proof=untouched_proof,
    )


def _build_mappings(
    base: HclDocument,
    transaction: EditTransaction,
    final_document: HclDocument,
) -> list[NodeMapping]:
    """One old-to-new mapping per operation whose target resolves in the
    base snapshot; insertions carry no mapping (edit.rs:1884-1953)."""
    mappings: list[NodeMapping] = []
    for operation in transaction.operations:
        kind = operation.kind
        if kind is EditOperationKind.SET_ATTRIBUTE_VALUE:
            old = _resolve_attribute_mapping(base, operation.body, operation.attribute)
            if old is None:
                continue
            new = _resolve_attribute_mapping(final_document, operation.body, operation.attribute)
            mappings.append(
                NodeMapping(
                    old=old,
                    new=new,
                    status=NodeMappingStatus.REPLACED,
                    reason=None if new is not None else "reparsed-node-not-uniquely-located",
                )
            )
        elif kind is EditOperationKind.RENAME_ATTRIBUTE:
            old = _resolve_attribute_mapping(base, operation.body, operation.attribute)
            if old is None:
                continue
            new = _resolve_attribute_mapping(final_document, operation.body, operation.name)
            mappings.append(
                NodeMapping(
                    old=old,
                    new=new,
                    status=NodeMappingStatus.REPLACED,
                    reason=None if new is not None else "reparsed-node-not-uniquely-located",
                )
            )
        elif kind is EditOperationKind.REMOVE_ATTRIBUTE:
            old = _resolve_attribute_mapping(base, operation.body, operation.attribute)
            if old is None:
                continue
            mappings.append(
                NodeMapping(old=old, new=None, status=NodeMappingStatus.DELETED, reason=None)
            )
        elif kind is EditOperationKind.REMOVE_BLOCK:
            old = _resolve_block_mapping(
                base, operation.body, operation.block_type, operation.labels, operation.occurrence
            )
            if old is None:
                continue
            mappings.append(
                NodeMapping(old=old, new=None, status=NodeMappingStatus.DELETED, reason=None)
            )
    return mappings


def _resolve_attribute_mapping(
    document: HclDocument, body: BodyPath, name: str
) -> NodeRef | None:
    try:
        target_body, _ = _resolve_body(document, body)
    except HclEditFailure:
        return None
    attribute = _find_attribute(target_body, name)
    if attribute is None:
        return None
    return document.node_ref(attribute)


def _resolve_block_mapping(
    document: HclDocument,
    body: BodyPath,
    block_type: str,
    labels: tuple[str, ...],
    occurrence: int,
) -> NodeRef | None:
    try:
        target_body, _ = _resolve_body(document, body)
    except HclEditFailure:
        return None
    block = _find_block_in_body(target_body, block_type, labels, occurrence)
    if block is None:
        return None
    return document.node_ref(block)


def _source_patch_limits(limits: HclParseLimits, operation_count: int) -> SourcePatchLimits:
    return SourcePatchLimits(
        source=SourceLimits(
            max_raw_bytes=limits.common.max_source_bytes,
            max_decoded_utf8_bytes=limits.common.max_source_bytes,
            max_decoded_scalars=limits.common.max_source_bytes,
        ),
        max_replacements=operation_count,
        max_patch_bytes=limits.common.max_source_bytes * 2,
    )


def operation_metadata(transaction: EditTransaction) -> dict[str, str]:
    """Operation metadata keys: operation.{index} = "id@version"
    (edit.rs:2037-2042)."""
    metadata: dict[str, str] = {}
    for index, operation in enumerate(transaction.operations):
        metadata[f"operation.{index}"] = _operation_id(operation)
    return metadata


_OPERATION_ID_BY_KIND = {
    EditOperationKind.SET_ATTRIBUTE_VALUE: "hcl.edit.set-attribute-value@1",
    EditOperationKind.INSERT_ATTRIBUTE: "hcl.edit.insert-attribute@1",
    EditOperationKind.REMOVE_ATTRIBUTE: "hcl.edit.remove-attribute@1",
    EditOperationKind.RENAME_ATTRIBUTE: "hcl.edit.rename-attribute@1",
    EditOperationKind.INSERT_BLOCK: "hcl.edit.insert-block@1",
    EditOperationKind.REMOVE_BLOCK: "hcl.edit.remove-block@1",
}


def _operation_id(operation: EditOperation) -> str:
    return _OPERATION_ID_BY_KIND[operation.kind]


def operation_summaries(transaction: EditTransaction) -> list[EditOperationSummary]:
    """Safe, content-free operation summaries (RFC 0004 §14)."""
    summaries: list[EditOperationSummary] = []
    for operation in transaction.operations:
        arguments: dict[str, str] = {}
        kind = operation.kind
        if kind in (
            EditOperationKind.SET_ATTRIBUTE_VALUE,
            EditOperationKind.REMOVE_ATTRIBUTE,
            EditOperationKind.RENAME_ATTRIBUTE,
        ):
            arguments["attribute"] = operation.attribute
        if kind is EditOperationKind.INSERT_ATTRIBUTE:
            arguments["name"] = operation.name
        if kind is EditOperationKind.RENAME_ATTRIBUTE:
            arguments["name"] = operation.name
        if kind is EditOperationKind.INSERT_BLOCK:
            arguments["type"] = operation.block_type
        if kind is EditOperationKind.REMOVE_BLOCK:
            arguments["type"] = operation.block_type
            arguments["occurrence"] = str(operation.occurrence)
        if kind in (
            EditOperationKind.INSERT_ATTRIBUTE,
            EditOperationKind.INSERT_BLOCK,
        ) and operation.placement is not None:
            arguments["placement"] = operation.placement.kind
        operation_id, version = _operation_id(operation).rsplit("@", 1)
        summaries.append(
            EditOperationSummary.new(
                FormatOperationId.new(operation_id, int(version)), arguments
            )
        )
    return summaries
