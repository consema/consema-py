"""consema.xml — the frozen ``xml.1.0-safe@1`` language profile (L3, mirror
of Go G3.1).

The XML family forms lossless immutable document snapshots (byte-exact
render, exhaustive raw-byte syntax coverage with the v1 kind vocabulary,
RFC 0012 §7), resolves namespaces to expanded names with immutable
ancestry-derived scope (RFC 0012 §5), expands only the five predefined
entities and bounded internal general text entities — never external
entities, parameter entities, or entity-generated markup (RFC 0012 §3,
deny-by-default, no external entity expansion) — and forms Complete or
deterministically Recovered documents with ordered diagnostics (RFC 0012
§4). It executes the ``xml.native-semantic-query@1`` and
``xml.lossless-syntax-query@1`` domains (RFC 0012 §8), projects native
semantics onto the exact ``xml.element-tree@1`` record (the XML domain
record — an element tree, not a PortableValue tree; RFC 0012 §9) with
provenance, materializes PortableValues into ``xml.safe-canonical-document@1``
style snapshots with reparse closure (RFC 0012 §10, RFC 0004), and commits
the frozen eight-operation XML edit surface atomically with ChangeSet,
SourcePatch, and untouched-byte evidence (RFC 0012 §11, RFC 0004 §13-§16).

Authority (language-neutral first; Rust only for byte/registry
arbitration; go/xml as a cross-reference only, never a template):

- conformance/vectors/xml-1-0-safe-v1.json — the machine-readable suite
  "consema.xml-1-0-safe.conformance@1" (35 cases; formation, syntax-query,
  native-query, projection, materialization, edit, limit capabilities);
- RFC 0012 (docs/rfcs/0012-xml-1.0-safe-profile-v1.md) — the
  language-neutral XML 1.0 safe Profile contract; RFC 0004
  (materialization/structural edit/provenance); RFC 0016 §5-§6 (API
  shapes, error classification, frozen spellings);
- crates/consema-xml/src/*.rs — byte/registry arbitration
  (operation_registry.rs:16-89; parser.rs; document.rs; query.rs;
  projection.rs; materialization.rs; edit.rs:1372-1383) and
  crates/consema-protocol/src/error_registry.rs:466-550/556-604 for the
  core.edit.*@1 and core.materialization.*@1 codes consumed by this
  family. The xml.* diagnostic codes are registered by RFC 0012 §12
  (lines 428-434) and do not enter the consema-protocol core registry.

This package is an independent, Python-idiomatic implementation with zero
third-party runtime dependencies (python/ zero-dependency runtime policy).
Error text is human presentation only and never participates in
conformance comparison (RFC 0016 §6).

Blind-write status: this code was written before the Python toolchain
verification gate (docs/multi-language-implementation-plan.md §3/§7). No
gate is claimed to have passed; the first verification step after the
toolchain lands is the formation/query/projection/materialization/edit
test suite under python/tests/xml/.
"""

from consema.xml.document import (
    Document,
    EntityDeclarationData,
    QNameFacts,
    ReferenceFragment,
    ReferenceFragmentKind,
    XmlAttributeData,
    XmlCdataData,
    XmlCommentData,
    XmlContent,
    XmlContentKind,
    XmlDeclarationData,
    XmlDoctypeData,
    XmlElement,
    XmlElementData,
    XmlErrorRegionData,
    XmlNamespaceBindingData,
    XmlPiData,
    XmlProfile,
    XmlPrologItem,
    XmlPrologItemKind,
    XmlTextData,
    text_semantic,
)
from consema.xml.edit import (
    AttributePlacement,
    ContentPlacement,
    EditCommit,
    EditOperation,
    EditOperationKind,
    EditTransaction,
    EditTransactionBuilder,
    NameFacts,
    PlacementKind,
)
from consema.xml.entities import (
    EntityExpansionLimits,
    EntityExpansionState,
    ExpansionBreach,
    ExpansionBreachKind,
    PREDEFINED_ENTITIES,
    ReplacementError,
    ReplacementErrorKind,
    is_xml_char,
    predefined_value,
    validate_replacement_text,
)
from consema.xml.errors import (
    XmlDiagnostic,
    XmlEditFailure,
    XmlEditFailureKind,
    XmlFormationFailure,
    XmlProjectionFailure,
    XmlProjectionFailureKind,
)
from consema.xml.kinds import XmlSyntaxKind
from consema.xml.materialization import materialize
from consema.xml.namespaces import (
    Binding,
    ExpandedName,
    NamespaceError,
    NamespaceErrorKind,
    NamespaceScope,
    QName,
    XML_NAMESPACE_URI,
    XMLNS_NAMESPACE_URI,
)
from consema.xml.operation_registry import (
    FormatOperationRegistry,
    OperationArgumentDescriptor,
    OperationArgumentKind,
    OperationDescriptor,
    OperationSupport,
    format_operation_registry,
)
from consema.xml.parser import (
    XmlEncodingSelection,
    XmlEncodingSelectionKind,
    XmlParseLimits,
    parse,
    parse_with_profile,
)
from consema.xml.paths import (
    AssociationLocation,
    AssociationRole,
    ValuePath,
    ValuePathSegment,
    ValuePathSegmentKind,
)
from consema.xml.projection import (
    AttributePolicy,
    CollisionPolicy,
    CompleteProjection,
    ExpandedNameKeyPolicy,
    FailedProjectionAttempt,
    Fidelity,
    ProjectedLocation,
    ProjectedLocationKind,
    ProjectionEvent,
    ProjectionEventKind,
    ProjectionLimits,
    ProjectionReport,
    ProjectionRequest,
    ProjectionResult,
    ProjectionTarget,
    ProvenanceEntry,
    ProvenanceMap,
    ProvenanceRelation,
    RepeatedChildPolicy,
    SourceOrigin,
    TextContentInclude,
    TextKeyPolicy,
    project_document,
)
from consema.xml.query import (
    CancellationToken,
    QueryLimits,
    XmlMatch,
    XmlMatchKind,
    XmlReferenceKind,
    XmlSyntaxMatch,
    execute_xml_query,
    execute_xml_syntax_query,
)

