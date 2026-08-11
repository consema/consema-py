"""Plist structural edits: six snapshot-bound operations per profile
(RFC 0013 §11).

Authority (Rust arbitration for exact byte semantics):

- Operation and path model: crates/consema-plist/src/edit.rs:76-186
  (EditPathStep DictKey{key, occurrence} | ArrayIndex, EditPath,
  DictPlacement End | Before | After, EditValue), 187-374 (EditOperation,
  EditTransaction, EditTransactionBuilder).
- Failure algebra and codes: edit.rs:389-455 (EditFailure and the
  StableFailure code mapping: core.edit.*@1 shared codes plus
  plist.edit.uid-in-xml@1 and plist.edit.unrepresentable@1).
- Atomic commit: edit.rs:457-576 — the base snapshot, Complete-with-native
  gate, per-operation reparse under the exact base request, byte splices,
  and final reparse; dry-run produces the identical patch and target
  digest (edit.rs:466-480; RFC 0004 §14).
- XML edits: edit.rs:504-539 (commit_xml), 840-1040 (xml_layout: every
  value element's byte facts in arena ordinal order), 1043-1226
  (prepare_xml_operation: set-value replaces the element span,
  insert-dict-entry/insert-array-element splice at the computed position
  wrapping self-closing tags, remove spans the entry's key-through-value
  range, rename replaces only the key text), 1247-1350 (entry_markup /
  encode_xml_element / encode_xml_key / encode_key_text / encode_text),
  1352-1419 (check_xml_value / check_xml_key / check_xml_string).
- Binary edits: edit.rs:544-575 (commit_binary), 1422-1568 (binary_step:
  container reference blocks re-encoded at the minimal ref width, fresh
  objects appended after the object area, offset table and trailer
  regenerated), 1595-1762 (binary_plan), 1765-1931 (encode_container /
  encode_binary_value / encode_binary_string / width helpers).
- Splice machinery: edit.rs:578-748 (apply_step, record_edit with fold
  merging, unmap_in / map_in, apply_splices), 1572-1592 (shifted /
  add_length_delta).
- Commit artifacts: edit.rs:1935+ (ChangeSet, SourcePatch derivation,
  UntouchedByteProof; RFC 0004 §13-§16). Frozen operation ids:
  crates/consema-plist/src/operation_registry.rs:20-83.

Values are supplied as typed native facts (integer, real, boolean, date,
data, string, UID), never as raw markup or raw bytes (RFC 0013 §11). No
operation writes a filesystem path.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

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
from consema.document.source import SourceLimits, SourceSnapshot
from consema.document.source_patch import SourcePatch, SourcePatchLimits
from consema.document.structural import FormationStatus, NodeRole
from consema.document.untouched_proof import UntouchedByteProof
from consema.plist.conversion import (
    _encode_base64,
    _escape_xml_text,
    _f64_bits,
    _render_real,
    _whole_second_date,
)
from consema.plist.document import PlistDocument, PlistRepresentation
from consema.plist.errors import PlistEditFailure, PlistEditFailureKind
from consema.plist.kinds import PlistEncodingSelection, PlistSyntaxKind, RealWidth
from consema.plist.native import (
    PlistBoolean,
    PlistData,
    PlistDate,
    PlistInteger,
    PlistKey,
    PlistReal,
    PlistString,
    PlistStringStatus,
    PlistUid,
    PlistValueKind,
    PlistValueRef,
)
from consema.plist.parser_binary import BinaryFacts, PlistFormedBinary, parse_binary
from consema.plist.parser_xml import PlistFormedXml, parse_xml


# ---------------------------------------------------------------------------
# Paths, placements, and values (edit.rs:76-186)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EditPathStep:
    """One root-relative path step (RFC 0013 §11; edit.rs:76-94).

    A ``DictKey`` step selects one physical dictionary association by exact
    key content and occurrence: with duplicate keys, ``occurrence`` is the
    0-based source position among the equal keys. An ``ArrayIndex`` step
    selects one array element by its 0-based position.
    """

    kind: str  # "DictKey" | "ArrayIndex"
    key: PlistKey | None = None
    occurrence: int | None = None
    index: int | None = None

    @classmethod
    def dict_key(cls, key: PlistKey, occurrence: int = 0) -> EditPathStep:
        return cls("DictKey", key=key, occurrence=occurrence)

    @classmethod
    def array_index(cls, index: int) -> EditPathStep:
        return cls("ArrayIndex", index=index)


@dataclass(frozen=True, slots=True)
class EditPath:
    """A root-relative path to one value or container (edit.rs:96-130).

    The empty path denotes the root value. A path step that meets a
    container of the wrong kind is a role failure; a step that does not
    exist in the current document state is a missing-target failure.
    """

    segments: tuple[EditPathStep, ...] = ()

    @classmethod
    def root(cls) -> EditPath:
        return cls()

    @classmethod
    def new(cls, steps: tuple[EditPathStep, ...]) -> EditPath:
        return cls(steps)

    def child(self, step: EditPathStep) -> EditPath:
        return EditPath(self.segments + (step,))


class DictPlacement(enum.Enum):
    """Dictionary entry insertion placement (edit.rs:132-143)."""

    END = "End"
    BEFORE = "Before"
    AFTER = "After"


@dataclass(frozen=True, slots=True)
class DictEntryPlacement:
    """One explicit dict-entry placement with its anchor position
    (edit.rs:132-143)."""

    kind: DictPlacement
    position: int | None = None

    @classmethod
    def end(cls) -> DictEntryPlacement:
        return cls(DictPlacement.END)

    @classmethod
    def before(cls, position: int) -> DictEntryPlacement:
        return cls(DictPlacement.BEFORE, position)

    @classmethod
    def after(cls, position: int) -> DictEntryPlacement:
        return cls(DictPlacement.AFTER, position)


@dataclass(frozen=True, slots=True)
class EditValue:
    """One typed native plist value supplied to an edit (RFC 0013 §11;
    edit.rs:146-185). Values are typed native facts, never raw markup or
    raw bytes."""

    kind: PlistValueKind
    payload: object

    @classmethod
    def string(cls, string: PlistString) -> EditValue:
        return cls(PlistValueKind.STRING, string)

    @classmethod
    def integer(cls, integer: PlistInteger) -> EditValue:
        return cls(PlistValueKind.INTEGER, integer)

    @classmethod
    def real(cls, real: PlistReal) -> EditValue:
        return cls(PlistValueKind.REAL, real)

    @classmethod
    def boolean(cls, boolean: PlistBoolean) -> EditValue:
        return cls(PlistValueKind.BOOLEAN, boolean)

    @classmethod
    def date(cls, date: PlistDate) -> EditValue:
        return cls(PlistValueKind.DATE, date)

    @classmethod
    def data(cls, data: PlistData) -> EditValue:
        return cls(PlistValueKind.DATA, data)

    @classmethod
    def uid(cls, uid: PlistUid) -> EditValue:
        return cls(PlistValueKind.UID, uid)


class EditOperationKind(enum.Enum):
    """Typed edit operation kinds (edit.rs:187-251)."""

    SET_VALUE = "SetValue"
    INSERT_DICT_ENTRY = "InsertDictEntry"
    REMOVE_DICT_ENTRY = "RemoveDictEntry"
    RENAME_DICT_KEY = "RenameDictKey"
    INSERT_ARRAY_ELEMENT = "InsertArrayElement"
    REMOVE_ARRAY_ELEMENT = "RemoveArrayElement"


@dataclass(frozen=True, slots=True)
class EditOperation:
    """One snapshot-bound plist structural operation (edit.rs:187-251).

    The path, key, occurrence, index, and placement of every operation
    refer to the document state as of the operation's own application:
    operations of one transaction apply sequentially.
    """

    kind: EditOperationKind
    path: EditPath
    value: EditValue | None = None
    key: PlistKey | None = None
    occurrence: int | None = None
    placement: DictEntryPlacement | None = None
    from_key: PlistKey | None = None
    to_key: PlistKey | None = None
    index: int | None = None


@dataclass(frozen=True, slots=True)
class EditTransaction:
    """Immutable snapshot-bound transaction (edit.rs:253-272)."""

    base: object
    operations: tuple[EditOperation, ...] = ()


class EditTransactionBuilder:
    """Builder that is not a committed edit (edit.rs:274-374)."""

    def __init__(self, document: PlistDocument) -> None:
        self._base = document.snapshot_identity()
        self._operations: list[EditOperation] = []

    def set_value(self, path: EditPath, value: EditValue) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(EditOperationKind.SET_VALUE, path, value=value)
        )
        return self

    def insert_dict_entry(
        self,
        path: EditPath,
        key: PlistKey,
        value: EditValue,
        placement: DictEntryPlacement,
    ) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(
                EditOperationKind.INSERT_DICT_ENTRY,
                path,
                key=key,
                value=value,
                placement=placement,
            )
        )
        return self

    def remove_dict_entry(
        self, path: EditPath, key: PlistKey, occurrence: int = 0
    ) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(
                EditOperationKind.REMOVE_DICT_ENTRY,
                path,
                key=key,
                occurrence=occurrence,
            )
        )
        return self

    def rename_dict_key(
        self,
        path: EditPath,
        from_key: PlistKey,
        occurrence: int,
        to_key: PlistKey,
    ) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(
                EditOperationKind.RENAME_DICT_KEY,
                path,
                from_key=from_key,
                occurrence=occurrence,
                to_key=to_key,
            )
        )
        return self

    def insert_array_element(
        self, path: EditPath, index: int, value: EditValue
    ) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(
                EditOperationKind.INSERT_ARRAY_ELEMENT,
                path,
                index=index,
                value=value,
            )
        )
        return self

    def remove_array_element(self, path: EditPath, index: int) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(EditOperationKind.REMOVE_ARRAY_ELEMENT, path, index=index)
        )
        return self

    def build(self) -> EditTransaction:
        return EditTransaction(base=self._base, operations=tuple(self._operations))


@dataclass(frozen=True, slots=True)
class EditCommit:
    """One complete committed edit (edit.rs:376-387)."""

    document: PlistDocument
    change_set: ChangeSet
    source_patch: SourcePatch
    untouched_proof: UntouchedByteProof


# ---------------------------------------------------------------------------
# Resolution helpers (edit.rs:749-787)
# ---------------------------------------------------------------------------


def _resolve_path(native, path: EditPath) -> PlistValueRef:
    """Resolves one root-relative path against the native arena
    (edit.rs:749-769)."""
    current = native.root()
    for step in path.segments:
        node = native.get(current)
        if node is None:
            raise PlistEditFailure(PlistEditFailureKind.TARGET_NOT_FOUND)
        if step.kind == "DictKey":
            dict_value = node.as_dict()
            if dict_value is None:
                raise PlistEditFailure(PlistEditFailureKind.WRONG_ROLE)
            position = _nth_key_position(dict_value.entries, step.key, step.occurrence or 0)
            current = dict_value.entries[position].value
        else:
            array_value = node.as_array()
            if array_value is None:
                raise PlistEditFailure(PlistEditFailureKind.WRONG_ROLE)
            index = step.index
            if index is None or index >= len(array_value.elements):
                raise PlistEditFailure(PlistEditFailureKind.TARGET_NOT_FOUND)
            current = array_value.elements[index]
    return current


def _nth_key_position(entries, key: PlistKey, occurrence: int) -> int:
    """Source position of the occurrence-th association with the given key
    (edit.rs:772-787)."""
    seen = 0
    for position, entry in enumerate(entries):
        if entry.key == key:
            if seen == occurrence:
                return position
            seen += 1
    raise PlistEditFailure(PlistEditFailureKind.TARGET_NOT_FOUND)


# ---------------------------------------------------------------------------
# Splice machinery (edit.rs:578-748, 1572-1592)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _AppliedEdit:
    pre_start: int
    pre_len: int
    replacement: bytes
    structural: bool = False


def _splice(pre_start: int, pre_len: int, replacement: bytes) -> _AppliedEdit:
    return _AppliedEdit(pre_start, pre_len, bytes(replacement), False)


def _structural_splice(pre_start: int, pre_len: int, replacement: bytes) -> _AppliedEdit:
    return _AppliedEdit(pre_start, pre_len, bytes(replacement), True)


def _shifted(base: int, delta: int) -> int:
    value = base + delta
    if value < 0:
        raise PlistEditFailure(
            PlistEditFailureKind.RESOURCE_LIMIT, resource_name="target-bytes"
        )
    return value


def _map_in(edits: list[_AppliedEdit], pos: int) -> int:
    """Maps one position from one pre-state to the final state through the
    applied edits in application order (edit.rs:648-659)."""
    for edit in edits:
        if pos <= edit.pre_start:
            continue
        if pos < edit.pre_start + edit.pre_len:
            raise PlistEditFailure(PlistEditFailureKind.OVERLAPPING_OWNERSHIP)
        pos = pos + len(edit.replacement) - edit.pre_len
    return pos


def _unmap_in(edits: list[_AppliedEdit], pos: int) -> int:
    """Maps one position from the final state back to the base snapshot
    through the applied edits in reverse application order (edit.rs:627-
    644)."""
    for index in range(len(edits) - 1, -1, -1):
        edit = edits[index]
        if pos <= edit.pre_start:
            continue
        if pos < edit.pre_start + len(edit.replacement):
            base_start = _unmap_in(edits[:index], edit.pre_start)
            return base_start + (pos - edit.pre_start)
        pos = pos - len(edit.replacement) + edit.pre_len
    return pos


def _record_edit(
    edits: list[_AppliedEdit],
    pre_start: int,
    pre_len: int,
    replacement: bytes,
    structural: bool,
) -> None:
    """Records one splice and rejects two insertions that map to the same
    base position; an operation whose span lies inside an earlier
    replacement folds into it (edit.rs:668-732)."""
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
            edits[index] = _AppliedEdit(
                edits[index].pre_start, edits[index].pre_len, merged, edits[index].structural
            )
            return
    edits.append(_AppliedEdit(pre_start, pre_len, bytes(replacement), structural))


def _apply_splices(bytes_: bytes, splices: list[_AppliedEdit]) -> bytes:
    """Builds the new bytes by applying the splices sequentially against a
    working buffer; every splice's pre-span is expressed in its own pre-
    state, so each application position is exact in the evolving bytes
    (edit.rs:730-746)."""
    working = bytearray(bytes_)
    for splice in splices:
        end = splice.pre_start + splice.pre_len
        if end > len(working):
            raise PlistEditFailure(PlistEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        working[splice.pre_start:end] = splice.replacement
    return bytes(working)


def _apply_step(
    edits: list[_AppliedEdit],
    bytes_: bytes,
    limits,
    splices: list[_AppliedEdit],
) -> bytes:
    """Applies one step's splices with target-length validation (hard gate
    4; edit.rs:581-607)."""
    target_len = len(bytes_)
    for splice in splices:
        target_len = target_len - splice.pre_len + len(splice.replacement)
        if target_len < 0:
            raise PlistEditFailure(
                PlistEditFailureKind.RESOURCE_LIMIT, resource_name="target-bytes"
            )
    if target_len > limits.common.max_source_bytes:
        raise PlistEditFailure(
            PlistEditFailureKind.RESOURCE_LIMIT, resource_name="target-bytes"
        )
    for splice in splices:
        _record_edit(
            edits,
            splice.pre_start,
            splice.pre_len,
            splice.replacement,
            splice.structural,
        )
    return _apply_splices(bytes_, splices)


# ---------------------------------------------------------------------------
# XML layout (edit.rs:840-1040)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _XmlKeyLayout:
    text: tuple[int, int]
    element: tuple[int, int]
    self_closing: bool


@dataclass(slots=True)
class _XmlNodeLayout:
    span: tuple[int, int]
    self_closing: bool
    open_end: int
    close_start: int
    children: list
    key_text: list
    entry_starts: list


@dataclass(slots=True)
class _XmlFrame:
    kind: PlistSyntaxKind
    open_start: int
    open_end: int
    children: list
    key_text: list
    entry_starts: list
    prev_value_end: int
    pending_key: _XmlKeyLayout | None = None


_OPEN_KINDS = {
    PlistSyntaxKind.DICT_OPEN,
    PlistSyntaxKind.ARRAY_OPEN,
    PlistSyntaxKind.STRING_OPEN,
    PlistSyntaxKind.INTEGER_OPEN,
    PlistSyntaxKind.REAL_OPEN,
    PlistSyntaxKind.DATE_OPEN,
    PlistSyntaxKind.DATA_OPEN,
}

_CLOSE_KINDS = {
    PlistSyntaxKind.DICT_CLOSE,
    PlistSyntaxKind.ARRAY_CLOSE,
    PlistSyntaxKind.STRING_CLOSE,
    PlistSyntaxKind.INTEGER_CLOSE,
    PlistSyntaxKind.REAL_CLOSE,
    PlistSyntaxKind.DATE_CLOSE,
    PlistSyntaxKind.DATA_CLOSE,
}


def _open_kind_for(close: PlistSyntaxKind) -> PlistSyntaxKind:
    return {
        PlistSyntaxKind.DICT_CLOSE: PlistSyntaxKind.DICT_OPEN,
        PlistSyntaxKind.ARRAY_CLOSE: PlistSyntaxKind.ARRAY_OPEN,
        PlistSyntaxKind.STRING_CLOSE: PlistSyntaxKind.STRING_OPEN,
        PlistSyntaxKind.INTEGER_CLOSE: PlistSyntaxKind.INTEGER_OPEN,
        PlistSyntaxKind.REAL_CLOSE: PlistSyntaxKind.REAL_OPEN,
        PlistSyntaxKind.DATE_CLOSE: PlistSyntaxKind.DATE_OPEN,
        PlistSyntaxKind.DATA_CLOSE: PlistSyntaxKind.DATA_OPEN,
    }[close]


def _piece_text(source, start: int, end: int) -> str:
    """Decoded text of one piece span (edit.rs:1027-1040)."""
    try:
        return source.bytes()[start:end].decode("utf-8")
    except UnicodeDecodeError:
        decoded = source.decoded_text()
        if decoded is None:
            raise PlistEditFailure(PlistEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        return decoded


def _xml_layout(formed: PlistFormedXml) -> list[_XmlNodeLayout]:
    """Walks the lossless pieces and assigns every value element its byte
    span in arena ordinal order (edit.rs:840-978)."""
    source = formed.source
    pieces = formed.lossless_structural_index().pieces
    kinds = formed.lossless_syntax_kinds()
    layouts: list[_XmlNodeLayout] = []
    stack: list[_XmlFrame] = []
    pending_key_open: tuple[int, int] | None = None
    for piece, kind in zip(pieces, kinds):
        start = piece.span.start_byte
        end = piece.span.end_byte
        if kind is PlistSyntaxKind.KEY_OPEN:
            if _piece_text(source, start, end) == ">":
                if pending_key_open is not None:
                    pending_key_open = (pending_key_open[0], end)
            else:
                pending_key_open = (start, end)
        elif kind is PlistSyntaxKind.KEY_CLOSE:
            text = _piece_text(source, start, end)
            if text.endswith("/>"):
                if pending_key_open is not None:
                    key = _XmlKeyLayout(
                        (pending_key_open[0], end), (pending_key_open[0], end), True
                    )
                else:
                    key = _XmlKeyLayout((start, end), (start, end), True)
                pending_key_open = None
            else:
                if pending_key_open is not None:
                    key = _XmlKeyLayout(
                        (pending_key_open[1], start), (pending_key_open[0], end), False
                    )
                else:
                    key = _XmlKeyLayout((start, end), (start, end), True)
                pending_key_open = None
            if stack:
                stack[-1].pending_key = key
        elif kind in _OPEN_KINDS:
            if _piece_text(source, start, end) == ">":
                if stack:
                    stack[-1].open_end = end
                    stack[-1].prev_value_end = end
            else:
                stack.append(
                    _XmlFrame(
                        kind=kind,
                        open_start=start,
                        open_end=end,
                        children=[],
                        key_text=[],
                        entry_starts=[],
                        prev_value_end=end,
                    )
                )
        elif kind in _CLOSE_KINDS:
            text = _piece_text(source, start, end)
            if text.endswith("/>"):
                if not stack:
                    raise PlistEditFailure(PlistEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
                frame = stack.pop()
                _finalize_xml_frame(stack, layouts, frame, end, end, True)
            elif stack and stack[-1].kind == _open_kind_for(kind):
                frame = stack.pop()
                _finalize_xml_frame(stack, layouts, frame, start, end, False)
            else:
                raise PlistEditFailure(PlistEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        elif kind in (PlistSyntaxKind.TRUE, PlistSyntaxKind.FALSE):
            text = _piece_text(source, start, end)
            if text == ">":
                if stack:
                    stack[-1].open_end = end
                    stack[-1].prev_value_end = end
            elif text.startswith("</"):
                if not stack:
                    raise PlistEditFailure(PlistEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
                frame = stack.pop()
                _finalize_xml_frame(stack, layouts, frame, start, end, False)
            elif text.endswith("/>"):
                if not stack:
                    raise PlistEditFailure(PlistEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
                frame = stack.pop()
                _finalize_xml_frame(stack, layouts, frame, end, end, True)
            else:
                stack.append(
                    _XmlFrame(
                        kind=kind,
                        open_start=start,
                        open_end=end,
                        children=[],
                        key_text=[],
                        entry_starts=[],
                        prev_value_end=end,
                    )
                )
    return layouts


def _finalize_xml_frame(
    stack: list[_XmlFrame],
    layouts: list[_XmlNodeLayout],
    frame: _XmlFrame,
    close_start: int,
    close_end: int,
    self_closing: bool,
) -> None:
    """Assigns the next arena ordinal to one closed frame and updates its
    parent dictionary's pending entry (edit.rs:996-1024)."""
    ordinal = len(layouts)
    if stack:
        parent = stack[-1]
        if parent.kind is PlistSyntaxKind.DICT_OPEN:
            if parent.pending_key is not None:
                parent.key_text.append(parent.pending_key)
                parent.entry_starts.append(parent.prev_value_end)
                parent.pending_key = None
        parent.children.append(ordinal)
        parent.prev_value_end = close_end
    layouts.append(
        _XmlNodeLayout(
            span=(frame.open_start, close_end),
            self_closing=self_closing,
            open_end=frame.open_end,
            close_start=close_start,
            children=frame.children,
            key_text=frame.key_text,
            entry_starts=frame.entry_starts,
        )
    )


