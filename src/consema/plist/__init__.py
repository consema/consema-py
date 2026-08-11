"""consema.plist — the Property List family (two explicit profiles), L3
milestone.

The language-neutral plist family contracts (mirror of the Go G3.2
milestone): frozen profiles (plist.xml@1, plist.binary@1), byte-exact
formation with Complete/Recovered status and exhaustive lossless piece or
binary-region coverage, the three query domains (native semantic, lossless
syntax, binary structure), exact ``plist.value-tree@1`` projection with an
explicit require-object target, canonical materialization per style with
reparse closure, cross-representation conversion with representation-
change reports, the six structural edit operations with atomic commit,
dry-run plans, untouched-byte proofs, and the six-record format operation
registry.

Authority (language-neutral first; Rust only for byte/registry
arbitration):

- conformance/vectors/plist-v1.json (34 cases, suite
  "consema.plist.conformance@1") — the machine-readable golden surface;
- RFC 0013 (plist family profiles v1, docs/rfcs/0013-plist-family-profiles-
  v1.md), RFC 0004 (materialization/conversion/structural edit,
  docs/rfcs/0004-...), RFC 0016 §5-§6 (API shapes and error
  classification, docs/rfcs/0016-...);
- crates/consema-plist/src/*.rs for byte/registry arbitration only;
- go/plist as a cross-reference only (never a template).

This package is an independent, Python-idiomatic implementation with zero
third-party runtime dependencies. Error text is human presentation only and
never participates in conformance comparison (RFC 0016 §6). The SDK never
classifies errors; the protocol layer owns classification (RFC 0015 §5.2,
RFC 0016 §6).
"""

from consema.plist.conversion import (
    ConversionEventKind,
    ConversionReport,
    ConversionReportEvent,
    ConvertedDocument,
)
from consema.plist.document import (
    PlistAccessError,
    PlistAccessErrorKind,
    PlistDocument,
    PlistRepresentation,
    parse,
)
from consema.plist.edit import (
    DictEntryPlacement,
    DictPlacement,
    EditCommit,
    EditOperation,
    EditOperationKind,
    EditPath,
    EditPathStep,
    EditTransaction,
    EditTransactionBuilder,
    EditValue,
    commit,
    dry_run,
)
from consema.plist.errors import (
    PlistConversionFailure,
    PlistConversionFailureKind,
    PlistDiagnostic,
    PlistEditFailure,
    PlistEditFailureKind,
    PlistFormationFailure,
    PlistFormationFailureKind,
    PlistProjectionFailure,
    PlistProjectionFailureKind,
    PlistSeverity,
    RelatedLocation,
)
from consema.plist.kinds import (
    PlistEncodingSelection,
    PlistParseLimits,
    PlistProfile,
    PlistStringStatus,
    PlistSyntaxKind,
    RealWidth,
)
from consema.plist.materialization import (
    PlistMaterializationFailure,
    PlistMaterializationFailureKind,
    materialize,
    requested_profile,
)
from consema.plist.native import (
    PLIST_EPOCH_OFFSET_UNIX,
    PlistArenaError,
    PlistArenaErrorKind,
    PlistArenaLimits,
    PlistArray,
    PlistBoolean,
    PlistData,
    PlistDate,
    PlistDateError,
    PlistDict,
    PlistDictEntry,
    PlistDocument as NativeDocument,
    PlistDocumentBuilder,
    PlistInteger,
    PlistKey,
    PlistReal,
    PlistString,
    PlistStringConversionError,
    PlistUid,
    PlistValue,
    PlistValueKind,
    PlistValueRef,
)
from consema.plist.operation_registry import (
    FormatOperationDescriptor,
    OperationArgumentDescriptor,
    OperationArgumentKind,
    OperationSupport,
    PlistFormatOperationRegistry,
    descriptors,
    format_operation_registry,
)
from consema.plist.parser_binary import (
    BinaryFacts,
    BinaryObjectFact,
    BinaryObjectRefFact,
    BinaryOffsetFact,
    BinaryTrailerFacts,
    PlistFormedBinary,
    parse_binary,
)
from consema.plist.parser_xml import (
    PlistFormedXml,
    parse_xml,
)
from consema.plist.projection import (
    AssociationLocation,
    AssociationRole,
    CollisionPolicy,
    CompleteProjection,
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
    SourceOrigin,
    UidPolicy,
    ValuePath,
    ValuePathSegment,
    ValuePathSegmentKind,
    project,
)
from consema.plist.query import (
    PlistBinaryMatch,
    PlistBinaryMatchKind,
    PlistCancellationToken,
    PlistMatch,
    PlistMatchKind,
    PlistQueryExecution,
    PlistQueryLimits,
    PlistSyntaxMatch,
    execute_plist_binary_query,
    execute_plist_native_query,
    execute_plist_syntax_query,
)

