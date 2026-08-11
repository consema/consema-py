"""consema.hcl — the HCL family (native/tfvars), L3 milestone (mirror of
Go G4.1).

The two frozen HCL profiles (RFC 0014 §1): `hcl.native@1` and
`hcl.tfvars@1` share one syntax system — the HCL Native Syntax as frozen by
HashiCorp's hclsyntax/spec.md — and one native semantic model
(body/attribute/block/label/expression/template facts, RFC 0014 §6).
`hcl.tfvars@1` is `hcl.native@1` under one structural restriction: the top
level of a tfvars document admits attributes only, never blocks (RFC 0014
§5).

Both profiles are formation-only documents: Consema parses, preserves, and
queries HCL syntax and structure but never evaluates it. Variables,
function calls, template interpolation, template directives, and
for-expressions are native content with exact source identity; no
evaluator exists anywhere in parse, query, projection, materialization, or
edit (RFC 0014 §1, hard gate 1; SECURITY.md:36). `hcl.expression@1`
carries only syntax facts — the kind family spelling, the exact source
text, and the structural fingerprint.

The module surface covers: byte-exact formation with Complete/Recovered
status (F10), the native and lossless syntax query domains (RFC 0014 §7),
the `hcl.projection.body@1` record projection with the explicit
`ProjectExpression` policy (RFC 0014 §8), `hcl.canonical-document@1`
materialization with reparse closure (RFC 0014 §9), the six structural
edit operations with atomic commit, dry-run plans, untouched-byte proofs,
and SourcePatch derivation (RFC 0014 §10), and the per-profile format
operation registry (RFC 0014 §10).

Authority (language-neutral first; Rust only for byte/registry
arbitration):

- conformance/vectors/hcl-v1.json (45 cases, suite
  "consema.hcl.conformance@1") — the machine-readable golden surface;
- RFC 0014 (HCL family profiles, docs/rfcs/0014-hcl-family-profiles-v1.md)
  and RFC 0004 (materialization/projection/edit algebra,
  docs/rfcs/0004-...);
- crates/consema-hcl/src/*.rs for byte/registry arbitration only;
- go/hcl as a cross-reference only (never a template).

This package is an independent, Python-idiomatic implementation with zero
third-party runtime dependencies. Error text is human presentation only and
never participates in conformance comparison (RFC 0016 §6).
"""

from consema.hcl.document import (
    HclDocument,
    HclEncodingSelection,
    parse,
)
from consema.hcl.edit import (
    BodyPath,
    BodyPathStep,
    BodyPlacement,
    EditCommit,
    EditKey,
    EditOperation,
    EditOperationKind,
    EditTransaction,
    EditTransactionBuilder,
    EditValue,
    NodeRef,
    commit,
    dry_run,
    operation_metadata,
)
from consema.hcl.errors import (
    HclDiagnostic,
    HclEditFailure,
    HclEditFailureKind,
    HclFormationFailure,
    HclFormationFailureKind,
    HclMaterializationFailure,
    HclMaterializationFailureKind,
    HclProjectionFailure,
    HclProjectionFailureKind,
    HclSeverity,
    RelatedLocation,
)
from consema.hcl.expression import (
    BinaryOp,
    HclCallArg,
    HclDirectiveKind,
    HclExpression,
    HclExpressionKind,
    HclForIntro,
    HclLiteralKey,
    HclLiteralObjectEntry,
    HclLiteralValue,
    HclNumber,
    HclObjectEntry,
    HclObjectKey,
    HclTemplateKey,
    HclTemplatePart,
    HclTraversalRoot,
    HclTraversalStep,
    HeredocFacts,
    HeredocMode,
    ObjectSeparator,
    UnaryOp,
    canonical_decimal,
    is_literal_complete,
    literal_value,
    structural_fingerprint,
    structural_fingerprint_hex,
)
from consema.hcl.kinds import (
    HclExpressionKindName,
    HclProfile,
    HclSyntaxKind,
    is_identifier_continue,
    is_identifier_start,
)
from consema.hcl.limits import HclParseLimits
from consema.hcl.materialization import (
    escape_text,
    materialize,
    render_decimal,
)
from consema.hcl.native import (
    HclAttribute,
    HclBlock,
    HclBlockLabel,
    HclBody,
    HclBodyItem,
    HclErrorRegion,
)
from consema.hcl.operation_registry import (
    FormatOperationDescriptor,
    HclFormatOperationRegistry,
    OperationArgumentDescriptor,
    OperationArgumentKind,
    OperationSupport,
    format_operation_registry,
)
from consema.hcl.projection import (
    CompleteProjection,
    ExpressionPolicy,
    FailedProjectionAttempt,
    Fidelity,
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
    project,
)
from consema.hcl.query import (
    HclCancellationToken,
    HclMatch,
    HclMatchKind,
    HclQueryExecution,
    HclQueryLimits,
    HclSyntaxMatch,
    execute_hcl_native_query,
    execute_hcl_syntax_query,
)