# ---------------------------------------------------------------------------
# XML value/key encoding (edit.rs:1247-1350)
# ---------------------------------------------------------------------------


def _encode_text(text: str, source: SourceSnapshot) -> bytes:
    """Encodes one decoded string under the source encoding
    (edit.rs:1336-1350)."""
    from consema.document.source import SourceEncodingKind

    selected = source.encoding_facts().selected
    kind = selected.kind
    if kind is SourceEncodingKind.UTF16LE:
        return text.encode("utf-16-le")
    if kind is SourceEncodingKind.UTF16BE:
        return text.encode("utf-16-be")
    return text.encode("utf-8")


def _encode_xml_element(value: EditValue, source: SourceSnapshot) -> bytes:
    """One value element written as markup (edit.rs:1258-1308)."""
    if value.kind is PlistValueKind.STRING:
        text = "<string>" + _escape_xml_text(value.payload.to_unicode()) + "</string>"
    elif value.kind is PlistValueKind.INTEGER:
        text = f"<integer>{value.payload.value}</integer>"
    elif value.kind is PlistValueKind.REAL:
        text = f"<real>{_render_real(value.payload)}</real>"
    elif value.kind is PlistValueKind.BOOLEAN:
        text = "<true/>" if value.payload.value else "<false/>"
    elif value.kind is PlistValueKind.DATE:
        fields = _whole_second_date(value.payload.seconds)
        if fields is None:
            raise PlistEditFailure(
                PlistEditFailureKind.UNREPRESENTABLE_VALUE,
                detail="fractional-seconds",
            )
        year, month, day, hour, minute, second = fields
        sign = "-" if year < 0 else ""
        spelling = (
            f"{sign}{abs(year):04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}Z"
        )
        text = f"<date>{spelling}</date>"
    elif value.kind is PlistValueKind.DATA:
        text = f"<data>{_encode_base64(value.payload.bytes)}</data>"
    elif value.kind is PlistValueKind.UID:
        raise PlistEditFailure(PlistEditFailureKind.UID_IN_XML)
    else:
        raise PlistEditFailure(PlistEditFailureKind.UNREPRESENTABLE_VALUE)
    return _encode_text(text, source)


