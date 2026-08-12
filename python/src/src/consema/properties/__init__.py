"""consema.properties - the Java Properties family (Reader/Latin-1), L2
milestone (mirror of the Go G2.3 milestone).

The language-neutral Java Properties family contracts: two exact source
profiles (java-properties.reader@1 character-source semantics and
java-properties.latin1@1 ISO-8859-1 byte semantics), natural/logical-line
formation with Complete/Recovered status and byte-exact spans, exact Java
UTF-16 code-unit semantics for ``\\uXXXX`` escapes, native and lossless
syntax query domains, best-exact EntryMapping and explicit Object
projection with duplicate policy and provenance, canonical Reader/Latin-1
materialization, the five-operation structural edit surface with atomic
commit, dry-run plans, untouched-byte proofs, and SourcePatch derivation.

Authority (language-neutral first; Rust only for byte/registry
arbitration):

- conformance/vectors/java-properties-v1.json (20 cases, suite
  "consema.java-properties.conformance@1") - the machine-readable golden
  surface;
- RFC 0010 (Java Properties profiles v1, docs/rfcs/0010-...), RFC 0004
  (materialization/conversion/structural edit, docs/rfcs/0004-...),
  RFC 0016 sections 5-6 (API shapes and error classification,
  docs/rfcs/0016-...);
- crates/consema-properties/src/*.rs and
  crates/consema-protocol/src/error_registry.rs:1098-1169 for byte/
  registry arbitration only;
- go/properties as a cross-reference only (never a template).

This package is an independent, Python-idiomatic implementation with zero
third-party runtime dependencies. Error text is human presentation only and
never participates in conformance comparison (RFC 0016 section 6). The SDK
never classifies errors; the protocol layer owns classification (RFC 0015
section 5.2, RFC 0016 section 6).
"""

from consema.properties.document import (
    PropertiesComment,
    PropertiesDocument,
    PropertiesEscape,
    PropertiesErrorLine,
    PropertiesLogicalLine,
    PropertiesNaturalLine,
    Property,
)
from consema.properties.edit import (
    EditCommit,
    EditOperation,
    EditOperationKind,
    EditTransaction,
    EditTransactionBuilder,
    commit,
    dry_run,
)
from consema.properties.errors import (
    PropertiesDiagnostic,
    PropertiesEditFailure,
    PropertiesEditFailureKind,
    PropertiesFormationFailure,
    PropertiesFormationFailureKind,
    PropertiesProjectionFailure,
    PropertiesProjectionFailureKind,
    PropertiesSeverity,
    RelatedLocation,
)
from consema.properties.java_string import (
    JavaString,
    JavaStringConversionError,
    JavaStringStatus,
)
from consema.properties.kinds import (
    NATIVE_QUERY_DOMAIN_ID,
    SYNTAX_QUERY_DOMAIN_ID,
    PropertiesEscapeKind,
    PropertiesLogicalLineKind,
    PropertiesProfile,
    PropertiesSyntaxKind,
    PropertiesValueState,
)
from consema.properties.limits import (
    PropertiesEncodingSelection,
    PropertiesEncodingSelectionKind,
    PropertiesParseLimits,
)
from consema.properties.materialization import (
    PropertiesStyle,
    materialize,
    requested_profile,
    requested_style,
)
from consema.properties.operation_registry import (
    FormatOperationDescriptor,
    OperationArgumentDescriptor,
    OperationArgumentKind,
    OperationSupport,
    PropertiesFormatOperationRegistry,
    descriptors,
    format_operation_registry,
)
from consema.properties.parser import (
    is_properties_whitespace,
    parse,
    parse_latin1,
    parse_reader,
)
from consema.properties.projection import (
    AssociationLocation,
    AssociationRole,
    CompleteProjection,
    DuplicatePolicy,
    FailedProjectionAttempt,
    Fidelity,
    ProjectedLocation,
    ProjectedLocationKind,
    ProjectionEvent,
    ProjectionLimits,
    ProjectionReport,
    ProjectionRequest,
    ProjectionResult,
    ProjectionTarget,
    ProvenanceEntry,
    ProvenanceMap,
    ProvenanceRelation,
    SourceOrigin,
    ValuePath,
    ValuePathSegment,
    ValuePathSegmentKind,
    project,
)
from consema.properties.query import (
    PropertiesCancellationToken,
    PropertiesMatch,
    PropertiesMatchKind,
    PropertiesQueryExecution,
    PropertiesQueryLimits,
    PropertiesSyntaxMatch,
    execute_properties_query,
    execute_properties_query_cursor,
    execute_properties_syntax_query,
    execute_properties_syntax_query_cursor,
)