# Frozen profile, domain, and style ids (RFC 0014 §1/§7/§9).
HCL_NATIVE_PROFILE = "hcl.native@1"
HCL_TFVARS_PROFILE = "hcl.tfvars@1"
NATIVE_QUERY_DOMAIN_V1 = "hcl.native-semantic-query@1"
LOSSLESS_SYNTAX_QUERY_DOMAIN_V1 = "hcl.lossless-syntax-query@1"
HCL_CANONICAL_DOCUMENT_STYLE = "hcl.canonical-document@1"
HCL_BODY_RECORD = "hcl.body@1"
HCL_EXPRESSION_RECORD = "hcl.expression@1"

__all__ = [
    "BinaryOp",
    "BodyPath",
    "BodyPathStep",
    "BodyPlacement",
    "CompleteProjection",
    "EditCommit",
    "EditKey",
    "EditOperation",
    "EditOperationKind",
    "EditTransaction",
    "EditTransactionBuilder",
    "EditValue",
    "ExpressionPolicy",
    "FailedProjectionAttempt",
    "Fidelity",
    "FormatOperationDescriptor",
    "HCL_BODY_RECORD",
    "HCL_CANONICAL_DOCUMENT_STYLE",
    "HCL_EXPRESSION_RECORD",
    "HCL_NATIVE_PROFILE",
    "HCL_TFVARS_PROFILE",
    "HclAttribute",
    "HclBlock",
    "HclBlockLabel",
    "HclBody",
    "HclBodyItem",
    "HclCallArg",
    "HclCancellationToken",
    "HclDiagnostic",
    "HclDirectiveKind",
    "HclDocument",
    "HclEditFailure",
    "HclEditFailureKind",
    "HclEncodingSelection",
    "HclErrorRegion",
    "HclExpression",
    "HclExpressionKind",
    "HclExpressionKindName",
    "HclForIntro",
    "HclFormationFailure",
    "HclFormationFailureKind",
    "HclFormatOperationRegistry",
    "HclLiteralKey",
    "HclLiteralObjectEntry",
    "HclLiteralValue",
    "HclMatch",
    "HclMatchKind",
    "HclMaterializationFailure",
    "HclMaterializationFailureKind",
    "HclNumber",
    "HclObjectEntry",
    "HclObjectKey",
    "HclParseLimits",
    "HclProfile",
    "HclProjectionFailure",
    "HclProjectionFailureKind",
    "HclQueryExecution",
    "HclQueryLimits",
    "HclSeverity",
    "HclSyntaxKind",
    "HclSyntaxMatch",
    "HclTemplateKey",
    "HclTemplatePart",
    "HclTraversalRoot",
    "HclTraversalStep",
    "HeredocFacts",
    "HeredocMode",
    "LOSSLESS_SYNTAX_QUERY_DOMAIN_V1",
    "NATIVE_QUERY_DOMAIN_V1",
    "NodeRef",
    "ObjectSeparator",
    "OperationArgumentDescriptor",
    "OperationArgumentKind",
    "OperationSupport",
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
    "SourceOrigin",
    "UnaryOp",
    "ValuePath",
    "ValuePathSegment",
    "ValuePathSegmentKind",
    "canonical_decimal",
    "commit",
    "dry_run",
    "escape_text",
    "execute_hcl_native_query",
    "execute_hcl_syntax_query",
    "format_operation_registry",
    "is_identifier_continue",
    "is_identifier_start",
    "is_literal_complete",
    "literal_value",
    "materialize",
    "operation_metadata",
    "parse",
    "project",
    "render_decimal",
    "structural_fingerprint",
    "structural_fingerprint_hex",
]
