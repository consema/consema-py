"""consema.json — the JSON family (JSON/JSONC/JSON5), L1 milestone.

The language-neutral JSON family contracts (mirror of the Go G1.2
milestone): frozen profiles (json.strict@1, jsonc.bounded@1,
json5.standard@1), byte-exact formation with Complete/Recovered status,
versioned native and lossless syntax query domains, exact-first core
projection, canonical materialization, dialect conversion via explicit
Projection-to-Materialization composition, scalar and structural edits with
atomic commit, dry-run plans, untouched-byte proofs, and the eight-record
format operation registry.

Authority (language-neutral first; Rust only for byte/registry
arbitration):

- conformance/vectors/json-family-v2.json (33 cases, suite
  "consema.json-family.conformance@2") and v1.json json cases (lines
  41-183) — the machine-readable golden surface;
- RFC 0004 (materialization/conversion/structural edit,
  docs/rfcs/0004-...), RFC 0005 (JSON family production and JSON5 v1,
  docs/rfcs/0005-...), RFC 0016 §5-§6 (API shapes and error
  classification, docs/rfcs/0016-...);
- crates/consema-json/src/*.rs for byte/registry arbitration only;
- go/json as a cross-reference only (never a template).

This package is an independent, Python-idiomatic implementation with zero
third-party runtime dependencies. Error text is human presentation only and
never participates in conformance comparison (RFC 0016 §6). The SDK never
classifies errors; the protocol layer owns classification (RFC 0015 §5.2,
RFC 0016 §6).
"""

