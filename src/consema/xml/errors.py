"""Typed XML family errors with frozen registered codes.

Authority (language-neutral first; Rust only for registry arbitration):

- RFC 0012 §12 (docs/rfcs/0012-xml-1.0-safe-profile-v1.md:422-434): the
  ``xml.*`` diagnostic codes are registered by RFC 0012 itself and do NOT
  enter the consema-protocol core error registry, which covers only
  core/protocol and line-format contract codes (RFC 0011 §10). When XML
  diagnostics are externalized through the protocol they are handled
  through the registry's corresponding format section or per RFC 0011's
  error-code classification rules.
- The exact xml.* code vocabulary transcribes crates/consema-xml/src
  (parser.rs:44,73,106,132-135,217,344,353,371,389,483,514,520,536,556,
  590,596,608,622,659,681,695,700,727,734,765,775,783,789,794,806,817,828,
  858,897,900,902,922,927,932,935,938,973,980,1009,1015,1022,1034,1038,
  1050,1148,1219,1228,1238,1256,1289,1300,1326,1350,1375,1385,1389,1410,
  1446,1478,1497,1509,1511,1527,1547,1592,1612,1638,1753,1754,1779,1796,
  1806,1815,1819,1893,1936,1940,1961,1991,2024,2051,2067,2216,2230,2283,
  2293,2304,2317,2344,2408,2599,2641; projection.rs:461-466; edit.rs:1851;
  materialization.rs:1507) — byte/registry arbitration only.
- The core edit codes consumed by the family are registered at
  crates/consema-protocol/src/error_registry.rs:466-550 (core.edit.*@1,
  introduced by RFC 0004 §17, docs/rfcs/0004-...:387-423); the family edit
  failure vocabulary follows crates/consema-xml/src/edit.rs:319-408.
- The core materialization codes consumed by the family are registered at
  error_registry.rs:556-604 (core.materialization.*@1); the projection
  failure vocabulary follows crates/consema-xml/src/projection.rs:421-469.
- Diagnostic categories follow the frozen semantic categories
  (crates/consema-protocol/src/error_registry.rs:1657-1671; Python
  consema.protocol DiagnosticCategory); severity follows the three frozen
  presentation severities (protocol diagnostic.py).
- RFC 0016 §6 (docs/rfcs/0016-go-api-mapping-v1.md:195-200): SDK operations
  return typed errors whose stable code is always the registered code;
  error text is human presentation only and never participates in
  conformance comparison.

Design note: formation diagnostics are snapshot-bound, so they carry an
``XmlDiagnostic`` record whose primary location is a snapshot-bound
``Span`` (the Rust ``DiagnosticLocation`` with a snapshot identity). The
wire record ``core.diagnostic@1`` instead requires a caller-stable source
id, so the two surfaces stay distinct here.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.document.structural import Span
from consema.protocol.diagnostic import Severity
from consema.protocol.error_registry import DiagnosticCategory


@dataclass(frozen=True, slots=True)
class XmlDiagnostic:
    """One snapshot-bound diagnostic of the XML family.

    Mirrors the frozen code/category/severity/primary/arguments/occurrence
    record shape of ``core.diagnostic@1`` with a snapshot-bound primary
    location (Rust DiagnosticLocation carries a snapshot identity). The
    vector suite references only the code
    (conformance/vectors/xml-1-0-safe-v1.json:111,123,135,147,159,577,591).
    """

    code: str
    category: DiagnosticCategory
    severity: Severity
    primary: Span | None = None
    arguments: dict[str, str] = field(default_factory=dict)
    occurrence: int = 0


class XmlFormationFailure(Exception):
    """Fatal formation failure; no document is ever returned.

    Fatal failures cover invalid byte decoding, impossible source
    coordinates, allocation/host-size overflow, and inability to construct
    exhaustive coverage (RFC 0012 §4, lines 158-163); syntax,
    well-formedness, namespace, safe-DTD, and entity errors form Recovered
    documents instead. Source failures delegate to the wrapped
    ``core.source.*`` code.
    """

    def __init__(self, diagnostics: list[XmlDiagnostic], source=None) -> None:
        self.diagnostics: tuple[XmlDiagnostic, ...] = tuple(diagnostics)
        self.source = source
        super().__init__(self.diagnostics[0].code if self.diagnostics else "formation-failed")

    @classmethod
    def fatal(cls, code: str, category: DiagnosticCategory) -> XmlFormationFailure:
        """One fatal formation failure carrying exactly one diagnostic."""
        return cls(
            [
                XmlDiagnostic(
                    code=code,
                    category=category,
                    severity=Severity.ERROR,
                    primary=None,
                    occurrence=0,
                )
            ]
        )

    @property
    def code(self) -> str:
        """The frozen registered failure code (RFC 0016 §6)."""
        return self.diagnostics[0].code

    def __str__(self) -> str:
        return f"{self.code}: {self.diagnostics[0].arguments}"


class XmlProjectionFailureKind(enum.Enum):
    """Stable XML projection failure (crates/consema-xml/src/projection.rs:
    421-441, transcribed verbatim)."""

    RECOVERED_DOCUMENT = "RecoveredDocument"
    SUBTREE_NOT_ELEMENT = "SubtreeNotElement"
    MAPPING_ADMISSION = "MappingAdmission"
    COLLISION = "Collision"
    RESOURCE_LIMIT = "ResourceLimit"
    CORE_INVARIANT = "CoreInvariant"


_CODE_BY_PROJECTION_KIND = {
    XmlProjectionFailureKind.RECOVERED_DOCUMENT: "xml.projection.recovered-document@1",
    XmlProjectionFailureKind.SUBTREE_NOT_ELEMENT: "xml.projection.subtree@1",
    XmlProjectionFailureKind.MAPPING_ADMISSION: "xml.projection.admission@1",
    XmlProjectionFailureKind.COLLISION: "xml.projection.collision@1",
    XmlProjectionFailureKind.RESOURCE_LIMIT: "xml.projection.resource-limit@1",
    XmlProjectionFailureKind.CORE_INVARIANT: "xml.projection.core-invariant@1",
}


class XmlProjectionFailure(Exception):
    """Stable XML projection failure with a frozen code
    (projection.rs:443-469)."""

    def __init__(
        self,
        kind: XmlProjectionFailureKind,
        *,
        reason: str | None = None,
        limit_name: str | None = None,
        span: Span | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.reason = reason
        self.limit_name = limit_name
        self.span = span

    @property
    def code(self) -> str:
        return _CODE_BY_PROJECTION_KIND[self.kind]

    def to_diagnostic(self) -> XmlDiagnostic:
        """One snapshot-bound failure diagnostic (projection.rs:459-468)."""
        category = (
            DiagnosticCategory.PROJECTION
            if self.kind is not XmlProjectionFailureKind.RESOURCE_LIMIT
            else DiagnosticCategory.RESOURCE
        )
        arguments: dict[str, str] = {}
        if self.kind is XmlProjectionFailureKind.RESOURCE_LIMIT:
            arguments["limit"] = self.limit_name or ""
        return XmlDiagnostic(
            code=self.code,
            category=category,
            severity=Severity.ERROR,
            primary=self.span,
            arguments=arguments,
            occurrence=0,
        )

    def __str__(self) -> str:
        return f"{self.code}: {self.kind.value}"


class XmlEditFailureKind(enum.Enum):
    """Stable XML edit validation or commit failure
    (crates/consema-xml/src/edit.rs:319-360, transcribed verbatim)."""

    WRONG_SNAPSHOT = "WrongSnapshot"
    WRONG_ROLE = "WrongRole"
    TARGET_NOT_FOUND = "TargetNotFound"
    INCOMPLETE_TARGET = "IncompleteTarget"
    INVALID_QNAME = "InvalidQName"
    UNBOUND_PREFIX = "UnboundPrefix"
    RESERVED_PREFIX = "ReservedPrefix"
    DUPLICATE_EXPANDED_ATTRIBUTE = "DuplicateExpandedAttribute"
    CANNOT_REMOVE_ROOT = "CannotRemoveRoot"
    ANCESTOR_PLACEMENT = "AncestorPlacement"
    CONFLICTING_EDITS = "ConflictingEdits"
    OVERLAPPING_OWNERSHIP = "OverlappingOwnership"
    ANCESTOR_DESCENDANT_CONFLICT = "AncestorDescendantConflict"
    PLACEMENT_ANCHOR_MODIFIED = "PlacementAnchorModified"
    RESOURCE_LIMIT = "ResourceLimit"
    NEW_DOCUMENT_FORMATION_FAILED = "NewDocumentFormationFailed"


_CODE_BY_EDIT_KIND = {
    XmlEditFailureKind.WRONG_SNAPSHOT: "core.edit.wrong-snapshot@1",
    XmlEditFailureKind.WRONG_ROLE: "core.edit.wrong-role@1",
    XmlEditFailureKind.TARGET_NOT_FOUND: "core.edit.target-not-found@1",
    XmlEditFailureKind.INCOMPLETE_TARGET: "core.edit.incomplete-target@1",
    XmlEditFailureKind.INVALID_QNAME: "core.edit.invalid-qname@1",
    XmlEditFailureKind.UNBOUND_PREFIX: "core.edit.unbound-prefix@1",
    XmlEditFailureKind.RESERVED_PREFIX: "core.edit.reserved-prefix@1",
    XmlEditFailureKind.DUPLICATE_EXPANDED_ATTRIBUTE: "core.edit.duplicate-expanded-attribute@1",
    XmlEditFailureKind.CANNOT_REMOVE_ROOT: "core.edit.cannot-remove-root@1",
    XmlEditFailureKind.ANCESTOR_PLACEMENT: "core.edit.ancestor-placement@1",
    XmlEditFailureKind.CONFLICTING_EDITS: "core.edit.conflicting-edits@1",
    XmlEditFailureKind.OVERLAPPING_OWNERSHIP: "core.edit.conflicting-edits@1",
    XmlEditFailureKind.ANCESTOR_DESCENDANT_CONFLICT: "core.edit.conflicting-edits@1",
    XmlEditFailureKind.PLACEMENT_ANCHOR_MODIFIED: "core.edit.conflicting-edits@1",
    XmlEditFailureKind.RESOURCE_LIMIT: "core.edit.resource-limit@1",
    XmlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED: "core.edit.formation-failed@1",
}


class XmlEditFailure(Exception):
    """Stable XML edit validation or commit failure with a frozen registered
    code (RFC 0004 §13; the code mapping is the Rust StableFailure impl,
    edit.rs:388-407; registry lines in the module docstring). The failure
    kind spellings are the exact Rust variant names (RFC 0016 §5.3/§8: one
    vocabulary per code)."""

    def __init__(
        self,
        kind: XmlEditFailureKind,
        *,
        prefix: str | None = None,
        limit_name: str | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.prefix = prefix
        self.limit_name = limit_name

    @property
    def code(self) -> str:
        return _CODE_BY_EDIT_KIND[self.kind]

    def __str__(self) -> str:
        return f"{self.code}: {self.kind.value}"
