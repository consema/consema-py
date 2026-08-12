"""consema.toml — the frozen ``toml.1.0@1`` language profile (L1, mirror of Go G1.3).

The TOML family forms lossless immutable document snapshots (byte-exact
render, exhaustive token/trivia coverage), exposes the closed TOML native
item model (scalar categories, root/standard/implicit/dotted tables,
inline tables, arrays, and arrays-of-tables with their own identities —
never JSON object/member types, RFC 0001 §1; IMPLEMENTATION.md:102),
executes the ``toml.native-semantic-query@1`` and
``toml.lossless-syntax-query@1`` domains, projects native semantics onto
the ``toml.best-exact-core@1`` target with provenance, materializes
PortableValues into ``toml.canonical-document@1`` style snapshots, and
commits the frozen seven-operation TOML edit surface atomically with
ChangeSet, SourcePatch, and untouched-byte evidence.

Authority (language-neutral first; Rust only for byte/registry
arbitration; go/toml as a cross-reference only, never a template):

- conformance/vectors/toml-v1.json — the machine-readable suite
  "consema.toml.conformance@1" (17 cases; formation, native items, query,
  projection, edit, resource, corpus capabilities);
- RFC 0001 (docs/rfcs/0001-toml-1.0-profile.md) — the language-neutral
  TOML contract; RFC 0004 (materialization/structural edit); RFC 0016
  §5-§6 (API shapes, error classification, frozen spellings);
- crates/consema-toml/src/*.rs — byte/registry arbitration
  (operation_registry.rs:16-74; projection.rs; materialization.rs;
  edit.rs:1280-1332; parser.rs) and crates/consema-protocol/src/
  error_registry.rs:339-361 for the toml-family codes.

This package is an independent, Python-idiomatic implementation with zero
third-party runtime dependencies (pyproject policy: python/ zero-dependency
runtime). Error text is human presentation only and never participates in
conformance comparison (RFC 0016 §6).

Blind-write status: this code was written before the Python toolchain
verification gate (docs/multi-language-implementation-plan.md §3/§7). No
gate is claimed to have passed; the first verification step after the
toolchain lands is the formation/query/projection/edit test suite under
python/tests/toml/.
"""

from consema.toml.document import (
    Document,
    TableFlavor,
    TomlAccessError,
    TomlAccessErrorKind,
    TomlArrayElement,
    TomlDate,
    TomlDateTime,
    TomlEntry,
    TomlItem,
    TomlItemKind,
    TomlOffset,
    TomlProfile,
    TomlTime,
)
from consema.toml.edits import (
    EditCommit,
    EditOperation,
    EditOperationKind,
    EditTransaction,
    EditTransactionBuilder,
    RepresentationPolicy,
    ScalarReplacement,
)
from consema.toml.errors import (
    TomlDiagnostic,
    TomlEditFailure,
    TomlEditFailureKind,
    TomlFormationFailure,
    TomlFormationFailureKind,
    TomlProjectionFailure,
    TomlProjectionFailureKind,
)
from consema.toml.materialization import (
    MaterializationEvent,
    canonical_fragment,
    materialize,
)
from consema.toml.operation_registry import (
    FormatOperationRegistry,
    OperationArgumentDescriptor,
    OperationArgumentKind,
    OperationDescriptor,
    OperationSupport,
    format_operation_registry,
)
from consema.toml.parser import parse, parse_with_profile
from consema.toml.projection import (
    CompleteProjection,
    FailedProjectionAttempt,
    Fidelity,
    ProjectedLocation,
    ProjectedLocationKind,
    ProjectionLimits,
    ProjectionReport,
    ProjectionRequest,
    ProjectionResult,
    ProjectionTarget,
    ProvenanceEntry,
    ProvenanceMap,
    ProvenanceRelation,
    SourceOrigin,
)
from consema.toml.query import (
    CancellationToken,
    QueryLimits,
    TomlMatch,
    TomlMatchKind,
    TomlSyntaxMatch,
    execute_toml_query,
    execute_toml_syntax_query,
)
from consema.toml.syntax import TomlSyntaxKind

__all__ = [
    "CancellationToken",
    "CompleteProjection",
    "Document",
    "EditCommit",
    "EditOperation",
    "EditOperationKind",
    "EditTransaction",
    "EditTransactionBuilder",
    "FailedProjectionAttempt",
    "Fidelity",
    "FormatOperationRegistry",
    "MaterializationEvent",
    "OperationArgumentDescriptor",
    "OperationArgumentKind",
    "OperationDescriptor",
    "OperationSupport",
    "ProjectedLocation",
    "ProjectedLocationKind",
    "ProjectionLimits",
    "ProjectionReport",
    "ProjectionRequest",
    "ProjectionResult",
    "ProjectionTarget",
    "ProvenanceEntry",
    "ProvenanceMap",
    "ProvenanceRelation",
    "QueryLimits",
    "RepresentationPolicy",
    "ScalarReplacement",
    "SourceOrigin",
    "TableFlavor",
    "TomlAccessError",
    "TomlAccessErrorKind",
    "TomlArrayElement",
    "TomlDate",
    "TomlDateTime",
    "TomlDiagnostic",
    "TomlEditFailure",
    "TomlEditFailureKind",
    "TomlEntry",
    "TomlFormationFailure",
    "TomlFormationFailureKind",
    "TomlItem",
    "TomlItemKind",
    "TomlMatch",
    "TomlMatchKind",
    "TomlOffset",
    "TomlProfile",
    "TomlProjectionFailure",
    "TomlProjectionFailureKind",
    "TomlSyntaxKind",
    "TomlSyntaxMatch",
    "TomlTime",
    "canonical_fragment",
    "execute_toml_query",
    "execute_toml_syntax_query",
    "format_operation_registry",
    "materialize",
    "parse",
    "parse_with_profile",
]