from consema.json.document import (
    JsonAccessError,
    JsonAccessErrorKind,
    JsonArrayElement,
    JsonDocument,
    JsonObjectMember,
    JsonValue,
)
from consema.json.edit import (
    EditCommit,
    EditOperation,
    EditOperationKind,
    EditTransaction,
    EditTransactionBuilder,
    RepresentationPolicy,
    ScalarReplacement,
    ScalarReplacementKind,
    commit,
    dry_run,
)
from consema.json.errors import (
    JsonDiagnostic,
    JsonEditFailure,
    JsonEditFailureKind,
    JsonFormationFailure,
    JsonFormationFailureKind,
    JsonProjectionFailure,
    JsonProjectionFailureKind,
    JsonSeverity,
    RelatedLocation,
)
from consema.json.kinds import (
    JsonProfile,
    JsonSyntaxKind,
    JsonValueKind,
    SemanticAvailability,
    SemanticUnavailable,
    is_json5_identifier_continue,
    is_json5_identifier_start,
    is_json5_line_terminator,
    is_json5_whitespace,
)
from consema.json.materialization import (
    JsonStyle,
    canonical_fragment,
    materialization_failure_name,
    materialize,
    requested_profile,
    requested_style,
)
from consema.json.operation_registry import (
    FormatOperationDescriptor,
    JsonFormatOperationRegistry,
    OperationArgumentDescriptor,
    OperationArgumentKind,
    OperationSupport,
    descriptors,
    format_operation_registry,
)
from consema.json.parser import (
    BITS_NAN,
    BITS_NEGATIVE_INFINITY,
    BITS_NEGATIVE_NAN,
    BITS_POSITIVE_INFINITY,
    decode_json5_identifier,
    decode_json_string,
    parse,
)
from consema.json.projection import (
    AssociationLocation,
    AssociationRole,
    CompleteProjection,
    DuplicateKeyPolicy,
    FailedProjectionAttempt,
    Fidelity,
    ProjectedLocation,
    ProjectedLocationKind,
    ProjectionEvent,
    ProjectionEventKind,
    ProjectionLimits,
    ProjectionPolicyScope,
    ProjectionReport,
    ProjectionRequest,
    ProjectionRequestBuilder,
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
from consema.json.query import (
    JsonCancellationToken,
    JsonMatch,
    JsonMatchKind,
    JsonQueryExecution,
    JsonQueryLimits,
    JsonSyntaxMatch,
    execute_json_query,
    execute_json_syntax_query,
)

# Frozen profile and domain ids (RFC 0005 §1/§7; lib.rs:140-146;
# query.rs:97-105).
JSON_STRICT_PROFILE = "json.strict@1"
JSONC_BOUNDED_PROFILE = "jsonc.bounded@1"
JSON5_STANDARD_PROFILE = "json5.standard@1"
NATIVE_QUERY_DOMAIN_V1 = "json.native-semantic-query@1"
NATIVE_QUERY_DOMAIN_V2 = "json.native-semantic-query@2"
LOSSLESS_SYNTAX_QUERY_DOMAIN_V1 = "json.lossless-syntax-query@1"
LOSSLESS_SYNTAX_QUERY_DOMAIN_V2 = "json.lossless-syntax-query@2"

# Frozen materialization styles (RFC 0004 §4; RFC 0005 §9).
JSON_CANONICAL_COMPACT_STYLE = "json.canonical-compact@1"
JSON_CANONICAL_PRETTY_STYLE = "json.canonical-pretty@1"
JSON5_CANONICAL_COMPACT_STYLE = "json5.canonical-compact@1"
JSON5_CANONICAL_PRETTY_STYLE = "json5.canonical-pretty@1"

# Frozen projection targets (RFC 0005 §8; projection.rs:15-24).
JSON_PROJECTION_PROJECT_AS_OBJECT = "json.projection.project-as-object@1"
JSON_PROJECTION_PROJECT_AS_ENTRY_MAPPING = "json.projection.project-as-entry-mapping@1"
JSON_PROJECTION_BEST_EXACT_CORE = "json.projection.best-exact-core@1"
JSON5_PROJECTION_BEST_EXACT_CORE = "json5.projection.best-exact-core@1"

__all__ = [
    "BITS_NAN",
    "BITS_NEGATIVE_INFINITY",
    "BITS_NEGATIVE_NAN",
    "BITS_POSITIVE_INFINITY",
    "CompleteProjection",
    "DuplicateKeyPolicy",
    "EditCommit",
    "EditOperation",
    "EditOperationKind",
    "EditTransaction",
    "EditTransactionBuilder",
    "FailedProjectionAttempt",
    "Fidelity",
    "FormatOperationDescriptor",
    "JsonAccessError",
    "JsonAccessErrorKind",
    "JsonArrayElement",
    "JsonCancellationToken",
    "JsonDiagnostic",
    "JsonDocument",
    "JsonEditFailure",
    "JsonEditFailureKind",
    "JsonFormationFailure",
    "JsonFormationFailureKind",
    "JsonMatch",
    "JsonMatchKind",
    "JsonObjectMember",
    "JsonProfile",
    "JsonProjectionFailure",
    "JsonProjectionFailureKind",
    "JsonQueryExecution",
    "JsonQueryLimits",
    "JsonSeverity",
    "JsonStyle",
    "JsonSyntaxKind",
    "JsonSyntaxMatch",
    "JsonValue",
    "JsonValueKind",
    "JsonFormatOperationRegistry",
    "OperationArgumentDescriptor",
    "OperationArgumentKind",
    "OperationSupport",
    "ProjectedLocation",
    "ProjectedLocationKind",
    "ProjectionEvent",
    "ProjectionEventKind",
    "ProjectionLimits",
    "ProjectionPolicyScope",
    "ProjectionReport",
    "ProjectionRequest",
    "ProjectionRequestBuilder",
    "ProjectionResult",
    "ProjectionTarget",
    "ProvenanceEntry",
    "ProvenanceMap",
    "ProvenanceRelation",
    "RelatedLocation",
    "RepresentationPolicy",
    "ScalarReplacement",
    "ScalarReplacementKind",
    "SemanticAvailability",
    "SemanticUnavailable",
    "SourceOrigin",
    "ValuePath",
    "ValuePathSegment",
    "ValuePathSegmentKind",
    "AssociationLocation",
    "AssociationRole",
    "JsonFormatOperationRegistry",
    "canonical_fragment",
    "commit",
    "decode_json5_identifier",
    "decode_json_string",
    "descriptors",
    "dry_run",
    "execute_json_query",
    "execute_json_syntax_query",
    "format_operation_registry",
    "is_json5_identifier_continue",
    "is_json5_identifier_start",
    "is_json5_line_terminator",
    "is_json5_whitespace",
    "materialization_failure_name",
    "materialize",
    "parse",
    "project",
    "requested_profile",
    "requested_style",
]