def _encode_xml_key(key: PlistKey, source: SourceSnapshot) -> bytes:
    """One key element written as markup (edit.rs:1311-1321)."""
    text = "<key>" + _escape_xml_text(key.to_unicode()) + "</key>"
    return _encode_text(text, source)


def _encode_key_text(key: PlistKey, source: SourceSnapshot) -> bytes:
    """Escaped key content only (edit.rs:1324-1333)."""
    return _encode_text(_escape_xml_text(key.to_unicode()), source)


def _check_xml_value(value: EditValue) -> None:
    """Validates one typed value for the XML representation (edit.rs:1353-
    1377)."""
    if value.kind is PlistValueKind.STRING:
        _check_xml_string(value.payload)
    elif value.kind is PlistValueKind.REAL:
        if value.payload.width is RealWidth.FLOAT32:
            raise PlistEditFailure(
                PlistEditFailureKind.UNREPRESENTABLE_VALUE, detail="float32-width"
            )
    elif value.kind is PlistValueKind.DATE:
        if _whole_second_date(value.payload.seconds) is None:
            raise PlistEditFailure(
                PlistEditFailureKind.UNREPRESENTABLE_VALUE, detail="fractional-seconds"
            )
    elif value.kind is PlistValueKind.UID:
        raise PlistEditFailure(PlistEditFailureKind.UID_IN_XML)


