"""consema.document — the document domain (L1 milestone, mirror of Go G1.1).

Immutable source snapshots (raw bytes + encoding facts + content digest),
structural locations (Span over raw bytes, snapshot-bound NodeRef), formation
status, parse limits, the common materialization request, verifiable
byte-level source patches, untouched-byte proofs, and dry-run edit plans.

Package topology mirrors RFC 0016 §3.2 (docs/rfcs/0016-go-api-mapping-v1.md:
99-109): ``consema/document`` maps to the Rust ``consema-document`` crate.

Authority (language-neutral first; Rust only for byte/registry arbitration):
- conformance/vectors/source-v1.json — the machine-readable case suite
  "consema.source.conformance@1" (28 cases; digest, encoding, decoded
  location, binary coverage, patch, limits capabilities);
- RFC 0003 (source facts / snapshot / patch), RFC 0004 (materialization /
  edit / proof / plan), RFC 0016 §5-§6 (API shapes, error classification);
- crates/consema-document/src/*.rs for byte/registry arbitration only;
- go/document as a cross-reference only (never a template).

This package is an independent, Python-idiomatic implementation with zero
third-party runtime dependencies. Error text is human presentation only and
never participates in conformance comparison (RFC 0016 §6).
"""

from consema.document.change_set import (
    ChangeSet,
    NodeMapping,
    NodeMappingStatus,
    SourceEdit,
)
from consema.document.edit_plan import (
    EditOperationSummary,
    EditPlan,
    EditPlanError,
    EditPlanErrorKind,
    EditPlanSourceId,
)
from consema.document.ids import (
    ContentDigest,
    FormatFamilyId,
    FormatOperationId,
    MaterializationStyleId,
    ProfileId,
)
from consema.document.limits import ParseLimits
from consema.document.materialization import (
    CompleteMaterialization,
    FailedMaterializationAttempt,
    MappingPolicy,
    MaterializationFailure,
    MaterializationFailureKind,
    MaterializationFidelity,
    MaterializationInputLocation,
    MaterializationInputLocationKind,
    MaterializationLimits,
    MaterializationProvenanceEntry,
    MaterializationProvenanceMap,
    MaterializationRelation,
    MaterializationReport,
    MaterializationRequest,
    MaterializationResult,
    MaterializationStyleId,
    MaterializedOrigin,
    NewlinePolicy,
    RepresentabilityPolicy,
)
from consema.document.source import (
    BomKind,
    BomPolicy,
    DecodedOffset,
    DecodedOffsetKind,
    DecodedPosition,
    EncodingFacts,
    EncodingRequest,
    SourceEncoding,
    SourceEncodingKind,
    SourceError,
    SourceErrorKind,
    SourceLimits,
    SourceSnapshot,
    UnsupportedBomKind,
    WindowsCodePage,
)
from consema.document.source_patch import (
    SourcePatch,
    SourcePatchError,
    SourcePatchErrorKind,
    SourcePatchLimits,
    SourcePatchRedactionError,
    SourceReplacement,
)
from consema.document.structural import (
    AssociationPlacement,
    BinaryRegion,
    BinaryStructuralIndex,
    DocumentAuthority,
    FormationStatus,
    LocationError,
    LocationErrorKind,
    LosslessStructuralIndex,
    NodeRef,
    NodeRole,
    SnapshotIdentity,
    Span,
    StructuralPiece,
    StructuralPieceKind,
)
from consema.document.untouched_proof import (
    UntouchedByteProof,
    UntouchedByteProofError,
    UntouchedByteProofErrorKind,
    UntouchedByteRegion,
)

__all__ = [
    "AssociationPlacement",
    "BinaryRegion",
    "BinaryStructuralIndex",
    "BomKind",
    "BomPolicy",
    "ChangeSet",
    "CompleteMaterialization",
    "ContentDigest",
    "DecodedOffset",
    "DecodedOffsetKind",
    "DecodedPosition",
    "DocumentAuthority",
    "EditOperationSummary",
    "EditPlan",
    "EditPlanError",
    "EditPlanErrorKind",
    "EditPlanSourceId",
    "EncodingFacts",
    "EncodingRequest",
    "FailedMaterializationAttempt",
    "FormationStatus",
    "FormatFamilyId",
    "FormatOperationId",
    "LocationError",
    "LocationErrorKind",
    "LosslessStructuralIndex",
    "MappingPolicy",
    "MaterializationFailure",
    "MaterializationFailureKind",
    "MaterializationFidelity",
    "MaterializationInputLocation",
    "MaterializationInputLocationKind",
    "MaterializationLimits",
    "MaterializationProvenanceEntry",
    "MaterializationProvenanceMap",
    "MaterializationRelation",
    "MaterializationReport",
    "MaterializationRequest",
    "MaterializationResult",
    "MaterializationStyleId",
    "MaterializedOrigin",
    "NewlinePolicy",
    "NodeMapping",
    "NodeMappingStatus",
    "NodeRef",
    "NodeRole",
    "ParseLimits",
    "ProfileId",
    "RepresentabilityPolicy",
    "SnapshotIdentity",
    "SourceEdit",
    "SourceEncoding",
    "SourceEncodingKind",
    "SourceError",
    "SourceErrorKind",
    "SourceLimits",
    "SourcePatch",
    "SourcePatchError",
    "SourcePatchErrorKind",
    "SourcePatchLimits",
    "SourcePatchRedactionError",
    "SourceReplacement",
    "SourceSnapshot",
    "Span",
    "StructuralPiece",
    "StructuralPieceKind",
    "UnsupportedBomKind",
    "UntouchedByteProof",
    "UntouchedByteProofError",
    "UntouchedByteProofErrorKind",
    "UntouchedByteRegion",
    "WindowsCodePage",
]