__all__ = [
    "AttributePlacement",
    "AttributePolicy",
    "Binding",
    "CancellationToken",
    "CollisionPolicy",
    "CompleteProjection",
    "ContentPlacement",
    "Document",
    "EditCommit",
    "EditOperation",
    "EditOperationKind",
    "EditTransaction",
    "EditTransactionBuilder",
    "EntityDeclarationData",
    "EntityExpansionLimits",
    "EntityExpansionState",
    "ExpandedName",
    "ExpandedNameKeyPolicy",
    "ExpansionBreach",
    "ExpansionBreachKind",
    "FailedProjectionAttempt",
    "Fidelity",
    "FormatOperationRegistry",
    "NameFacts",
    "NamespaceError",
    "NamespaceErrorKind",
    "NamespaceScope",
    "OperationArgumentDescriptor",
    "OperationArgumentKind",
    "OperationDescriptor",
    "OperationSupport",
    "PREDEFINED_ENTITIES",
    "PlacementKind",
    "ProjectedLocation",
    "ProjectedLocationKind",
    "ProjectionEvent",
    "ProjectionEventKind",
    "ProjectionLimits",
    "ProjectionReport",
    "ProjectionRequest",
    "ProjectionResult",
    "ProjectionTarget",
    "ProvenanceEntry",
    "ProvenanceMap",
    "ProvenanceRelation",
    "QName",
    "QNameFacts",
    "QueryLimits",
    "ReferenceFragment",
    "ReferenceFragmentKind",
    "ReplacementError",
    "ReplacementErrorKind",
    "RepeatedChildPolicy",
    "SourceOrigin",
    "TextContentInclude",
    "TextKeyPolicy",
    "ValuePath",
    "ValuePathSegment",
    "ValuePathSegmentKind",
    "AssociationLocation",
    "AssociationRole",
    "XML_NAMESPACE_URI",
    "XMLNS_NAMESPACE_URI",
    "XmlAttributeData",
    "XmlCdataData",
    "XmlCommentData",
    "XmlContent",
    "XmlContentKind",
    "XmlDeclarationData",
    "XmlDiagnostic",
    "XmlDoctypeData",
    "XmlEditFailure",
    "XmlEditFailureKind",
    "XmlElement",
    "XmlElementData",
    "XmlEncodingSelection",
    "XmlEncodingSelectionKind",
    "XmlErrorRegionData",
    "XmlFormationFailure",
    "XmlMatch",
    "XmlMatchKind",
    "XmlNamespaceBindingData",
    "XmlParseLimits",
    "XmlPiData",
    "XmlProfile",
    "XmlProjectionFailure",
    "XmlProjectionFailureKind",
    "XmlPrologItem",
    "XmlPrologItemKind",
    "XmlReferenceKind",
    "XmlSyntaxKind",
    "XmlSyntaxMatch",
    "XmlTextData",
    "execute_xml_query",
    "execute_xml_syntax_query",
    "format_operation_registry",
    "is_xml_char",
    "materialize",
    "parse",
    "parse_with_profile",
    "predefined_value",
    "project_document",
    "text_semantic",
    "validate_replacement_text",
]