# Frozen profile and domain ids (RFC 0010 section 1/10; lib.rs:44-49;
# query.rs:124-150, 167-211).
READER_PROFILE = "java-properties.reader@1"
LATIN1_PROFILE = "java-properties.latin1@1"
NATIVE_QUERY_DOMAIN = "java-properties.native-semantic-query@1"
SYNTAX_QUERY_DOMAIN = "java-properties.lossless-syntax-query@1"

# Frozen materialization styles (RFC 0010 section 12,
# docs/rfcs/0010-...:357-364; materialization.rs:96-110).
READER_CANONICAL_STYLE = "java-properties.reader-canonical@1"
LATIN1_CANONICAL_STYLE = "java-properties.latin1-canonical@1"

# Frozen projection targets (RFC 0010 section 11,
# docs/rfcs/0010-...:312-314; project_cmd.rs:158).
BEST_EXACT_ENTRY_MAPPING_TARGET = "java-properties.projection.best-exact-entry-mapping@1"
REQUIRE_OBJECT_TARGET = "java-properties.projection.require-object@1"

# Frozen duplicate-key authorizing rules (RFC 0010 section 11,
# docs/rfcs/0010-...:341-344).
DUPLICATE_KEY_FIRST_WINS_RULE = "java-properties.duplicate-key.first-wins@1"
DUPLICATE_KEY_LAST_WINS_JDK_TABLE_RULE = (
    "java-properties.duplicate-key.last-wins-jdk-table@1"
)

__all__ = [
    "AssociationLocation",
    "AssociationRole",
    "BEST_EXACT_ENTRY_MAPPING_TARGET",
    "CompleteProjection",
    "DUPLICATE_KEY_FIRST_WINS_RULE",
    "DUPLICATE_KEY_LAST_WINS_JDK_TABLE_RULE",
    "DuplicatePolicy",
    "EditCommit",
    "EditOperation",
    "EditOperationKind",
    "EditTransaction",
    "EditTransactionBuilder",
    "FailedProjectionAttempt",
    "Fidelity",
    "FormatOperationDescriptor",
    "JavaString",
    "JavaStringConversionError",
    "JavaStringStatus",
    "LATIN1_CANONICAL_STYLE",
    "LATIN1_PROFILE",
    "NATIVE_QUERY_DOMAIN",
    "NATIVE_QUERY_DOMAIN_ID",
    "OperationArgumentDescriptor",
    "OperationArgumentKind",
    "OperationSupport",
    "ProjectedLocation",
    "ProjectedLocationKind",
    "ProjectionEvent",
    "ProjectionLimits",
    "ProjectionReport",
    "ProjectionRequest",
    "ProjectionResult",
    "ProjectionTarget",
    "PropertiesCancellationToken",
    "PropertiesComment",
    "PropertiesDiagnostic",
    "PropertiesDocument",
    "PropertiesEditFailure",
    "PropertiesEditFailureKind",
    "PropertiesEncodingSelection",
    "PropertiesEncodingSelectionKind",
    "PropertiesEscape",
    "PropertiesEscapeKind",
    "PropertiesErrorLine",
    "PropertiesFormationFailure",
    "PropertiesFormationFailureKind",
    "PropertiesFormatOperationRegistry",
    "PropertiesLogicalLine",
    "PropertiesLogicalLineKind",
    "PropertiesMatch",
    "PropertiesMatchKind",
    "PropertiesNaturalLine",
    "PropertiesParseLimits",
    "PropertiesProfile",
    "PropertiesProjectionFailure",
    "PropertiesProjectionFailureKind",
    "PropertiesQueryExecution",
    "PropertiesQueryLimits",
    "PropertiesSeverity",
    "PropertiesStyle",
    "PropertiesSyntaxKind",
    "PropertiesSyntaxMatch",
    "PropertiesValueState",
    "Property",
    "ProvenanceEntry",
    "ProvenanceMap",
    "ProvenanceRelation",
    "READER_CANONICAL_STYLE",
    "READER_PROFILE",
    "REQUIRE_OBJECT_TARGET",
    "RelatedLocation",
    "SYNTAX_QUERY_DOMAIN",
    "SYNTAX_QUERY_DOMAIN_ID",
    "SourceOrigin",
    "ValuePath",
    "ValuePathSegment",
    "ValuePathSegmentKind",
    "commit",
    "descriptors",
    "dry_run",
    "execute_properties_query",
    "execute_properties_query_cursor",
    "execute_properties_syntax_query",
    "execute_properties_syntax_query_cursor",
    "format_operation_registry",
    "is_properties_whitespace",
    "materialize",
    "parse",
    "parse_latin1",
    "parse_reader",
    "project",
    "requested_profile",
    "requested_style",
]