# Frozen profile and domain ids (RFC 0013 §1/§8; lib.rs:83-92).
PLIST_XML_PROFILE = "plist.xml@1"
PLIST_BINARY_PROFILE = "plist.binary@1"
NATIVE_QUERY_DOMAIN_V1 = "plist.native-semantic-query@1"
LOSSLESS_SYNTAX_QUERY_DOMAIN_V1 = "plist.lossless-syntax-query@1"
BINARY_STRUCTURE_QUERY_DOMAIN_V1 = "plist.binary-structure-query@1"

# Frozen materialization styles (RFC 0013 §10, docs/rfcs/0013-...:640-673).
PLIST_XML_CANONICAL_STYLE = "plist.xml-canonical@1"
PLIST_BINARY_CANONICAL_STYLE = "plist.binary-canonical@1"

# Frozen projection targets (RFC 0013 §9, docs/rfcs/0013-...:600-631).
PLIST_PROJECTION_VALUE_TREE = "plist.projection.value-tree@1"
PLIST_PROJECTION_REQUIRE_OBJECT = "plist.projection.require-object@1"

# The value-tree record identity (RFC 0013 §9/§10).
PLIST_VALUE_TREE_RECORD = "plist.value-tree@1"

__all__ = [
    "AssociationLocation",
    "AssociationRole",
    "BINARY_STRUCTURE_QUERY_DOMAIN_V1",
    "BinaryFacts",
    "BinaryObjectFact",
    "BinaryObjectRefFact",
    "BinaryOffsetFact",
    "BinaryTrailerFacts",
    "CollisionPolicy",
    "CompleteProjection",
    "ConversionEventKind",
    "ConversionReport",
    "ConversionReportEvent",
    "ConvertedDocument",
    "DictEntryPlacement",
    "DictPlacement",
    "EditCommit",
    "EditOperation",
    "EditOperationKind",
    "EditPath",
    "EditPathStep",
    "EditTransaction",
    "EditTransactionBuilder",
    "EditValue",
    "FailedProjectionAttempt",
    "Fidelity",
    "FormatOperationDescriptor",
    "LOSSLESS_SYNTAX_QUERY_DOMAIN_V1",
    "NATIVE_QUERY_DOMAIN_V1",
    "NativeDocument",
    "OperationArgumentDescriptor",
    "OperationArgumentKind",
    "OperationSupport",
    "PLIST_BINARY_CANONICAL_STYLE",
    "PLIST_BINARY_PROFILE",
    "PLIST_EPOCH_OFFSET_UNIX",
    "PLIST_PROJECTION_REQUIRE_OBJECT",
    "PLIST_PROJECTION_VALUE_TREE",
    "PLIST_VALUE_TREE_RECORD",
    "PLIST_XML_CANONICAL_STYLE",
    "PLIST_XML_PROFILE",
    "PlistAccessError",
    "PlistAccessErrorKind",
    "PlistArenaError",
    "PlistArenaErrorKind",
    "PlistArenaLimits",
    "PlistArray",
    "PlistBinaryMatch",
    "PlistBinaryMatchKind",
    "PlistBoolean",
    "PlistCancellationToken",
    "PlistConversionFailure",
    "PlistConversionFailureKind",
    "PlistData",
    "PlistDate",
    "PlistDateError",
    "PlistDiagnostic",
    "PlistDict",
    "PlistDictEntry",
    "PlistDocument",
    "PlistDocumentBuilder",
    "PlistEditFailure",
    "PlistEditFailureKind",
    "PlistEncodingSelection",
    "PlistFormationFailure",
    "PlistFormationFailureKind",
    "PlistFormedBinary",
    "PlistFormedXml",
    "PlistFormatOperationRegistry",
    "PlistInteger",
    "PlistKey",
    "PlistMaterializationFailure",
    "PlistMaterializationFailureKind",
    "PlistMatch",
    "PlistMatchKind",
    "PlistParseLimits",
    "PlistProfile",
    "PlistProjectionFailure",
    "PlistProjectionFailureKind",
    "PlistQueryExecution",
    "PlistQueryLimits",
    "PlistReal",
    "PlistRepresentation",
    "PlistSeverity",
    "PlistString",
    "PlistStringConversionError",
    "PlistStringStatus",
    "PlistSyntaxKind",
    "PlistSyntaxMatch",
    "PlistUid",
    "PlistValue",
    "PlistValueKind",
    "PlistValueRef",
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
    "RealWidth",
    "RelatedLocation",
    "SourceOrigin",
    "UidPolicy",
    "ValuePath",
    "ValuePathSegment",
    "ValuePathSegmentKind",
    "commit",
    "descriptors",
    "dry_run",
    "execute_plist_binary_query",
    "execute_plist_native_query",
    "execute_plist_syntax_query",
    "format_operation_registry",
    "materialize",
    "parse",
    "parse_binary",
    "parse_xml",
    "project",
    "requested_profile",
]
