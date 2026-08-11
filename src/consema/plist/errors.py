"""Typed plist-family failures with frozen registered codes, and the
SDK-internal diagnostic record.

Frozen code names with authority citations (the ``plist.*`` codes are
registered by RFC 0013 §12 — docs/rfcs/0013-plist-family-profiles-v1.md:
738-752 — and do not enter the ``consema-protocol`` core error registry;
the Rust family's StableFailure impls and parser emission sites are the
arbitration for the exact spellings):

- XML grammar diagnostics ``plist.parse.*@1``: crates/consema-plist/src/
  parser_xml.rs — declaration-version@1 (823), declaration-conflict@1 (856),
  pi-target@1 (936), doctype-subset@1 (1014), doctype@1 (1079),
  dict-missing-value@1 (1163), key-outside-dict@1 (1172), dict-key@1 (1192),
  scalar-content@1 (1209), element-name@1 (1221), root-version@1 (1303),
  root-attribute@1 (1318), element-attribute@1 (1320), mismatched-end-tag@1
  (1458), extra-end-tag@1 (1482), empty-value@1 (1656), integer@1 (1667),
  real@1 (1692), date@1 (1720), data@1 (1757), text-outside-value@1 (1808),
  boolean-content@1 (1826), reference@1 (1960), entity@1 (2052),
  well-formedness@1 (2141), unclosed-element@1 (2158), missing-root@1
  (2178), root-value-count@1 (2188).
- XML fatal/encoding codes: plist.xml.encoding@1 (454), plist.xml.overflow@1
  (2777), plist.xml.internal@1 (2787), plist.xml.coverage@1 (2798),
  plist.xml.coordinates@1 (2808).
- Binary structure diagnostics ``plist.binary.*@1``: parser_binary.rs —
  minimum-size@1 (531), header@1 (548), unproven-top-object@1 (620),
  unproven-reference@1 (634), cycle@1 (660), coverage@1 (753), trailer@1
  (785+), offset-table@1 (942), marker@1 (1110), extent@1 (1137),
  string@1 (1156), date@1 (1170), uid@1 (1181), reference@1 (1218),
  extended-size@1 (1296), non-string-key@1 (1342), overflow@1 (1604),
  internal@1 (1614); plus the fatal plist.binary.encoding@1 (lib.rs:251).
- Limit failures ``plist.limit.*@1``: parser_xml.rs:4146-4300 and
  parser_binary.rs:2897-3155 — string-code-units, data-bytes, array-elements,
  dict-entries, duplicate-key-group, nesting-depth, container-depth,
  object-count, syntax-pieces, recovery-regions, uid-count,
  extended-size-value, extended-size-integers, offset-int-size,
  object-ref-size, offset-table-bytes, binary-facts, conversion-nodes,
  report-events.
- Projection codes: projection.rs:393-402 (incomplete-document@1,
  unpaired-surrogate@1, collision@1, unrepresentable@1, resource-limit@1,
  core-invariant@1).
- Edit codes: edit.rs:442-454 (core.edit.wrong-snapshot@1 / wrong-role@1 /
  target-not-found@1 / incomplete-target@1 / conflicting-edits@1 /
  resource-limit@1 / formation-failed@1, plist.edit.uid-in-xml@1,
  plist.edit.unrepresentable@1).
- Conversion codes: document.rs:264 (same-representation@1), 270-276
  (formation@1), 718 (inexpressible@1), 1297 (internal@1), 1303
  (reparse@1).
- Materialization: materialization.rs:149 (fractional-date@1); the shared
  core.materialization.*@1 codes of consema.document (RFC 0004 §17).
- Conformance vector spellings: crates/consema-conformance/src/plist_v1.rs
  — query failures map to plist.query.*@1 (1143-1154, type-mismatch@1 at
  1149) and materialization failures to plist.materialization.*@1
  (1800-1816).

Design: the plist family raises typed exceptions whose stable ``code`` is
the registered code (RFC 0016 §6). Error text is human presentation only
and never participates in conformance comparison. The vector-facing failure
*names* ("RecoveredDocument", "Collision", ...) are exposed as ``name``
properties using the exact Rust variant spellings the conformance vectors
reference.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.document.structural import Span
from consema.protocol.error_registry import DiagnosticCategory

# ---------------------------------------------------------------------------
# SDK-internal diagnostic record (mirror of consema_core::Diagnostic)
# ---------------------------------------------------------------------------


class PlistSeverity(enum.Enum):
    """The three frozen presentation severities (diagnostic.rs)."""

    INFO = "Info"
    WARNING = "Warning"
    ERROR = "Error"


@dataclass(frozen=True, slots=True)
class RelatedLocation:
    """One related source location with its stable relationship role."""

    role: str
    location: Span


@dataclass(frozen=True, slots=True)
class PlistDiagnostic:
    """One format-layer diagnostic record (mirror of consema_core::Diagnostic).

    The code is always one registered public code; category, severity,
    primary span, arguments, related locations, and the stable occurrence
    ordinal follow the core record shape (RFC 0011 §8). This SDK-internal
    record is distinct from the protocol-layer ``core.diagnostic@1``
    transfer record (consema.protocol.diagnostic.Diagnostic).
    """

    code: str
    category: DiagnosticCategory
    severity: PlistSeverity
    primary: Span | None
    occurrence: int = 0
    arguments: dict[str, str] = field(default_factory=dict, repr=False)
    related: tuple[RelatedLocation, ...] = field(default_factory=tuple, repr=False)
    notes: tuple[str, ...] = field(default_factory=tuple, repr=False)

    def sort_key(self) -> tuple:
        """Deterministic order key (diagnostic.rs:107-123)."""
        start = self.primary.start_byte if self.primary is not None else 2**64 - 1
        return (start, self.category.value, self.code, self.occurrence)


def sort_diagnostics(diagnostics: list[PlistDiagnostic]) -> None:
    """Sorts in place by (primary start, category, code, occurrence)
    (diagnostic.rs:107-123)."""
    diagnostics.sort(key=lambda diagnostic: diagnostic.sort_key())


# ---------------------------------------------------------------------------
# Fatal formation failures
# ---------------------------------------------------------------------------


class PlistFormationFailureKind(enum.Enum):
    """Fatal formation failure categories (FatalFormationFailure of
    consema-document); the resource names follow the Rust spellings used by
    parser_xml.rs / parser_binary.rs and pinned by the RFC 0013 §12 limit
    list (docs/rfcs/0013-...:718-732)."""

    SOURCE_BYTES = "source-bytes"
    DECODED_UTF8_BYTES = "decoded-utf8-bytes"
    DECODED_SCALARS = "decoded-scalars"
    OBJECT_COUNT = "object-count"
    CONTAINER_DEPTH = "container-depth"
    DICT_ENTRIES = "dict-entries"
    ARRAY_ELEMENTS = "array-elements"
    DUPLICATE_KEY_GROUP = "duplicate-key-group"
    STRING_CODE_UNITS = "string-code-units"
    DATA_BYTES = "data-bytes"
    UID_COUNT = "uid-count"
    EXTENDED_SIZE_INTEGERS = "extended-size-integers"
    EXTENDED_SIZE_VALUE = "extended-size-value"
    OFFSET_INT_SIZE = "offset-int-size"
    OBJECT_REF_SIZE = "object-ref-size"
    OFFSET_TABLE_BYTES = "offset-table-bytes"
    SYNTAX_PIECES = "syntax-pieces"
    BINARY_FACTS = "binary-facts"
    CONVERSION_NODES = "conversion-nodes"
    REPORT_EVENTS = "report-events"
    RECOVERY_REGIONS = "recovery-regions"
    NESTING_DEPTH = "nesting-depth"
    TOKEN_COUNT = "token-count"
    NODE_COUNT = "node-count"
    DIAGNOSTICS = "diagnostics"
    XML_ENCODING = "xml-encoding"
    XML_OVERFLOW = "xml-overflow"
    XML_INTERNAL = "xml-internal"
    XML_COVERAGE = "xml-coverage"
    XML_COORDINATES = "xml-coordinates"
    BINARY_ENCODING = "binary-encoding"
    BINARY_MINIMUM_SIZE = "binary-minimum-size"
    BINARY_OVERFLOW = "binary-overflow"
    BINARY_INTERNAL = "binary-internal"
    BINARY_COVERAGE = "binary-coverage"
    SOURCE = "source"


class PlistFormationFailure(Exception):
    """Fatal formation failure; no Document exists.

    Exceeding a configured limit is fatal with no truncation-then-success
    (RFC 0016 §6; RFC 0013 §12, docs/rfcs/0013-...:729-732); an invalid or
    profile-conflicting encoding is likewise fatal before a Document exists
    (RFC 0013 §2). The frozen codes are the ``plist.limit.*@1`` resource
    names, the fatal ``plist.xml.*@1`` / ``plist.binary.*@1`` codes, or the
    wrapped source-layer code (``core.source.*@1``).
    """

    def __init__(
        self,
        kind: PlistFormationFailureKind,
        *,
        resource_name: str | None = None,
        observed: int | None = None,
        limit: int | None = None,
        source=None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.resource_name = resource_name
        self.observed = observed
        self.limit = limit
        self.source = source

    @property
    def name(self) -> str:
        """The exact resource-name or failure spelling the diagnostics
        reference."""
        if self.kind is PlistFormationFailureKind.SOURCE:
            assert self.source is not None
            return self.source.kind.value
        return self.kind.value

    @property
    def code(self) -> str:
        if self.kind is PlistFormationFailureKind.XML_ENCODING:
            return "plist.xml.encoding@1"
        if self.kind is PlistFormationFailureKind.XML_OVERFLOW:
            return "plist.xml.overflow@1"
        if self.kind is PlistFormationFailureKind.XML_INTERNAL:
            return "plist.xml.internal@1"
        if self.kind is PlistFormationFailureKind.XML_COVERAGE:
            return "plist.xml.coverage@1"
        if self.kind is PlistFormationFailureKind.XML_COORDINATES:
            return "plist.xml.coordinates@1"
        if self.kind is PlistFormationFailureKind.BINARY_ENCODING:
            return "plist.binary.encoding@1"
        if self.kind is PlistFormationFailureKind.BINARY_MINIMUM_SIZE:
            return "plist.binary.minimum-size@1"
        if self.kind is PlistFormationFailureKind.BINARY_OVERFLOW:
            return "plist.binary.overflow@1"
        if self.kind is PlistFormationFailureKind.BINARY_INTERNAL:
            return "plist.binary.internal@1"
        if self.kind is PlistFormationFailureKind.BINARY_COVERAGE:
            return "plist.binary.coverage@1"
        if self.kind is PlistFormationFailureKind.SOURCE:
            assert self.source is not None
            return self.source.code
        return f"plist.limit.{self.kind.value}@1"

    def __str__(self) -> str:
        if self.kind is PlistFormationFailureKind.SOURCE:
            return f"plist source failure: {self.source}"
        return (
            f"plist formation limit {self.resource_name or self.kind.value}: "
            f"observed {self.observed} > limit {self.limit}"
        )


# ---------------------------------------------------------------------------
# Projection failures
# ---------------------------------------------------------------------------


class PlistProjectionFailureKind(enum.Enum):
    """Stable projection failure categories (projection.rs:355-375)."""

    INCOMPLETE_DOCUMENT = "IncompleteDocument"
    UNPAIRED_SURROGATE = "UnpairedSurrogate"
    COLLISION = "Collision"
    UNREPRESENTABLE = "Unrepresentable"
    RESOURCE_LIMIT = "ResourceLimit"
    CORE_INVARIANT = "CoreInvariant"


class PlistProjectionFailure(Exception):
    """Stable projection failure with a frozen registered code.

    Code mapping authority: projection.rs:393-402. ``name`` is the exact
    Rust variant spelling the conformance vectors reference.
    """

    def __init__(
        self,
        kind: PlistProjectionFailureKind,
        *,
        key: str | None = None,
        detail: str | None = None,
        resource_name: str | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.key = key
        self.detail = detail
        self.resource_name = resource_name

    @property
    def name(self) -> str:
        return self.kind.value

    @property
    def code(self) -> str:
        return _PROJECTION_CODES[self.kind]


_PROJECTION_CODES = {
    PlistProjectionFailureKind.INCOMPLETE_DOCUMENT: "plist.projection.incomplete-document@1",
    PlistProjectionFailureKind.UNPAIRED_SURROGATE: "plist.projection.unpaired-surrogate@1",
    PlistProjectionFailureKind.COLLISION: "plist.projection.collision@1",
    PlistProjectionFailureKind.UNREPRESENTABLE: "plist.projection.unrepresentable@1",
    PlistProjectionFailureKind.RESOURCE_LIMIT: "plist.projection.resource-limit@1",
    PlistProjectionFailureKind.CORE_INVARIANT: "plist.projection.core-invariant@1",
}


# ---------------------------------------------------------------------------
# Edit failures
# ---------------------------------------------------------------------------


class PlistEditFailureKind(enum.Enum):
    """Stable edit failure categories (edit.rs:391-420)."""

    WRONG_SNAPSHOT = "WrongSnapshot"
    WRONG_ROLE = "WrongRole"
    TARGET_NOT_FOUND = "TargetNotFound"
    INCOMPLETE_TARGET = "IncompleteTarget"
    CONFLICTING_EDITS = "ConflictingEdits"
    OVERLAPPING_OWNERSHIP = "OverlappingOwnership"
    UID_IN_XML = "UidInXml"
    UNREPRESENTABLE_VALUE = "UnrepresentableValue"
    RESOURCE_LIMIT = "ResourceLimit"
    NEW_DOCUMENT_FORMATION_FAILED = "NewDocumentFormationFailed"


class PlistEditFailure(Exception):
    """Stable edit failure with a frozen registered code.

    Code mapping authority: edit.rs:442-454 (RFC 0013 §11 conflict list,
    docs/rfcs/0013-...:703-714). ``name`` is the exact Rust variant spelling
    the conformance vectors reference (plist-v1.json:1557-1561).
    """

    def __init__(
        self,
        kind: PlistEditFailureKind,
        *,
        detail: str | None = None,
        resource_name: str | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.detail = detail
        self.resource_name = resource_name

    @property
    def name(self) -> str:
        return self.kind.value

    @property
    def code(self) -> str:
        return _EDIT_CODES[self.kind]


_EDIT_CODES = {
    PlistEditFailureKind.WRONG_SNAPSHOT: "core.edit.wrong-snapshot@1",
    PlistEditFailureKind.WRONG_ROLE: "core.edit.wrong-role@1",
    PlistEditFailureKind.TARGET_NOT_FOUND: "core.edit.target-not-found@1",
    PlistEditFailureKind.INCOMPLETE_TARGET: "core.edit.incomplete-target@1",
    PlistEditFailureKind.CONFLICTING_EDITS: "core.edit.conflicting-edits@1",
    PlistEditFailureKind.OVERLAPPING_OWNERSHIP: "core.edit.conflicting-edits@1",
    PlistEditFailureKind.UID_IN_XML: "plist.edit.uid-in-xml@1",
    PlistEditFailureKind.UNREPRESENTABLE_VALUE: "plist.edit.unrepresentable@1",
    PlistEditFailureKind.RESOURCE_LIMIT: "core.edit.resource-limit@1",
    PlistEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED: "core.edit.formation-failed@1",
}


# ---------------------------------------------------------------------------
# Conversion failures
# ---------------------------------------------------------------------------


class PlistConversionFailureKind(enum.Enum):
    """Stable conversion failure categories (document.rs:262-288,
    1292-1303)."""

    SAME_REPRESENTATION = "SameRepresentation"
    FORMATION = "Formation"
    INEXPRESSIBLE = "Inexpressible"
    REPARSE = "Reparse"
    INTERNAL = "Internal"


class PlistConversionFailure(Exception):
    """Atomic conversion failure (RFC 0013 §7, hard gate 3).

    A failed conversion returns no target document, no partial bytes, and no
    partial report; the ordered diagnostics explain which facts blocked the
    conversion and why (document.rs:436-459).
    """

    def __init__(
        self,
        kind: PlistConversionFailureKind,
        *,
        detail: str | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.detail = detail

    @property
    def name(self) -> str:
        return self.kind.value

    @property
    def code(self) -> str:
        return _CONVERSION_CODES[self.kind]


_CONVERSION_CODES = {
    PlistConversionFailureKind.SAME_REPRESENTATION: "plist.conversion.same-representation@1",
    PlistConversionFailureKind.FORMATION: "plist.conversion.formation@1",
    PlistConversionFailureKind.INEXPRESSIBLE: "plist.conversion.inexpressible@1",
    PlistConversionFailureKind.REPARSE: "plist.conversion.reparse@1",
    PlistConversionFailureKind.INTERNAL: "plist.conversion.internal@1",
}
