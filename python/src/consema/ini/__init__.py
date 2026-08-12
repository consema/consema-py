"""consema.ini — the INI family (three explicit profiles), L2 milestone.

The language-neutral INI family contracts (mirror of the Go G2.2
milestone): frozen profiles (ini.portable@1, ini.windows@1,
ini.python-configparser@1), byte-exact formation with Complete/Recovered
status and Windows code-page handling, the native and lossless syntax
query domains, exact EntryMapping projection with explicit Object
collapse, canonical materialization per profile, value and structural
edits with atomic commit, dry-run plans, untouched-byte proofs, and the
eight-record format operation registry.

Authority (language-neutral first; Rust only for byte/registry
arbitration):

- conformance/vectors/ini-v1.json (19 cases, suite
  "consema.ini.conformance@1") — the machine-readable golden surface;
- RFC 0009 (INI family profiles v1, docs/rfcs/0009-ini-family-profiles-
  v1.md), RFC 0004 (materialization/conversion/structural edit,
  docs/rfcs/0004-...), RFC 0016 §5-§6 (API shapes and error
  classification, docs/rfcs/0016-...);
- crates/consema-ini/src/*.rs for byte/registry arbitration only;
- go/ini as a cross-reference only (never a template).

This package is an independent, Python-idiomatic implementation with zero
third-party runtime dependencies. Error text is human presentation only and
never participates in conformance comparison (RFC 0016 §6). The SDK never
classifies errors; the protocol layer owns classification (RFC 0015 §5.2,
RFC 0016 §6).
"""

from consema.ini.document import (
    IniAccessError,
    IniAccessErrorKind,
    IniDocument,
)
from consema.ini.edit import (
    EditCommit,
    EditOperation,
    EditOperationKind,
    EditTransaction,
    EditTransactionBuilder,
    RepresentationPolicy,
    ValueReplacement,
    ValueReplacementKind,
    commit,
    dry_run,
)
from consema.ini.errors import (
    IniDiagnostic,
    IniEditFailure,
    IniEditFailureKind,
    IniFormationFailure,
    IniFormationFailureKind,
    IniProjectionFailure,
    IniProjectionFailureKind,
    IniSeverity,
    RelatedLocation,
)
from consema.ini.kinds import (
    IniEncodingSelection,
    IniLogicalLineKind,
    IniParseLimits,
    IniProfile,
    IniQuoteStyle,
    IniSyntaxKind,
    IniValueState,
    is_horizontal,
    is_portable_name,
    is_portable_value,
    is_windows_name,
    windows_value_needs_quotes,
)
from consema.ini.materialization import (
    materialize,
    requested_profile,
)
from consema.ini.operation_registry import (
    FormatOperationDescriptor,
    IniFormatOperationRegistry,
    OperationArgumentDescriptor,
    OperationArgumentKind,
    OperationSupport,
    descriptors,
    format_operation_registry,
)
from consema.ini.parser import (
    IniEntry,
    IniErrorLine,
    IniLogicalLine,
    IniPhysicalLine,
    IniSection,
    parse,
)
from consema.ini.projection import (
    CollisionPolicy,
    CompleteProjection,
    FailedProjectionAttempt,
    Fidelity,
    NameComparison,
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
    ValuePath,
    ValuePathSegment,
    ValuePathSegmentKind,
    AssociationLocation,
    AssociationRole,
    project,
)
from consema.ini.query import (
    IniCancellationToken,
    IniMatch,
    IniMatchKind,
    IniQueryExecution,
    IniQueryLimits,
    IniSyntaxMatch,
    execute_ini_query,
    execute_ini_syntax_query,
)

# Frozen profile and domain ids (RFC 0009 §1/§9; lib.rs:49-55).
INI_PORTABLE_PROFILE = "ini.portable@1"
INI_WINDOWS_PROFILE = "ini.windows@1"
INI_PYTHON_CONFIGPARSER_PROFILE = "ini.python-configparser@1"
NATIVE_QUERY_DOMAIN_V1 = "ini.native-semantic-query@1"
LOSSLESS_SYNTAX_QUERY_DOMAIN_V1 = "ini.lossless-syntax-query@1"

# Frozen materialization styles (RFC 0009 §11, docs/rfcs/0009-...:393-399).
INI_PORTABLE_CANONICAL_STYLE = "ini.portable-canonical@1"
INI_WINDOWS_CANONICAL_STYLE = "ini.windows-canonical@1"
INI_PYTHON_CONFIGPARSER_CANONICAL_STYLE = "ini.python-configparser-canonical@1"

# Frozen projection targets (RFC 0009 §10, docs/rfcs/0009-...:375-376).
INI_PROJECTION_BEST_EXACT_ENTRY_MAPPING = "ini.projection.best-exact-entry-mapping@1"
INI_PROJECTION_REQUIRE_OBJECT = "ini.projection.require-object@1"

__all__ = [
    "AssociationLocation",
    "AssociationRole",
    "CollisionPolicy",
    "CompleteProjection",
    "EditCommit",
    "EditOperation",
    "EditOperationKind",
    "EditTransaction",
    "EditTransactionBuilder",
    "FailedProjectionAttempt",
    "Fidelity",
    "FormatOperationDescriptor",
    "INI_PORTABLE_CANONICAL_STYLE",
    "INI_PORTABLE_PROFILE",
    "INI_PROJECTION_BEST_EXACT_ENTRY_MAPPING",
    "INI_PROJECTION_REQUIRE_OBJECT",
    "INI_PYTHON_CONFIGPARSER_CANONICAL_STYLE",
    "INI_PYTHON_CONFIGPARSER_PROFILE",
    "INI_WINDOWS_CANONICAL_STYLE",
    "INI_WINDOWS_PROFILE",
    "IniAccessError",
    "IniAccessErrorKind",
    "IniCancellationToken",
    "IniDiagnostic",
    "IniDocument",
    "IniEditFailure",
    "IniEditFailureKind",
    "IniEncodingSelection",
    "IniEntry",
    "IniErrorLine",
    "IniFormationFailure",
    "IniFormationFailureKind",
    "IniFormatOperationRegistry",
    "IniLogicalLine",
    "IniLogicalLineKind",
    "IniMatch",
    "IniMatchKind",
    "IniParseLimits",
    "IniPhysicalLine",
    "IniProfile",
    "IniProjectionFailure",
    "IniProjectionFailureKind",
    "IniQueryExecution",
    "IniQueryLimits",
    "IniQuoteStyle",
    "IniSection",
    "IniSeverity",
    "IniSyntaxKind",
    "IniSyntaxMatch",
    "IniValueState",
    "LOSSLESS_SYNTAX_QUERY_DOMAIN_V1",
    "NATIVE_QUERY_DOMAIN_V1",
    "NameComparison",
    "OperationArgumentDescriptor",
    "OperationArgumentKind",
    "OperationSupport",
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
    "RelatedLocation",
    "RepresentationPolicy",
    "SourceOrigin",
    "ValuePath",
    "ValuePathSegment",
    "ValuePathSegmentKind",
    "ValueReplacement",
    "ValueReplacementKind",
    "commit",
    "descriptors",
    "dry_run",
    "execute_ini_query",
    "execute_ini_syntax_query",
    "format_operation_registry",
    "is_horizontal",
    "is_portable_name",
    "is_portable_value",
    "is_windows_name",
    "materialize",
    "parse",
    "project",
    "requested_profile",
    "windows_value_needs_quotes",
]
