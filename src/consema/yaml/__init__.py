"""consema.yaml - the YAML family (YAML 1.2 Core / 1.1 compat), L2 milestone.

The language-neutral YAML family contracts (mirror of the Go G2.1
milestone): frozen profiles (yaml.1.2-core@1, yaml.1.1-compat@1),
byte-exact formation with Complete status, the lossless syntax index with
the closed YamlSyntaxKind classification, versioned native and lossless
syntax query domains, exact-first graph and value projection with
provenance, canonical block/flow materialization, and snapshot-bound
structural edits with atomic commit, dry-run plans, untouched-byte proofs,
and the eight-record format operation registry.

Authority (language-neutral first; Rust only for byte/registry
arbitration):

- conformance/vectors/yaml-v1.json (25 cases, suite
  "consema.yaml.conformance@1") - the machine-readable golden surface;
- RFC 0007 (docs/rfcs/0007-yaml-family-profiles-and-safety-v1.md) - the
  YAML family contract; RFC 0004 (materialization/conversion/structural
  edit) and RFC 0006 (PortableGraph/PGCE) for the shared contracts;
- crates/consema-yaml/src/*.rs for byte/registry arbitration only;
- go/yaml as a cross-reference only (never a template).

This package is an independent, Python-idiomatic implementation with zero
third-party runtime dependencies. Error text is human presentation only and
never participates in conformance comparison (RFC 0016 s6). The SDK never
classifies errors; the protocol layer owns classification (RFC 0015 s5.2,
RFC 0016 s6).
"""