def _check_xml_key(key: PlistKey) -> None:
    if key.status() is PlistStringStatus.UNPAIRED_SURROGATE:
        raise PlistEditFailure(
            PlistEditFailureKind.UNREPRESENTABLE_VALUE, detail="unpaired-surrogate"
        )


def _check_xml_string(string: PlistString) -> None:
    if string.status() is PlistStringStatus.UNPAIRED_SURROGATE:
        raise PlistEditFailure(
            PlistEditFailureKind.UNREPRESENTABLE_VALUE, detail="unpaired-surrogate"
        )


def _entry_markup(key: PlistKey, value: EditValue, source: SourceSnapshot) -> bytes:
    return _encode_xml_key(key, source) + _encode_xml_element(value, source)


# ---------------------------------------------------------------------------
# XML operation preparation (edit.rs:1043-1226)
# ---------------------------------------------------------------------------


def _prepare_xml_operation(
    formed: PlistFormedXml,
    layout: list[_XmlNodeLayout],
    operation: EditOperation,
) -> list[_AppliedEdit]:
    document = formed.document
    assert document is not None
    source = formed.source
    kind = operation.kind
    if kind is EditOperationKind.SET_VALUE:
        _check_xml_value(operation.value)
        node = _resolve_path(document, operation.path)
        node_layout = layout[node.index]
        return [
            _splice(
                node_layout.span[0],
                node_layout.span[1] - node_layout.span[0],
                _encode_xml_element(operation.value, source),
            )
        ]
    if kind is EditOperationKind.INSERT_DICT_ENTRY:
        _check_xml_key(operation.key)
        _check_xml_value(operation.value)
        dict_ref = _resolve_path(document, operation.path)
        if document.get(dict_ref).as_dict() is None:
            raise PlistEditFailure(PlistEditFailureKind.WRONG_ROLE)
        dict_layout = layout[dict_ref.index]
        count = len(dict_layout.children)
        placement = operation.placement
        assert placement is not None and operation.key is not None and operation.value is not None
        markup = _entry_markup(operation.key, operation.value, source)
        if placement.kind is DictPlacement.END:
            if dict_layout.self_closing:
                replacement = b"<dict>" + markup + b"</dict>"
                return [
                    _splice(
                        dict_layout.span[0],
                        dict_layout.span[1] - dict_layout.span[0],
                        replacement,
                    )
                ]
            return [_splice(dict_layout.close_start, 0, markup)]
        position = placement.position
        if position is None or position >= count:
            raise PlistEditFailure(PlistEditFailureKind.TARGET_NOT_FOUND)
        if placement.kind is DictPlacement.BEFORE:
            return [_splice(dict_layout.entry_starts[position], 0, markup)]
        # After
        after_end = layout[dict_layout.children[position]].span[1]
        return [_splice(after_end, 0, markup)]
    if kind is EditOperationKind.REMOVE_DICT_ENTRY:
        dict_ref = _resolve_path(document, operation.path)
        dict_value = document.get(dict_ref).as_dict()
        if dict_value is None:
            raise PlistEditFailure(PlistEditFailureKind.WRONG_ROLE)
        dict_layout = layout[dict_ref.index]
        position = _nth_key_position(dict_value.entries, operation.key, operation.occurrence or 0)
        span_start = dict_layout.entry_starts[position]
        span_end = layout[dict_layout.children[position]].span[1]
        return [_splice(span_start, span_end - span_start, b"")]
    if kind is EditOperationKind.RENAME_DICT_KEY:
        _check_xml_key(operation.to_key)
        dict_ref = _resolve_path(document, operation.path)
        dict_value = document.get(dict_ref).as_dict()
        if dict_value is None:
            raise PlistEditFailure(PlistEditFailureKind.WRONG_ROLE)
        dict_layout = layout[dict_ref.index]
        position = _nth_key_position(dict_value.entries, operation.from_key, operation.occurrence or 0)
        key_layout = dict_layout.key_text[position]
        if key_layout.self_closing:
            return [
                _splice(
                    key_layout.element[0],
                    key_layout.element[1] - key_layout.element[0],
                    _encode_xml_key(operation.to_key, source),
                )
            ]
        return [
            _splice(
                key_layout.text[0],
                key_layout.text[1] - key_layout.text[0],
                _encode_key_text(operation.to_key, source),
            )
        ]
    if kind is EditOperationKind.INSERT_ARRAY_ELEMENT:
        _check_xml_value(operation.value)
        array_ref = _resolve_path(document, operation.path)
        if document.get(array_ref).as_array() is None:
            raise PlistEditFailure(PlistEditFailureKind.WRONG_ROLE)
        array_layout = layout[array_ref.index]
        count = len(array_layout.children)
        index = operation.index
        if index is None or index > count:
            raise PlistEditFailure(PlistEditFailureKind.TARGET_NOT_FOUND)
        markup = _encode_xml_element(operation.value, source)
        if index == count:
            if array_layout.self_closing:
                replacement = b"<array>" + markup + b"</array>"
                return [
                    _splice(
                        array_layout.span[0],
                        array_layout.span[1] - array_layout.span[0],
                        replacement,
                    )
                ]
            return [_splice(array_layout.close_start, 0, markup)]
        if index == 0:
            return [_splice(array_layout.open_end, 0, markup)]
        return [_splice(layout[array_layout.children[index]].span[0], 0, markup)]
    # RemoveArrayElement
    array_ref = _resolve_path(document, operation.path)
    if document.get(array_ref).as_array() is None:
        raise PlistEditFailure(PlistEditFailureKind.WRONG_ROLE)
    array_layout = layout[array_ref.index]
    count = len(array_layout.children)
    index = operation.index
    if index is None or index >= count:
        raise PlistEditFailure(PlistEditFailureKind.TARGET_NOT_FOUND)
    if index == 0:
        span_start = array_layout.open_end
        span_end = layout[array_layout.children[0]].span[1]
    else:
        span_start = layout[array_layout.children[index - 1]].span[1]
        span_end = layout[array_layout.children[index]].span[1]
    return [_splice(span_start, span_end - span_start, b"")]


