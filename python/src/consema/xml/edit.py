"""Snapshot-bound XML structural edit (RFC 0012 §11).

Authority:

- RFC 0012 §11 (docs/rfcs/0012-xml-1.0-safe-profile-v1.md:374-404): V1
  publishes eight versioned operations (xml.edit.replace-text@1,
  insert-attribute@1, remove-attribute@1, rename-attribute@1,
  set-attribute-value@1, insert-element@1, remove-element@1,
  rename-element@1); each operation targets one exact ``NodeRef``;
  placement uses one exact parent and an optional sibling/attribute
  anchor; duplicate expanded attributes, invalid namespace bindings,
  unbound prefixes, reserved-prefix misuse, ancestor/self placement, stale
  snapshots, overlapping replacements, and operations that would break
  mixed-content or document-root invariants fail before commit; semantic
  replacement accepts text or validated QName/expanded name facts, never
  raw untrusted markup; new literal content is XML-escaped under the
  existing encoding; commit preserves every byte outside operation-owned
  spans, reparses the target, verifies promised XML/namespace semantics,
  produces a complete ChangeSet, derives an ``UntouchedByteProof``, and
  emits a replayable ``SourcePatch``; dry-run and commit have identical
  replacement sets and target digest.
- The transaction, validation, preparation, and commit logic transcribe
  crates/consema-xml/src/edit.rs:44-1435 (PreparedEdit:44-56, NameFacts:
  58-89, AttributePlacement:91-101, ContentPlacement:102-111,
  EditOperation:113-176, EditTransaction/Builder:178-304, EditCommit:
  306-317, EditFailure:319-408, commit:410-570, dry_run:572-588,
  validate_dependencies:598-641, encoding helpers:643-743, prepare_*:
  745-1307, find_node_by_span:1309-1336, source_patch_limits:1346-1356,
  operation metadata:1358-1435) — byte/registry arbitration only.
- The failure codes are the registered core.edit.*@1 codes
  (RFC 0004 §17, docs/rfcs/0004-...:387-423; consema.xml.errors).
- Vector coverage: conformance/vectors/xml-1-0-safe-v1.json cases
  ``xml.edit.*`` (lines 437-566).

go/xml is a cross-reference only; no code structure is copied.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.document.change_set import ChangeSet, NodeMapping, NodeMappingStatus, SourceEdit
from consema.document.edit_plan import (
    EditOperationSummary,
    EditPlan,
    EditPlanSourceId,
    FormatOperationId,
)
from consema.document.source import SourceEncoding, SourceEncodingKind, SourceLimits
from consema.document.source_patch import SourcePatch, SourcePatchLimits
from consema.document.structural import (
    FormationStatus,
    NodeRef,
    NodeRole,
    SnapshotIdentity,
    Span,
)
from consema.document.untouched_proof import UntouchedByteProof

from consema.xml.document import (
    Document,
    XmlAttributeData,
    XmlContent,
    XmlContentKind,
    XmlElementData,
    XmlTextData,
)
from consema.xml.errors import XmlEditFailure, XmlEditFailureKind
from consema.xml.namespaces import XML_NAMESPACE_URI, ExpandedName, NamespaceScope
from consema.xml.parser import XmlEncodingSelection, XmlParseLimits, XmlProfile, parse


@dataclass(frozen=True, slots=True)
class NameFacts:
    """A validated element or attribute name for structural operations
    (edit.rs:58-89).

    The prefix must already be bound to ``namespace`` in the target's
    in-scope scope; the edit never guesses or fabricates namespace
    declarations.
    """

    prefix: str | None
    local: str
    namespace: str | None

    @classmethod
    def new(cls, prefix: str | None, local: str, namespace: str | None) -> NameFacts:
        return cls(prefix=prefix, local=local, namespace=namespace)

    @property
    def spelling(self) -> str:
        """Full lexical spelling (edit.rs:83-88)."""
        if self.prefix is not None:
            return f"{self.prefix}:{self.local}"
        return self.local


class PlacementKind(enum.Enum):
    """Closed placement category (edit.rs:91-111)."""

    BEFORE = "Before"
    AFTER = "After"
    END = "End"


@dataclass(frozen=True, slots=True)
class AttributePlacement:
    """Attribute insertion placement inside one start tag (edit.rs:91-101)."""

    kind: PlacementKind
    anchor: NodeRef | None = None

    @classmethod
    def before(cls, anchor: NodeRef) -> AttributePlacement:
        return cls(kind=PlacementKind.BEFORE, anchor=anchor)

    @classmethod
    def after(cls, anchor: NodeRef) -> AttributePlacement:
        return cls(kind=PlacementKind.AFTER, anchor=anchor)

    @classmethod
    def end(cls) -> AttributePlacement:
        return cls(kind=PlacementKind.END)


@dataclass(frozen=True, slots=True)
class ContentPlacement:
    """Content insertion placement inside one element (edit.rs:102-111)."""

    kind: PlacementKind
    anchor: NodeRef | None = None

    @classmethod
    def before(cls, anchor: NodeRef) -> ContentPlacement:
        return cls(kind=PlacementKind.BEFORE, anchor=anchor)

    @classmethod
    def after(cls, anchor: NodeRef) -> ContentPlacement:
        return cls(kind=PlacementKind.AFTER, anchor=anchor)

    @classmethod
    def end(cls) -> ContentPlacement:
        return cls(kind=PlacementKind.END)


class EditOperationKind(enum.Enum):
    """The closed eight-operation surface (edit.rs:113-176)."""

    REPLACE_TEXT = "replace-text"
    INSERT_ATTRIBUTE = "insert-attribute"
    REMOVE_ATTRIBUTE = "remove-attribute"
    RENAME_ATTRIBUTE = "rename-attribute"
    SET_ATTRIBUTE_VALUE = "set-attribute-value"
    INSERT_ELEMENT = "insert-element"
    REMOVE_ELEMENT = "remove-element"
    RENAME_ELEMENT = "rename-element"

    @property
    def operation_id(self) -> str:
        """The frozen operation id@version (edit.rs:1372-1383)."""
        return f"xml.edit.{self.value}@1"


@dataclass(frozen=True, slots=True)
class EditOperation:
    """One snapshot-bound XML structural operation (edit.rs:113-176)."""

    kind: EditOperationKind
    target: NodeRef
    name: NameFacts | None = None
    value: str | None = None
    content: str | None = None
    placement: object | None = None


@dataclass(frozen=True, slots=True)
class EditTransaction:
    """Immutable snapshot-bound transaction (edit.rs:178-197)."""

    base: SnapshotIdentity
    operations: tuple[EditOperation, ...] = field(default_factory=tuple)


class EditTransactionBuilder:
    """Builds one transaction against one immutable snapshot
    (edit.rs:199-304)."""

    __slots__ = ("base", "operations")

    def __init__(self, document: Document) -> None:
        self.base = document.snapshot_identity()
        self.operations: list[EditOperation] = []

    def replace_text(self, target: NodeRef, text: str) -> EditTransactionBuilder:
        self.operations.append(
            EditOperation(kind=EditOperationKind.REPLACE_TEXT, target=target, value=text)
        )
        return self

    def insert_attribute(
        self,
        target: NodeRef,
        name: NameFacts,
        value: str,
        placement: AttributePlacement,
    ) -> EditTransactionBuilder:
        self.operations.append(
            EditOperation(
                kind=EditOperationKind.INSERT_ATTRIBUTE,
                target=target,
                name=name,
                value=value,
                placement=placement,
            )
        )
        return self

    def remove_attribute(self, target: NodeRef) -> EditTransactionBuilder:
        self.operations.append(
            EditOperation(kind=EditOperationKind.REMOVE_ATTRIBUTE, target=target)
        )
        return self

    def rename_attribute(self, target: NodeRef, name: NameFacts) -> EditTransactionBuilder:
        self.operations.append(
            EditOperation(
                kind=EditOperationKind.RENAME_ATTRIBUTE, target=target, name=name
            )
        )
        return self

    def set_attribute_value(self, target: NodeRef, value: str) -> EditTransactionBuilder:
        self.operations.append(
            EditOperation(
                kind=EditOperationKind.SET_ATTRIBUTE_VALUE, target=target, value=value
            )
        )
        return self

    def insert_element(
        self,
        target: NodeRef,
        name: NameFacts,
        content: str | None,
        placement: ContentPlacement,
    ) -> EditTransactionBuilder:
        self.operations.append(
            EditOperation(
                kind=EditOperationKind.INSERT_ELEMENT,
                target=target,
                name=name,
                content=content,
                placement=placement,
            )
        )
        return self

    def remove_element(self, target: NodeRef) -> EditTransactionBuilder:
        self.operations.append(
            EditOperation(kind=EditOperationKind.REMOVE_ELEMENT, target=target)
        )
        return self

    def rename_element(self, target: NodeRef, name: NameFacts) -> EditTransactionBuilder:
        self.operations.append(
            EditOperation(kind=EditOperationKind.RENAME_ELEMENT, target=target, name=name)
        )
        return self

    def build(self) -> EditTransaction:
        return EditTransaction(base=self.base, operations=tuple(self.operations))


@dataclass(frozen=True, slots=True)
class EditCommit:
    """One complete committed edit (edit.rs:306-317)."""

    document: Document
    change_set: ChangeSet
    source_patch: SourcePatch
    untouched_proof: UntouchedByteProof


@dataclass(frozen=True, slots=True)
class _PreparedEdit:
    """One prepared raw-byte edit owned by the transaction (edit.rs:44-56)."""

    old_span: Span
    replacement: bytes
    mapping: tuple[NodeRef, str] | None = None  # (old NodeRef, "Replaced"|"Deleted")


# ---------------------------------------------------------------------------
# Encoding helpers (edit.rs:643-743)
# ---------------------------------------------------------------------------


def _char_width(encoding: SourceEncoding) -> int:
    if encoding.kind in (SourceEncodingKind.UTF16LE, SourceEncodingKind.UTF16BE):
        return 2
    return 1


def _empty_element_tag_close(source: bytes, span_end: int, encoding: SourceEncoding) -> bool:
    """Whether the element tag ending at ``span_end`` is written with a
    ``/>`` close, probed in raw bytes (edit.rs:655-664)."""
    offset = span_end - 2 * _char_width(encoding)
    if offset < 0:
        return False
    slash = offset + 1 if encoding.kind is SourceEncodingKind.UTF16BE else offset
    return source[slash : slash + 1] == b"/"


def _push_encoded_text(output: bytearray, text: str, encoding: SourceEncoding) -> None:
    """Appends literal text to a replacement buffer under the source
    encoding (edit.rs:666-687)."""
    if encoding.kind is SourceEncodingKind.UTF16LE:
        output.extend(text.encode("utf-16-le"))
    elif encoding.kind is SourceEncodingKind.UTF16BE:
        output.extend(text.encode("utf-16-be"))
    else:
        output.extend(text.encode("utf-8"))


def _spelling_bytes(name: NameFacts, encoding: SourceEncoding) -> bytes:
    """Encodes one name spelling under the source encoding (edit.rs:695-704)."""
    output = bytearray()
    if name.prefix is not None:
        _push_encoded_text(output, name.prefix, encoding)
        _push_encoded_text(output, ":", encoding)
    _push_encoded_text(output, name.local, encoding)
    return bytes(output)


def _qname_spelling_bytes(qname, encoding: SourceEncoding) -> bytes:
    """Encodes one source QName spelling under the source encoding
    (edit.rs:706-715)."""
    output = bytearray()
    if qname.prefix is not None:
        _push_encoded_text(output, qname.prefix, encoding)
        _push_encoded_text(output, ":", encoding)
    _push_encoded_text(output, qname.local, encoding)
    return bytes(output)


def _escape_text(text: str, encoding: SourceEncoding) -> bytes:
    """Escapes literal character data for text content (edit.rs:717-728)."""
    output = bytearray()
    for character in text:
        if character == "&":
            _push_encoded_text(output, "&amp;", encoding)
        elif character == "<":
            _push_encoded_text(output, "&lt;", encoding)
        else:
            _push_encoded_text(output, character, encoding)
    return bytes(output)


def _escape_attribute(text: str, encoding: SourceEncoding) -> bytes:
    """Escapes literal text for double-quoted attribute values
    (edit.rs:730-743)."""
    output = bytearray()
    for character in text:
        if character == "&":
            _push_encoded_text(output, "&amp;", encoding)
        elif character == "<":
            _push_encoded_text(output, "&lt;", encoding)
        elif character == '"':
            _push_encoded_text(output, "&quot;", encoding)
        else:
            _push_encoded_text(output, character, encoding)
    return bytes(output)


def _leading_whitespace_start(source: bytes, start: int) -> int:
    """Scans back over the leading whitespace of one occurrence
    (edit.rs:1338-1344)."""
    cursor = start
    while cursor > 0 and source[cursor - 1 : cursor] in (b" ", b"\t", b"\r", b"\n"):
        cursor -= 1
    return cursor


# ---------------------------------------------------------------------------
# Transaction validation and commit (edit.rs:410-595)
# ---------------------------------------------------------------------------


def _validate_dependencies(transaction: EditTransaction) -> None:
    """Cross-operation dependency checks before any span is computed
    (edit.rs:598-641)."""
    targets: set[NodeRef] = set()
    for operation in transaction.operations:
        anchor: NodeRef | None = None
        if operation.kind in (
            EditOperationKind.INSERT_ATTRIBUTE,
            EditOperationKind.INSERT_ELEMENT,
        ):
            placement = operation.placement
            if placement is not None and placement.kind is not PlacementKind.END:
                anchor = placement.anchor
        if operation.target in targets:
            raise XmlEditFailure(XmlEditFailureKind.CONFLICTING_EDITS)
        targets.add(operation.target)
        if anchor is not None and anchor in targets:
            raise XmlEditFailure(XmlEditFailureKind.PLACEMENT_ANCHOR_MODIFIED)


def Document_commit(self: Document, transaction: EditTransaction) -> EditCommit:
    """Atomically commits structural operations. On failure the document
    remains unchanged (edit.rs:410-570)."""
    if transaction.base != self.snapshot_identity():
        raise XmlEditFailure(XmlEditFailureKind.WRONG_SNAPSHOT)
    if self.status is not FormationStatus.COMPLETE:
        raise XmlEditFailure(XmlEditFailureKind.INCOMPLETE_TARGET)
    _validate_dependencies(transaction)
    prepared: list[_PreparedEdit] = []
    for operation in transaction.operations:
        prepared.extend(_prepare_operation(self, operation))
    prepared.sort(key=lambda edit: (edit.old_span.start_byte, edit.old_span.end_byte))
    for first, second in zip(prepared, prepared[1:]):
        if first.old_span == second.old_span or (
            first.old_span.is_empty()
            and second.old_span.is_empty()
            and first.old_span.start_byte == second.old_span.start_byte
        ):
            raise XmlEditFailure(XmlEditFailureKind.OVERLAPPING_OWNERSHIP)
        if (
            not first.old_span.is_empty()
            and not second.old_span.is_empty()
            and first.old_span.end_byte > second.old_span.start_byte
        ):
            raise XmlEditFailure(XmlEditFailureKind.OVERLAPPING_OWNERSHIP)
    target_len = len(self.render())
    for edit in prepared:
        target_len = target_len - edit.old_span.len() + len(edit.replacement)
        if target_len > self.parse_limits().common.max_source_bytes:
            raise XmlEditFailure(
                XmlEditFailureKind.RESOURCE_LIMIT, limit_name="target-bytes"
            )
    rendered = bytearray()
    cursor = 0
    for edit in prepared:
        rendered.extend(self.render()[cursor : edit.old_span.start_byte])
        rendered.extend(edit.replacement)
        cursor = edit.old_span.end_byte
    rendered.extend(self.render()[cursor:])
    new_document = parse(
        bytes(rendered),
        XmlProfile.SAFE_V1,
        XmlEncodingSelection.profile_default(),
        self.parse_limits(),
    )
    if new_document.status is not FormationStatus.COMPLETE:
        raise XmlEditFailure(XmlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
    delta = 0
    source_edits: list[SourceEdit] = []
    mappings: list[NodeMapping] = []
    mapped_old: set[NodeRef] = set()
    for edit in prepared:
        replacement_len = len(edit.replacement)
        new_start = edit.old_span.start_byte + delta
        new_end = new_start + replacement_len
        new_span = new_document._authority.span(new_start, new_end)
        source_edits.append(
            SourceEdit(
                old_span=edit.old_span,
                new_span=new_span,
                replacement=edit.replacement,
            )
        )
        if edit.mapping is not None:
            old, plan = edit.mapping
            if old not in mapped_old:
                mapped_old.add(old)
                if plan == "Replaced":
                    found = _find_node_by_span(new_document, new_start, new_end)
                    mappings.append(
                        NodeMapping(
                            old=old,
                            new=found,
                            status=NodeMappingStatus.REPLACED
                            if found is not None
                            else NodeMappingStatus.UNMAPPED,
                            reason=None
                            if found is not None
                            else "reparsed-node-not-uniquely-located",
                        )
                    )
                else:
                    mappings.append(
                        NodeMapping(
                            old=old, new=None, status=NodeMappingStatus.DELETED, reason=None
                        )
                    )
        delta += replacement_len - edit.old_span.len()
    change_set = ChangeSet(
        old_snapshot=self.snapshot_identity(),
        new_snapshot=new_document.snapshot_identity(),
        source_edits=tuple(source_edits),
        node_mappings=tuple(mappings),
    )
    patch_limits = _source_patch_limits(self.parse_limits(), len(change_set.source_edits))
    source_patch = SourcePatch.derive(
        self.source(),
        new_document.source(),
        change_set,
        _operation_metadata(transaction),
        patch_limits,
    )
    untouched_proof = UntouchedByteProof.create(
        self.source(),
        new_document.source(),
        list(source_patch.replacements),
    )
    return EditCommit(
        document=new_document,
        change_set=change_set,
        source_patch=source_patch,
        untouched_proof=untouched_proof,
    )


def Document_dry_run(
    self: Document, transaction: EditTransaction, source_id: EditPlanSourceId
) -> EditPlan:
    """Fully validates and plans a transaction without returning a new
    Document (edit.rs:572-588; RFC 0004 §14)."""
    commit = Document_commit(self, transaction)
    return EditPlan.new(
        source_id,
        self.profile(),
        _operation_summaries(transaction),
        commit.source_patch,
        [],
    )


# ---------------------------------------------------------------------------
# Operation preparation (edit.rs:745-1307)
# ---------------------------------------------------------------------------


def _prepare_operation(self: Document, operation: EditOperation) -> list[_PreparedEdit]:
    kind = operation.kind
    if kind is EditOperationKind.REPLACE_TEXT:
        return _prepare_replace_text(self, operation.target, operation.value or "")
    if kind is EditOperationKind.INSERT_ATTRIBUTE:
        assert operation.name is not None and operation.placement is not None
        return _prepare_insert_attribute(
            self, operation.target, operation.name, operation.value or "", operation.placement
        )
    if kind is EditOperationKind.REMOVE_ATTRIBUTE:
        return _prepare_remove_attribute(self, operation.target)
    if kind is EditOperationKind.RENAME_ATTRIBUTE:
        assert operation.name is not None
        return _prepare_rename_attribute(self, operation.target, operation.name)
    if kind is EditOperationKind.SET_ATTRIBUTE_VALUE:
        return _prepare_set_attribute_value(self, operation.target, operation.value or "")
    if kind is EditOperationKind.INSERT_ELEMENT:
        assert operation.name is not None and operation.placement is not None
        return _prepare_insert_element(
            self, operation.target, operation.name, operation.content, operation.placement
        )
    if kind is EditOperationKind.REMOVE_ELEMENT:
        return _prepare_remove_element(self, operation.target)
    assert operation.name is not None
    return _prepare_rename_element(self, operation.target, operation.name)


def _prepare_replace_text(self: Document, target: NodeRef, text: str) -> list[_PreparedEdit]:
    """Replaces one text occurrence; CDATA is never a target (RoleXmlText
    only, edit.rs:778-790)."""
    text_data = _text_for(self, target)
    encoding = self.source().encoding_facts().selected
    return [
        _PreparedEdit(
            old_span=text_data.span,
            replacement=_escape_text(text, encoding),
            mapping=(target, "Replaced"),
        )
    ]


def _prepare_insert_attribute(
    self: Document,
    target: NodeRef,
    name: NameFacts,
    value: str,
    placement: AttributePlacement,
) -> list[_PreparedEdit]:
    """edit.rs:792-862."""
    element = _element_for(self, target)
    _validate_name_facts(name, element, attribute=True)
    _reject_duplicate_attribute(element, name)
    encoding = self.source().encoding_facts().selected
    if placement.kind is PlacementKind.BEFORE:
        assert placement.anchor is not None
        anchor = _attribute_for(self, placement.anchor)
        insert_at = anchor.span.start_byte
        replacement = (
            _spelling_bytes(name, encoding)
            + b"="
            + b'"'
            + _escape_attribute(value, encoding)
            + b'"'
            + b" "
        )
    elif placement.kind is PlacementKind.AFTER:
        assert placement.anchor is not None
        anchor = _attribute_for(self, placement.anchor)
        insert_at = anchor.span.end_byte
        replacement = (
            b" "
            + _spelling_bytes(name, encoding)
            + b"="
            + b'"'
            + _escape_attribute(value, encoding)
            + b'"'
        )
    else:
        empty_element = _empty_element_tag_close(
            self.render(), element.span.end_byte, encoding
        )
        width = _char_width(encoding)
        insert_at = element.span.end_byte - (2 * width if empty_element else width)
        replacement = b" " + _spelling_bytes(name, encoding) + b"=" + b'"' + _escape_attribute(value, encoding) + b'"'
    span = self._authority.span(insert_at, insert_at)
    return [_PreparedEdit(old_span=span, replacement=replacement, mapping=None)]


def _prepare_remove_attribute(self: Document, target: NodeRef) -> list[_PreparedEdit]:
    """edit.rs:864-876: the removal owns the leading whitespace too."""
    attribute = _attribute_for(self, target)
    start = _leading_whitespace_start(self.render(), attribute.span.start_byte)
    span = self._authority.span(start, attribute.span.end_byte)
    return [_PreparedEdit(old_span=span, replacement=b"", mapping=(target, "Deleted"))]


def _prepare_rename_attribute(
    self: Document, target: NodeRef, name: NameFacts
) -> list[_PreparedEdit]:
    """edit.rs:878-914."""
    attribute = _attribute_for(self, target)
    element = next(
        (data for data in _elements(self) if any(a.ordinal == attribute.ordinal for a in data.attributes)),
        None,
    )
    if element is None:
        raise XmlEditFailure(XmlEditFailureKind.TARGET_NOT_FOUND)
    _validate_name_facts(name, element, attribute=True)
    remaining = [a for a in element.attributes if a.ordinal != attribute.ordinal]
    new_expanded = _expanded_name_for_facts(name, element)
    if new_expanded is not None and any(
        a.expanded == new_expanded for a in remaining
    ):
        raise XmlEditFailure(XmlEditFailureKind.DUPLICATE_EXPANDED_ATTRIBUTE)
    encoding = self.source().encoding_facts().selected
    return [
        _PreparedEdit(
            old_span=attribute.qname.span,
            replacement=_spelling_bytes(name, encoding),
            mapping=(target, "Replaced"),
        )
    ]


def _prepare_set_attribute_value(
    self: Document, target: NodeRef, value: str
) -> list[_PreparedEdit]:
    """edit.rs:916-928: only the value span between the quotes is owned."""
    attribute = _attribute_for(self, target)
    encoding = self.source().encoding_facts().selected
    return [
        _PreparedEdit(
            old_span=attribute.value_span,
            replacement=_escape_attribute(value, encoding),
            mapping=(target, "Replaced"),
        )
    ]


def _prepare_insert_element(
    self: Document,
    target: NodeRef,
    name: NameFacts,
    content: str | None,
    placement: ContentPlacement,
) -> list[_PreparedEdit]:
    """edit.rs:930-1007."""
    element = _element_for(self, target)
    _validate_name_facts(name, element, attribute=False)
    encoding = self.source().encoding_facts().selected
    spelling = _spelling_bytes(name, encoding)
    markup = bytearray()
    _push_encoded_text(markup, "<", encoding)
    markup.extend(spelling)
    if content is not None:
        _push_encoded_text(markup, ">", encoding)
        markup.extend(_escape_text(content, encoding))
        _push_encoded_text(markup, "</", encoding)
        markup.extend(spelling)
        _push_encoded_text(markup, ">", encoding)
    else:
        _push_encoded_text(markup, "/>", encoding)
    if placement.kind is PlacementKind.BEFORE or placement.kind is PlacementKind.AFTER:
        assert placement.anchor is not None
        role, span = _content_span_for(self, placement.anchor)
        if not any(
            self._nodes[child].span == span and _node_role(self, child) == role
            for child in element.children
        ):
            raise XmlEditFailure(XmlEditFailureKind.TARGET_NOT_FOUND)
        if placement.kind is PlacementKind.BEFORE:
            start = end = span.start_byte
        else:
            start = end = span.end_byte
        replacement = bytes(markup)
    else:
        if element.children:
            at = _content_extent_end(self, element.children[-1])
            start = end = at
            replacement = bytes(markup)
        else:
            end = element.span.end_byte
            if _empty_element_tag_close(self.render(), end, encoding):
                # `<root/>`: replace the `/>` close with `>` plus the new
                # element plus a fresh `</parent-name>` close.
                wrapped = bytearray()
                _push_encoded_text(wrapped, ">", encoding)
                wrapped.extend(markup)
                _push_encoded_text(wrapped, "</", encoding)
                wrapped.extend(_qname_spelling_bytes(element.qname, encoding))
                _push_encoded_text(wrapped, ">", encoding)
                start = end - 2 * _char_width(encoding)
                replacement = bytes(wrapped)
            else:
                # `<root></root>`: insert directly before the explicit end tag.
                start = end = end
                replacement = bytes(markup)
    span = self._authority.span(start, end)
    return [_PreparedEdit(old_span=span, replacement=replacement, mapping=None)]


def _prepare_remove_element(self: Document, target: NodeRef) -> list[_PreparedEdit]:
    """edit.rs:1009-1030."""
    element = _element_for(self, target)
    root = self.root()
    if root is not None and root.index == element.index:
        raise XmlEditFailure(XmlEditFailureKind.CANNOT_REMOVE_ROOT)
    start = _leading_whitespace_start(self.render(), element.span.start_byte)
    end = _content_extent_end(self, element.index)
    span = self._authority.span(start, end)
    return [_PreparedEdit(old_span=span, replacement=b"", mapping=(target, "Deleted"))]


def _prepare_rename_element(
    self: Document, target: NodeRef, name: NameFacts
) -> list[_PreparedEdit]:
    """edit.rs:1032-1070: both the start-tag and end-tag names are owned."""
    element = _element_for(self, target)
    _validate_name_facts(name, element, attribute=False)
    encoding = self.source().encoding_facts().selected
    spelling = _spelling_bytes(name, encoding)
    edits = [
        _PreparedEdit(
            old_span=element.qname.span,
            replacement=spelling,
            mapping=(target, "Replaced"),
        )
    ]
    empty_element = _empty_element_tag_close(self.render(), element.span.end_byte, encoding)
    if not empty_element:
        last_child_end = (
            element.span.end_byte
            if not element.children
            else _content_extent_end(self, element.children[-1])
        )
        width = _char_width(encoding)
        name_start = last_child_end + 2 * width
        end_name = self._authority.span(name_start, name_start + element.qname.span.len())
        edits.append(
            _PreparedEdit(old_span=end_name, replacement=spelling, mapping=None)
        )
    return edits


# -- target resolution (edit.rs:1072-1186) -------------------------------------


def _element_for(self: Document, target: NodeRef) -> XmlElementData:
    if target.snapshot != self.snapshot_identity() or target.role is not NodeRole.XML_ELEMENT:
        raise XmlEditFailure(XmlEditFailureKind.WRONG_SNAPSHOT)
    if target.index >= len(self._nodes):
        raise XmlEditFailure(XmlEditFailureKind.TARGET_NOT_FOUND)
    content = self._nodes[target.index]
    if content.kind is not XmlContentKind.ELEMENT:
        raise XmlEditFailure(XmlEditFailureKind.WRONG_ROLE)
    data = content.data
    if data.index != target.index:
        raise XmlEditFailure(XmlEditFailureKind.WRONG_ROLE)
    return data


def _attribute_for(self: Document, target: NodeRef) -> XmlAttributeData:
    if target.snapshot != self.snapshot_identity() or target.role is not NodeRole.XML_ATTRIBUTE:
        raise XmlEditFailure(XmlEditFailureKind.WRONG_SNAPSHOT)
    for attribute in _attributes(self):
        if attribute.ordinal == target.index:
            return attribute
    raise XmlEditFailure(XmlEditFailureKind.TARGET_NOT_FOUND)


def _text_for(self: Document, target: NodeRef) -> XmlTextData:
    if target.snapshot != self.snapshot_identity() or target.role is not NodeRole.XML_TEXT:
        raise XmlEditFailure(XmlEditFailureKind.WRONG_SNAPSHOT)
    for text in _texts(self):
        if text.ordinal == target.index:
            return text
    raise XmlEditFailure(XmlEditFailureKind.TARGET_NOT_FOUND)


def _elements(self: Document):
    for content in self._nodes:
        if content.kind is XmlContentKind.ELEMENT:
            yield content.data


def _attributes(self: Document):
    for element in _elements(self):
        yield from element.attributes


def _texts(self: Document):
    for content in self._nodes:
        if content.kind is XmlContentKind.TEXT:
            yield content.data


def _node_role(self: Document, index: int) -> NodeRole:
    content = self._nodes[index]
    return {
        XmlContentKind.ELEMENT: NodeRole.XML_ELEMENT,
        XmlContentKind.TEXT: NodeRole.XML_TEXT,
        XmlContentKind.CDATA: NodeRole.XML_CDATA,
        XmlContentKind.COMMENT: NodeRole.XML_COMMENT,
        XmlContentKind.PROCESSING_INSTRUCTION: NodeRole.XML_PROCESSING_INSTRUCTION,
        XmlContentKind.ERROR_REGION: NodeRole.XML_ERROR_REGION,
    }[content.kind]


def _content_extent_end(self: Document, index: int) -> int:
    """The exact end of one content item's full extent: for an element
    child this is its closing end tag, not its start-tag end
    (edit.rs:1110-1144)."""
    content = self._nodes[index]
    if content.kind is not XmlContentKind.ELEMENT:
        return content.span.end_byte
    data = content.data
    encoding = self.source().encoding_facts().selected
    width = _char_width(encoding)
    if not data.children:
        if _empty_element_tag_close(self.render(), data.span.end_byte, encoding):
            return data.span.end_byte
        return data.span.end_byte + 2 * width + data.qname.span.len() + width
    return (
        _content_extent_end(self, data.children[-1])
        + 2 * width
        + data.qname.span.len()
        + width
    )


def _content_span_for(self: Document, target: NodeRef) -> tuple[NodeRole, Span]:
    """One content item span by role (edit.rs:1146-1186)."""
    if target.snapshot != self.snapshot_identity():
        raise XmlEditFailure(XmlEditFailureKind.WRONG_SNAPSHOT)
    if target.role is NodeRole.XML_ELEMENT:
        return NodeRole.XML_ELEMENT, _element_for(self, target).span
    if target.role is NodeRole.XML_TEXT:
        return NodeRole.XML_TEXT, _text_for(self, target).span
    if target.role is NodeRole.XML_CDATA:
        for content in self._nodes:
            if content.kind is XmlContentKind.CDATA and content.data.ordinal == target.index:
                return NodeRole.XML_CDATA, content.data.span
        raise XmlEditFailure(XmlEditFailureKind.TARGET_NOT_FOUND)
    if target.role is NodeRole.XML_COMMENT:
        for content in self._nodes:
            if content.kind is XmlContentKind.COMMENT and content.data.ordinal == target.index:
                return NodeRole.XML_COMMENT, content.data.span
        raise XmlEditFailure(XmlEditFailureKind.TARGET_NOT_FOUND)
    if target.role is NodeRole.XML_PROCESSING_INSTRUCTION:
        for content in self._nodes:
            if (
                content.kind is XmlContentKind.PROCESSING_INSTRUCTION
                and content.data.ordinal == target.index
            ):
                return NodeRole.XML_PROCESSING_INSTRUCTION, content.data.span
        raise XmlEditFailure(XmlEditFailureKind.TARGET_NOT_FOUND)
    raise XmlEditFailure(XmlEditFailureKind.WRONG_ROLE)


# -- name validation (edit.rs:1188-1307) ----------------------------------------


def _validate_name_facts(name: NameFacts, element: XmlElementData, attribute: bool) -> None:
    """Validates name facts against one element's in-scope scope
    (edit.rs:1188-1255)."""
    if (
        not name.local
        or ":" in name.local
        or name.local[0].isdigit()
        or name.local[0] == "-"
    ):
        raise XmlEditFailure(XmlEditFailureKind.INVALID_QNAME)
    if name.prefix is None and name.namespace is not None:
        if attribute:
            # An unprefixed attribute never carries a namespace.
            raise XmlEditFailure(XmlEditFailureKind.UNBOUND_PREFIX, prefix="")
        default = next(
            (
                binding.uri
                for binding in reversed(element.scope.bindings)
                if binding.prefix is None
            ),
            None,
        )
        if default != name.namespace:
            raise XmlEditFailure(XmlEditFailureKind.UNBOUND_PREFIX, prefix="")
        return
    if name.prefix is not None and name.namespace is None:
        raise XmlEditFailure(XmlEditFailureKind.UNBOUND_PREFIX, prefix=name.prefix)
    if name.prefix is None and name.namespace is None:
        return
    assert name.prefix is not None and name.namespace is not None
    if name.prefix == "xmlns":
        raise XmlEditFailure(XmlEditFailureKind.RESERVED_PREFIX, prefix=name.prefix)
    if name.prefix == "xml" and name.namespace != XML_NAMESPACE_URI:
        raise XmlEditFailure(XmlEditFailureKind.UNBOUND_PREFIX, prefix=name.prefix)
    bound = next(
        (
            binding.uri
            for binding in reversed(element.scope.bindings)
            if binding.prefix == name.prefix
        ),
        "",
    )
    if bound != name.namespace:
        raise XmlEditFailure(XmlEditFailureKind.UNBOUND_PREFIX, prefix=name.prefix)


def _expanded_name_for_facts(name: NameFacts, element: XmlElementData) -> ExpandedName | None:
    """The expanded name promised by name facts, when resolvable
    (edit.rs:1257-1287)."""
    if name.namespace is None:
        return None
    if name.prefix == "xml":
        return ExpandedName(namespace=XML_NAMESPACE_URI, local=name.local)
    bound = next(
        (
            binding.uri
            for binding in reversed(element.scope.bindings)
            if binding.prefix == (name.prefix or "")
        ),
        None,
    )
    if bound != name.namespace:
        raise XmlEditFailure(XmlEditFailureKind.UNBOUND_PREFIX, prefix=name.prefix or "")
    return ExpandedName(namespace=name.namespace, local=name.local)


def _reject_duplicate_attribute(element: XmlElementData, name: NameFacts) -> None:
    """Rejects an attribute whose expanded name already exists
    (edit.rs:1289-1306)."""
    promised = _expanded_name_for_facts(name, element)
    if promised is None:
        return
    if any(attribute.expanded == promised for attribute in element.attributes):
        raise XmlEditFailure(XmlEditFailureKind.DUPLICATE_EXPANDED_ATTRIBUTE)


# -- commit helpers (edit.rs:1309-1435) -----------------------------------------


def _find_node_by_span(document: Document, start: int, end: int) -> NodeRef | None:
    """One node uniquely located by its exact span (edit.rs:1309-1336)."""
    for content in document._nodes:
        span = content.span
        if span.start_byte == start and span.end_byte == end:
            return document.node_ref(_content_ordinal(content), _content_role(content))
    return None


def _content_role(content: XmlContent) -> NodeRole:
    return {
        XmlContentKind.ELEMENT: NodeRole.XML_ELEMENT,
        XmlContentKind.TEXT: NodeRole.XML_TEXT,
        XmlContentKind.CDATA: NodeRole.XML_CDATA,
        XmlContentKind.COMMENT: NodeRole.XML_COMMENT,
        XmlContentKind.PROCESSING_INSTRUCTION: NodeRole.XML_PROCESSING_INSTRUCTION,
        XmlContentKind.ERROR_REGION: NodeRole.XML_ERROR_REGION,
    }[content.kind]


def _content_ordinal(content: XmlContent) -> int:
    if content.kind is XmlContentKind.ELEMENT:
        return content.data.index
    return content.data.ordinal


def _source_patch_limits(limits: XmlParseLimits, operation_count: int) -> SourcePatchLimits:
    """edit.rs:1346-1356."""
    return SourcePatchLimits(
        source=SourceLimits(
            max_raw_bytes=limits.common.max_source_bytes,
            max_decoded_utf8_bytes=limits.max_decoded_utf8_bytes,
            max_decoded_scalars=limits.max_decoded_scalars,
        ),
        max_replacements=max(operation_count, 1),
        max_patch_bytes=limits.common.max_source_bytes * 2,
    )


def _operation_metadata(transaction: EditTransaction) -> dict[str, str]:
    """edit.rs:1358-1370."""
    return {
        f"operation.{index}": operation.kind.operation_id
        for index, operation in enumerate(transaction.operations)
    }


def _operation_summaries(transaction: EditTransaction) -> list[EditOperationSummary]:
    """edit.rs:1385-1435."""
    summaries: list[EditOperationSummary] = []
    for operation in transaction.operations:
        kind = operation.kind
        if kind is EditOperationKind.REPLACE_TEXT:
            arguments = {"text_bytes": str(len(operation.value or ""))}
            id_name = "xml.edit.replace-text"
        elif kind is EditOperationKind.INSERT_ATTRIBUTE:
            arguments = {
                "name_bytes": str(len(operation.name.spelling) if operation.name else 0),
                "value_bytes": str(len(operation.value or "")),
            }
            id_name = "xml.edit.insert-attribute"
        elif kind is EditOperationKind.REMOVE_ATTRIBUTE:
            arguments = {}
            id_name = "xml.edit.remove-attribute"
        elif kind is EditOperationKind.RENAME_ATTRIBUTE:
            arguments = {"name_bytes": str(len(operation.name.spelling) if operation.name else 0)}
            id_name = "xml.edit.rename-attribute"
        elif kind is EditOperationKind.SET_ATTRIBUTE_VALUE:
            arguments = {"value_bytes": str(len(operation.value or ""))}
            id_name = "xml.edit.set-attribute-value"
        elif kind is EditOperationKind.INSERT_ELEMENT:
            arguments = {
                "name_bytes": str(len(operation.name.spelling) if operation.name else 0),
                "content_bytes": str(len(operation.content or "")),
            }
            id_name = "xml.edit.insert-element"
        elif kind is EditOperationKind.REMOVE_ELEMENT:
            arguments = {}
            id_name = "xml.edit.remove-element"
        else:
            arguments = {"name_bytes": str(len(operation.name.spelling) if operation.name else 0)}
            id_name = "xml.edit.rename-element"
        summaries.append(
            EditOperationSummary.new(FormatOperationId.new(id_name, 1), arguments)
        )
    return summaries


# Bind the document methods (Python-idiomatic alternative to inherent
# extension methods; the family entry points mirror the Rust Document API).
Document.commit = Document_commit  # type: ignore[attr-defined]
Document.dry_run = Document_dry_run  # type: ignore[attr-defined]