from consema.yaml.document import (
    Document,
    YamlAlias,
    YamlDocument,
    YamlMappingEntry,
    YamlNode,
    YamlScalar,
    YamlSequenceItem,
)
from consema.yaml.edit import (
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
from consema.yaml.errors import (
    YamlDiagnostic,
    YamlEditFailure,
    YamlEditFailureKind,
    YamlFormationFailure,
    YamlFormationFailureKind,
    YamlGraphMaterializationFailure,
    YamlGraphMaterializationFailureKind,
    YamlGraphProjectionError,
    YamlGraphProjectionErrorKind,
    YamlProjectionFailure,
    YamlProjectionFailureKind,
    YamlSeverity,
)
from consema.yaml.kinds import (
    YamlNodeKind,
    YamlProfile,
    YamlScalarKind,
    YamlScalarStyle,
    YamlSyntaxKind,
)
from consema.yaml.materialization import (
    CompleteGraphMaterialization,
    FailedGraphMaterializationAttempt,
    YAML_CANONICAL_BLOCK_STYLE,
    YAML_CANONICAL_FLOW_STYLE,
    YamlStyle,
    materialize_graph,
    materialize_value,
    requested_profile,
    requested_style,
)
from consema.yaml.operation_registry import (
    FormatOperationDescriptor,
    OperationArgumentDescriptor,
    OperationArgumentKind,
    OperationSupport,
    YamlFormatOperationRegistry,
    descriptors,
    format_operation_registry,
)
from consema.yaml.parser import parse
from consema.yaml.projection import (
    CompleteGraphProjection,
    CompleteValueProjection,
    FailedValueProjection,
    Fidelity,
    GraphProjectedLocation,
    GraphProjectionLimits,
    GraphProjectionRequest,
    GraphProvenanceEntry,
    GraphProvenanceMap,
    MappingPolicy,
    ProjectedLocation,
    ProjectedLocationKind,
    ProjectionEvent,
    ProjectionEventKind,
    ProjectionReport,
    ProvenanceEntry,
    ProvenanceMap,
    ProvenanceRelation,
    SharingPolicy,
    SourceOrigin,
    TagPolicy,
    ValueProjectionLimits,
    ValueProjectionRequest,
    project_graph,
    project_graph_with_provenance,
    project_value,
)
from consema.yaml.query import (
    YamlCancellationToken,
    YamlMatch,
    YamlMatchKind,
    YamlQueryExecution,
    YamlQueryLimits,
    YamlSyntaxMatch,
    execute_yaml_query,
    execute_yaml_syntax_query,
)

# Frozen profile and domain ids (RFC 0007 s1/s9; lib.rs:241-257;
# query.rs:167-177, 213-223).
YAML12_CORE_PROFILE = "yaml.1.2-core@1"
YAML11_COMPAT_PROFILE = "yaml.1.1-compat@1"
NATIVE_QUERY_DOMAIN_V1 = "yaml.native-semantic-query@1"
LOSSLESS_SYNTAX_QUERY_DOMAIN_V1 = "yaml.lossless-syntax-query@1"

# Frozen projection targets (RFC 0007 s10).
YAML_PROJECTION_BEST_EXACT_GRAPH = "yaml.projection.best-exact-graph@1"
YAML_PROJECTION_BEST_EXACT_VALUE = "yaml.projection.best-exact-value@1"

__all__ = [
    "CompleteGraphMaterialization",
    "CompleteGraphProjection",
    "CompleteValueProjection",
    "Document",
    "EditCommit",
    "EditOperation",
    "EditOperationKind",
    "EditTransaction",
    "EditTransactionBuilder",
    "FailedGraphMaterializationAttempt",
    "FailedValueProjection",
    "Fidelity",
    "FormatOperationDescriptor",
    "GraphProjectedLocation",
    "GraphProjectionLimits",
    "GraphProjectionRequest",
    "GraphProvenanceEntry",
    "GraphProvenanceMap",
    "LOSSLESS_SYNTAX_QUERY_DOMAIN_V1",
    "MappingPolicy",
    "NATIVE_QUERY_DOMAIN_V1",
    "OperationArgumentDescriptor",
    "OperationArgumentKind",
    "OperationSupport",
    "ProjectedLocation",
    "ProjectedLocationKind",
    "ProjectionEvent",
    "ProjectionEventKind",
    "ProjectionReport",
    "ProvenanceEntry",
    "ProvenanceMap",
    "ProvenanceRelation",
    "RepresentationPolicy",
    "ScalarReplacement",
    "ScalarReplacementKind",
    "SharingPolicy",
    "SourceOrigin",
    "TagPolicy",
    "ValueProjectionLimits",
    "ValueProjectionRequest",
    "YAML11_COMPAT_PROFILE",
    "YAML12_CORE_PROFILE",
    "YAML_CANONICAL_BLOCK_STYLE",
    "YAML_CANONICAL_FLOW_STYLE",
    "YAML_PROJECTION_BEST_EXACT_GRAPH",
    "YAML_PROJECTION_BEST_EXACT_VALUE",
    "YamlAlias",
    "YamlCancellationToken",
    "YamlDiagnostic",
    "YamlDocument",
    "YamlEditFailure",
    "YamlEditFailureKind",
    "YamlFormationFailure",
    "YamlFormationFailureKind",
    "YamlGraphMaterializationFailure",
    "YamlGraphMaterializationFailureKind",
    "YamlGraphProjectionError",
    "YamlGraphProjectionErrorKind",
    "YamlMappingEntry",
    "YamlMatch",
    "YamlMatchKind",
    "YamlNode",
    "YamlNodeKind",
    "YamlProfile",
    "YamlProjectionFailure",
    "YamlProjectionFailureKind",
    "YamlQueryExecution",
    "YamlQueryLimits",
    "YamlScalar",
    "YamlScalarKind",
    "YamlScalarStyle",
    "YamlSequenceItem",
    "YamlSeverity",
    "YamlStyle",
    "YamlSyntaxKind",
    "YamlSyntaxMatch",
    "YamlFormatOperationRegistry",
    "commit",
    "descriptors",
    "dry_run",
    "execute_yaml_query",
    "execute_yaml_syntax_query",
    "format_operation_registry",
    "materialize_graph",
    "materialize_value",
    "parse",
    "project_graph",
    "project_graph_with_provenance",
    "project_value",
    "requested_profile",
    "requested_style",
]