# ---------------------------------------------------------------------------
# Binary encoding helpers (edit.rs:1765-1931)
# ---------------------------------------------------------------------------


def _write_sized_marker(out: bytearray, marker: int, count: int) -> None:
    if count < 0x0F:
        out.append(marker | count)
        return
    out.append(marker | 0x0F)
    width = _unsigned_width(count)
    out.append(0x10 | {1: 0, 2: 1, 4: 2, 8: 3}[width])
    out.extend(count.to_bytes(width, "big"))


def _write_be(out: bytearray, value: int, width: int) -> None:
    out.extend(value.to_bytes(width, "big"))


def _ref_width_for(max_index: int) -> int:
    size = 1
    capacity = 256
    while max_index >= capacity and size < 8:
        size += 1
        capacity *= 256
    return size


def _integer_width(value: int) -> int:
    if value >= 0:
        return _unsigned_width(value)
    return 8


def _unsigned_width(value: int) -> int:
    if value <= 0xFF:
        return 1
    if value <= 0xFFFF:
        return 2
    if value <= 0xFFFF_FFFF:
        return 4
    return 8


def _uid_width(value: int) -> int:
    if value <= 0xFF:
        return 1
    if value <= 0xFFFF:
        return 2
    if value <= 0xFF_FFFF:
        return 3
    return 4


def _encode_binary_string(string: PlistString) -> bytes:
    out = bytearray()
    units = string.code_units
    if all(unit < 0x80 for unit in units):
        _write_sized_marker(out, 0x50, len(units))
        for unit in units:
            out.append(unit)
    else:
        _write_sized_marker(out, 0x60, len(units))
        for unit in units:
            out.extend(unit.to_bytes(2, "big"))
    return bytes(out)


def _encode_binary_value(value: EditValue) -> bytes:
    """One binary object payload (edit.rs:1790-1834)."""
    out = bytearray()
    if value.kind is PlistValueKind.STRING:
        return _encode_binary_string(value.payload)
    if value.kind is PlistValueKind.INTEGER:
        integer = value.payload.value
        width = _integer_width(integer)
        out.append(0x10 | {1: 0, 2: 1, 4: 2, 8: 3}[width])
        _write_be(out, integer & 0xFFFFFFFFFFFFFFFF, width)
    elif value.kind is PlistValueKind.REAL:
        real = value.payload
        if real.width is RealWidth.FLOAT64:
            out.append(0x23)
            _write_be(out, real.bits, 8)
        else:
            out.append(0x22)
            _write_be(out, real.bits, 4)
    elif value.kind is PlistValueKind.BOOLEAN:
        out.append(0x09 if value.payload.value else 0x08)
    elif value.kind is PlistValueKind.DATE:
        out.append(0x33)
        _write_be(out, _f64_bits(value.payload.seconds), 8)
    elif value.kind is PlistValueKind.DATA:
        data = value.payload.bytes
        _write_sized_marker(out, 0x40, len(data))
        out.extend(data)
    elif value.kind is PlistValueKind.UID:
        uid = value.payload.value
        width = _uid_width(uid)
        out.append(0x80 | (width - 1))
        _write_be(out, uid, width)
    else:
        raise PlistEditFailure(PlistEditFailureKind.UNREPRESENTABLE_VALUE)
    return bytes(out)


def _encode_container(refs: list[int], is_dict: bool, ref_size: int) -> bytes:
    """One container's marker and reference block (edit.rs:1775-1789)."""
    out = bytearray()
    if is_dict:
        count = len(refs) // 2
        _write_sized_marker(out, 0xD0, count)
    else:
        count = len(refs)
        _write_sized_marker(out, 0xA0, count)
    for ref in refs:
        _write_be(out, ref, ref_size)
    return bytes(out)


def _container_is_dict(document, index: int) -> bool:
    node = document.get(PlistValueRef(index))
    return node is not None and node.kind is PlistValueKind.DICT


# ---------------------------------------------------------------------------
# Binary operation planning and splicing (edit.rs:1422-1762)
# ---------------------------------------------------------------------------


def _binary_step(
    formed: PlistFormedBinary, operation: EditOperation, limits
) -> list[_AppliedEdit]:
    document = formed.document
    assert document is not None
    facts = formed.facts
    plan = _binary_plan(document, facts, operation)
    node_count = document.node_count()
    new_object_count = node_count + len(plan["appended"])
    if new_object_count > limits.max_object_count:
        raise PlistEditFailure(
            PlistEditFailureKind.RESOURCE_LIMIT, resource_name="object-count"
        )
    current_ref_size = facts.trailer.object_ref_size
    new_ref_size = _ref_width_for(new_object_count)
    if new_ref_size > limits.max_object_ref_size:
        raise PlistEditFailure(
            PlistEditFailureKind.RESOURCE_LIMIT, resource_name="object-ref-size"
        )
    replacements: dict[int, bytes] = dict(plan["scalar_replaces"])
    for index in plan["container_touched"]:
        replacements[index] = _encode_container(
            plan["refs"][index], _container_is_dict(document, index), new_ref_size
        )
    if new_ref_size != current_ref_size:
        for index in range(node_count):
            node = document.get(PlistValueRef(index))
            if _container_is_dict(document, index) or (
                node is not None and node.kind is PlistValueKind.ARRAY
            ):
                replacements[index] = _encode_container(
                    plan["refs"][index], _container_is_dict(document, index), new_ref_size
                )

    new_lens: list[int] = [
        facts.objects[index].span.len() for index in range(node_count)
    ]
    splices: list[_AppliedEdit] = []
    delta = 0
    for index in sorted(replacements):
        span = facts.objects[index].span
        new_lens[index] = len(replacements[index])
        pre_start = _shifted(span.start_byte, delta)
        splices.append(_splice(pre_start, span.len(), replacements[index]))
        delta = delta + len(replacements[index]) - span.len()

    object_area_end = facts.trailer.offset_table_offset
    appended_bytes = b"".join(plan["appended"])
    if appended_bytes:
        pre_start = _shifted(object_area_end, delta)
        splices.append(_splice(pre_start, 0, appended_bytes))
        delta = delta + len(appended_bytes)

    new_offsets: list[int] = []
    cursor = 8
    for length in new_lens:
        new_offsets.append(cursor)
        cursor = cursor + length
    for bytes_ in plan["appended"]:
        new_offsets.append(cursor)
        cursor = cursor + len(bytes_)
    new_table_offset = cursor

    old_table_start = _shifted(object_area_end, delta)
    old_table_bytes = facts.trailer.num_objects * facts.trailer.offset_int_size
    offset_int_size = _ref_width_for(new_table_offset)
    if offset_int_size > limits.max_offset_int_size:
        raise PlistEditFailure(
            PlistEditFailureKind.RESOURCE_LIMIT, resource_name="offset-int-size"
        )
    table_bytes = new_object_count * offset_int_size
    if table_bytes > limits.max_offset_table_bytes:
        raise PlistEditFailure(
            PlistEditFailureKind.RESOURCE_LIMIT, resource_name="offset-table-bytes"
        )
    target_len = new_table_offset + table_bytes + 32
    if target_len > limits.common.max_source_bytes:
        raise PlistEditFailure(
            PlistEditFailureKind.RESOURCE_LIMIT, resource_name="target-bytes"
        )
    table = bytearray()
    for offset in new_offsets:
        _write_be(table, offset, offset_int_size)
    table_len = len(table)
    splices.append(_structural_splice(old_table_start, old_table_bytes, bytes(table)))
    delta = delta + table_len - old_table_bytes

    old_len = len(formed.render())
    trailer = bytearray(b"\x00\x00\x00\x00\x00")
    trailer.append(0)  # sortVersion
    trailer.append(offset_int_size)
    trailer.append(new_ref_size)
    trailer.extend(new_object_count.to_bytes(8, "big"))
    trailer.extend(document.root().index.to_bytes(8, "big"))
    trailer.extend(new_table_offset.to_bytes(8, "big"))
    trailer_start = _shifted(old_len, delta) - 32
    if trailer_start < 0:
        raise PlistEditFailure(PlistEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
    splices.append(_structural_splice(trailer_start, 32, bytes(trailer)))
    return splices


def _binary_plan(document, facts: BinaryFacts, operation: EditOperation) -> dict:
    """Computes one operation's structural changes over the current arena
    (edit.rs:1595-1762)."""
    node_count = document.node_count()
    dict_counts: list[int] = []
    for index in range(node_count):
        node = document.get(PlistValueRef(index))
        if node is not None and node.kind is PlistValueKind.DICT:
            dict_counts.append(len(node.payload.entries))
        else:
            dict_counts.append(0)
    key_refs: list[list[int]] = [[] for _ in range(node_count)]
    for reference in facts.refs:
        if reference.position < dict_counts[reference.owner]:
            key_refs[reference.owner].append(reference.target)
    refs: list[list[int]] = [[] for _ in range(node_count)]
    for index in range(node_count):
        node = document.get(PlistValueRef(index))
        if node is None:
            continue
        if node.kind is PlistValueKind.DICT:
            refs[index] = list(key_refs[index])
            refs[index].extend(entry.value.index for entry in node.payload.entries)
        elif node.kind is PlistValueKind.ARRAY:
            refs[index] = [element.index for element in node.payload.elements]

    kind = operation.kind
    if kind is EditOperationKind.SET_VALUE:
        target = _resolve_path(document, operation.path)
        return {
            "refs": refs,
            "appended": [],
            "scalar_replaces": {target.index: _encode_binary_value(operation.value)},
            "container_touched": [],
        }
    if kind is EditOperationKind.INSERT_DICT_ENTRY:
        dict_ref = _resolve_path(document, operation.path)
        dict_value = document.get(dict_ref).as_dict()
        if dict_value is None:
            raise PlistEditFailure(PlistEditFailureKind.WRONG_ROLE)
        count = len(dict_value.entries)
        placement = operation.placement
        assert placement is not None and operation.key is not None and operation.value is not None
        if placement.kind is DictPlacement.END:
            position = count
        elif (
            placement.kind is DictPlacement.BEFORE
            and placement.position is not None
            and placement.position < count
        ):
            position = placement.position
        elif (
            placement.kind is DictPlacement.AFTER
            and placement.position is not None
            and placement.position < count
        ):
            position = placement.position + 1
        else:
            raise PlistEditFailure(PlistEditFailureKind.TARGET_NOT_FOUND)
        key_bytes = _encode_binary_string(operation.key.string)
        value_bytes = _encode_binary_value(operation.value)
        key_index = node_count
        value_index = node_count + 1
        dict_refs = refs[dict_ref.index]
        dict_refs.insert(position, key_index)
        dict_refs.insert(count + 1 + position, value_index)
        return {
            "refs": refs,
            "appended": [key_bytes, value_bytes],
            "scalar_replaces": {},
            "container_touched": [dict_ref.index],
        }
    if kind is EditOperationKind.REMOVE_DICT_ENTRY:
        dict_ref = _resolve_path(document, operation.path)
        dict_value = document.get(dict_ref).as_dict()
        if dict_value is None:
            raise PlistEditFailure(PlistEditFailureKind.WRONG_ROLE)
        position = _nth_key_position(dict_value.entries, operation.key, operation.occurrence or 0)
        count = len(dict_value.entries)
        dict_refs = refs[dict_ref.index]
        del dict_refs[position]
        del dict_refs[count - 1 + position]
        return {
            "refs": refs,
            "appended": [],
            "scalar_replaces": {},
            "container_touched": [dict_ref.index],
        }
    if kind is EditOperationKind.RENAME_DICT_KEY:
        dict_ref = _resolve_path(document, operation.path)
        dict_value = document.get(dict_ref).as_dict()
        if dict_value is None:
            raise PlistEditFailure(PlistEditFailureKind.WRONG_ROLE)
        position = _nth_key_position(dict_value.entries, operation.from_key, operation.occurrence or 0)
        new_key_index = node_count
        refs[dict_ref.index][position] = new_key_index
        return {
            "refs": refs,
            "appended": [_encode_binary_string(operation.to_key.string)],
            "scalar_replaces": {},
            "container_touched": [dict_ref.index],
        }
    if kind is EditOperationKind.INSERT_ARRAY_ELEMENT:
        array_ref = _resolve_path(document, operation.path)
        array_value = document.get(array_ref).as_array()
        if array_value is None:
            raise PlistEditFailure(PlistEditFailureKind.WRONG_ROLE)
        count = len(array_value.elements)
        index = operation.index
        if index is None or index > count:
            raise PlistEditFailure(PlistEditFailureKind.TARGET_NOT_FOUND)
        value_index = node_count
        refs[array_ref.index].insert(index, value_index)
        return {
            "refs": refs,
            "appended": [_encode_binary_value(operation.value)],
            "scalar_replaces": {},
            "container_touched": [array_ref.index],
        }
    # RemoveArrayElement
    array_ref = _resolve_path(document, operation.path)
    array_value = document.get(array_ref).as_array()
    if array_value is None:
        raise PlistEditFailure(PlistEditFailureKind.WRONG_ROLE)
    count = len(array_value.elements)
    index = operation.index
    if index is None or index >= count:
        raise PlistEditFailure(PlistEditFailureKind.TARGET_NOT_FOUND)
    del refs[array_ref.index][index]
    return {
        "refs": refs,
        "appended": [],
        "scalar_replaces": {},
        "container_touched": [array_ref.index],
    }


# ---------------------------------------------------------------------------
# Commit and dry-run (edit.rs:457-576, 1935+)
# ---------------------------------------------------------------------------


def _xml_encoding_selection(document: PlistDocument) -> PlistEncodingSelection:
    """Reparse under the exact request the base was formed with, so the
    committed snapshot reproduces the base encoding facts (edit.rs:515-
    518)."""
    override = document.source.encoding_facts().caller_override
    if override is not None:
        return PlistEncodingSelection.explicit(override)
    return PlistEncodingSelection.profile_default()


def commit(document: PlistDocument, transaction: EditTransaction) -> EditCommit:
    """Atomically commits structural operations; on failure the base
    document remains unchanged (edit.rs:457-575)."""
    if transaction.base != document.snapshot_identity():
        raise PlistEditFailure(PlistEditFailureKind.WRONG_SNAPSHOT)
    if document.formation_status() is not FormationStatus.COMPLETE or document.document() is None:
        raise PlistEditFailure(PlistEditFailureKind.INCOMPLETE_TARGET)
    limits = document.parse_limits()
    if len(transaction.operations) > limits.max_report_events:
        raise PlistEditFailure(
            PlistEditFailureKind.RESOURCE_LIMIT, resource_name="report-events"
        )
    if document.representation() is PlistRepresentation.XML:
        return _commit_xml(document, transaction, limits)
    return _commit_binary(document, transaction, limits)


def _commit_xml(
    document: PlistDocument, transaction: EditTransaction, limits
) -> EditCommit:
    """XML byte-level commit: each operation resolves against the current
    reparse, replaces only operation-owned spans, and reparses after every
    operation (edit.rs:504-539)."""
    selection = _xml_encoding_selection(document)
    bytes_ = document.render()
    edits: list[_AppliedEdit] = []
    for operation in transaction.operations:
        formed = parse_xml(bytes_, selection, limits)
        if formed.status is not FormationStatus.COMPLETE or formed.document is None:
            raise PlistEditFailure(PlistEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        layout = _xml_layout(formed)
        splices = _prepare_xml_operation(formed, layout, operation)
        bytes_ = _apply_step(edits, bytes_, limits, splices)
    final = PlistDocument.parse(bytes_, document.profile, selection, limits)
    if final.formation_status() is not FormationStatus.COMPLETE:
        raise PlistEditFailure(PlistEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
    return _build_commit(document, transaction, final, edits)


def _commit_binary(
    document: PlistDocument, transaction: EditTransaction, limits
) -> EditCommit:
    """Binary structural commit: each operation rewrites the owning object
    bytes, appends fresh objects for new values, regenerates the offset
    table and trailer, and reparses after every operation (edit.rs:544-
    575)."""
    bytes_ = document.render()
    edits: list[_AppliedEdit] = []
    for operation in transaction.operations:
        formed = parse_binary(bytes_, PlistEncodingSelection.profile_default(), limits)
        if formed.status is not FormationStatus.COMPLETE or formed.document is None:
            raise PlistEditFailure(PlistEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        splices = _binary_step(formed, operation, limits)
        bytes_ = _apply_step(edits, bytes_, limits, splices)
    final = PlistDocument.parse(
        bytes_, document.profile, PlistEncodingSelection.profile_default(), limits
    )
    if final.formation_status() is not FormationStatus.COMPLETE:
        raise PlistEditFailure(PlistEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
    return _build_commit(document, transaction, final, edits)


def _build_commit(
    base: PlistDocument,
    transaction: EditTransaction,
    final: PlistDocument,
    edits: list[_AppliedEdit],
) -> EditCommit:
    """Builds the ChangeSet, SourcePatch, and UntouchedByteProof from the
    recorded splices (edit.rs:1935-2033; RFC 0004 §13-§16).

    The recorded edits are merged into maximal non-overlapping base runs
    (spans that overlap or touch, including the binary structural regions
    every step rewrites). Each run's replacement is the exact target bytes
    at its new span, so the change set, patch, and proof are always
    self-consistent with the committed bytes."""
    if len(edits) > base.parse_limits().max_report_events:
        raise PlistEditFailure(
            PlistEditFailureKind.RESOURCE_LIMIT, resource_name="report-events"
        )
    spans: list[tuple[int, int, int]] = []
    for index, edit in enumerate(edits):
        old_start = _unmap_in(edits[:index], edit.pre_start)
        old_end = _unmap_in(edits[:index], edit.pre_start + edit.pre_len)
        delta = len(edit.replacement) - edit.pre_len
        spans.append((old_start, old_end, delta))
    spans.sort(key=lambda item: (item[0], item[1]))
    runs: list[tuple[int, int, int]] = []
    for start, end, delta in spans:
        if runs and start <= runs[-1][1]:
            run_start, run_end, run_delta = runs[-1]
            runs[-1] = (run_start, max(run_end, end), run_delta + delta)
            continue
        runs.append((start, end, delta))
    before_delta = 0
    target_bytes = final.render()
    source_edits: list[SourceEdit] = []
    for start, end, run_delta in runs:
        target_start = _shifted(start, before_delta)
        run_len = (end - start) + run_delta
        if run_len < 0:
            raise PlistEditFailure(PlistEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        target_end = target_start + run_len
        if target_end > len(target_bytes):
            raise PlistEditFailure(PlistEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        source_edits.append(
            SourceEdit(
                old_span=base.authority.span(start, end),
                new_span=final.authority.span(target_start, target_end),
                replacement=bytes(target_bytes[target_start:target_end]),
            )
        )
        before_delta = before_delta + run_delta

    mappings = _node_mappings(base, final, transaction)
    change_set = ChangeSet(
        old_snapshot=base.snapshot_identity(),
        new_snapshot=final.snapshot_identity(),
        source_edits=tuple(source_edits),
        node_mappings=tuple(mappings),
        diagnostics=(),
    )
    patch_limits = SourcePatchLimits(
        source=SourceLimits(
            max_raw_bytes=base.parse_limits().common.max_source_bytes,
            max_decoded_utf8_bytes=base.parse_limits().max_decoded_utf8_bytes,
            max_decoded_scalars=base.parse_limits().max_decoded_scalars,
        ),
        max_replacements=max(len(source_edits), 1),
        max_patch_bytes=base.parse_limits().common.max_source_bytes * 2,
    )
    try:
        source_patch = SourcePatch.derive(
            base.source,
            final.source,
            change_set,
            operation_metadata(transaction),
            patch_limits,
        )
    except Exception as error:
        raise PlistEditFailure(
            PlistEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED,
            detail=f"patch-derive: {error!r}",
        ) from None
    try:
        untouched_proof = UntouchedByteProof.create(
            base.source, final.source, list(source_patch.replacements)
        )
    except Exception as error:
        raise PlistEditFailure(
            PlistEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED,
            detail=f"proof-create: {error!r}",
        ) from None
    return EditCommit(
        document=final,
        change_set=change_set,
        source_patch=source_patch,
        untouched_proof=untouched_proof,
    )


def _node_mappings(
    base: PlistDocument, final: PlistDocument, transaction: EditTransaction
) -> list[NodeMapping]:
    """Old-to-new node mappings: every destructive target maps REPLACED or
    DELETED against the final reparse, containers map UNMAPPED."""
    mappings: list[NodeMapping] = []
    base_native = base.document()
    final_native = final.document()
    assert base_native is not None and final_native is not None
    mapped: set[int] = set()
    for operation in transaction.operations:
        kind = operation.kind
        if kind is EditOperationKind.RENAME_DICT_KEY:
            continue  # the owning dictionary is mapped below
        if kind in (
            EditOperationKind.SET_VALUE,
            EditOperationKind.INSERT_DICT_ENTRY,
            EditOperationKind.INSERT_ARRAY_ELEMENT,
            EditOperationKind.REMOVE_DICT_ENTRY,
            EditOperationKind.REMOVE_ARRAY_ELEMENT,
        ):
            target = _resolve_path(base_native, operation.path)
            if target.index in mapped:
                continue  # the sequential model edits the same container twice
            mapped.add(target.index)
            try:
                new_target = _resolve_path(final_native, operation.path)
            except PlistEditFailure:
                new_target = None
            status = (
                NodeMappingStatus.REPLACED
                if new_target is not None
                else NodeMappingStatus.DELETED
            )
            mappings.append(
                NodeMapping(
                    old=base.authority.node_ref(target.index, NodeRole.PLIST_VALUE),
                    new=(
                        final.authority.node_ref(new_target.index, NodeRole.PLIST_VALUE)
                        if new_target is not None
                        else None
                    ),
                    status=status,
                )
            )
    mappings.append(
        NodeMapping(
            old=base.node_ref(),
            new=final.node_ref(),
            status=NodeMappingStatus.UNMAPPED,
            reason="document-reparsed-after-edits",
        )
    )
    return mappings


def dry_run(
    document: PlistDocument,
    transaction: EditTransaction,
    source_id: EditPlanSourceId,
) -> EditPlan:
    """Fully validates and plans an edit without returning a new Document
    (edit.rs:466-480; RFC 0004 §14)."""
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
        raise PlistEditFailure(PlistEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED) from None


# ---------------------------------------------------------------------------
# Operation metadata and summaries (RFC 0004 §14)
# ---------------------------------------------------------------------------


def operation_metadata(transaction: EditTransaction) -> dict[str, str]:
    """Operation metadata keys: operation.{index} = "id@version"
    (edit.rs:2153-2158)."""
    metadata: dict[str, str] = {}
    for index, operation in enumerate(transaction.operations):
        metadata[f"operation.{index}"] = _operation_id(operation)
    return metadata


def _operation_id(operation: EditOperation) -> str:
    return {
        EditOperationKind.SET_VALUE: "plist.edit.set-value@1",
        EditOperationKind.INSERT_DICT_ENTRY: "plist.edit.insert-dict-entry@1",
        EditOperationKind.REMOVE_DICT_ENTRY: "plist.edit.remove-dict-entry@1",
        EditOperationKind.RENAME_DICT_KEY: "plist.edit.rename-dict-key@1",
        EditOperationKind.INSERT_ARRAY_ELEMENT: "plist.edit.insert-array-element@1",
        EditOperationKind.REMOVE_ARRAY_ELEMENT: "plist.edit.remove-array-element@1",
    }[operation.kind]


def operation_summaries(transaction: EditTransaction) -> list[EditOperationSummary]:
    """Safe, content-free operation summaries (RFC 0004 §14)."""
    summaries = []
    for operation in transaction.operations:
        id_string, arguments = _summary_facts(operation)
        summaries.append(
            EditOperationSummary.new(
                FormatOperationId.new(id_string, 1),
                arguments,
            )
        )
    return summaries


def _summary_facts(operation: EditOperation) -> tuple[str, dict[str, str]]:
    if operation.kind is EditOperationKind.SET_VALUE:
        return (
            "plist.edit.set-value",
            {"path_steps": str(len(operation.path.segments))},
        )
    if operation.kind is EditOperationKind.INSERT_DICT_ENTRY:
        assert operation.key is not None and operation.placement is not None
        return (
            "plist.edit.insert-dict-entry",
            {
                "path_steps": str(len(operation.path.segments)),
                "key_scalars": str(len(operation.key.to_unicode())),
                "placement": operation.placement.kind.value,
            },
        )
    if operation.kind is EditOperationKind.REMOVE_DICT_ENTRY:
        return (
            "plist.edit.remove-dict-entry",
            {"path_steps": str(len(operation.path.segments))},
        )
    if operation.kind is EditOperationKind.RENAME_DICT_KEY:
        assert operation.to_key is not None
        return (
            "plist.edit.rename-dict-key",
            {
                "path_steps": str(len(operation.path.segments)),
                "key_scalars": str(len(operation.to_key.to_unicode())),
            },
        )
    if operation.kind is EditOperationKind.INSERT_ARRAY_ELEMENT:
        return (
            "plist.edit.insert-array-element",
            {"path_steps": str(len(operation.path.segments))},
        )
    return (
        "plist.edit.remove-array-element",
        {"path_steps": str(len(operation.path.segments))},
    )
