"""Unified `hcl.native@1` / `hcl.tfvars@1` document layer (RFC 0014 §1, §3,
§5).

The two profiles share one grammar and one native semantic model, so —
unlike the plist family (RFC 0013 §7) — the HCL Document is one structure
with the profile as a field: `hcl.tfvars@1` is `hcl.native@1` under one
structural restriction, the top-level body admits attributes only, never
blocks (RFC 0014 §5).

Formation runs the frozen native pipeline first, then the tfvars gate
rejects any top-level block with `hcl.tfvars.block-not-allowed@1` and
Recovered status (crates/consema-hcl/src/document.rs:46-116). The rejected
block stays a native item of the Recovered document (RFC 0014 §3, §7):
recovery retains every independently proven construct, and the tfvars
restriction is a profile-level rule that does not break the native model's
invariants. The gate emits diagnostics, never error regions.

The encoding contract is the frozen UTF-8-only source contract (RFC 0014
§2), validated by formation: a BOM is Recovered with
`hcl.parse.byte-order-mark@1`, invalid UTF-8 is a fatal formation failure
with `hcl.parse.invalid-utf8@1`, and a lone CR is Recovered with
`hcl.parse.lone-cr@1`. The caller-side explicit selection surface admits
UTF-8 only (crates/consema-hcl/src/lib.rs:120-164): any other explicit
encoding is a source-contract conflict that fails fatally with
`hcl.parse.encoding@1` before any byte is read.

Node identity: every native node of the body tree carries a deterministic
pre-order ordinal (parser.py `_assign_ordinals`; projection.rs:124-130);
this document exposes the ordinal map for snapshot-bound query handles.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.document.ids import FormatFamilyId, ProfileId
from consema.document.source import SourceEncoding, SourceSnapshot
from consema.document.structural import (
    DocumentAuthority,
    FormationStatus,
    LosslessStructuralIndex,
    NodeRole,
)
from consema.hcl.errors import (
    HclDiagnostic,
    HclFormationFailure,
    HclFormationFailureKind,
    HclSeverity,
    sort_diagnostics,
)
from consema.hcl.kinds import HclProfile, HclSyntaxKind
from consema.hcl.limits import HclParseLimits
from consema.hcl.native import HclBody, HclErrorRegion
from consema.hcl.parser import HclFormed, parse_hcl
from consema.protocol.error_registry import DiagnosticCategory

T_FVARS_BLOCK_NOT_ALLOWED = "hcl.tfvars.block-not-allowed@1"


class HclEncodingSelection(enum.Enum):
    """Explicit source-encoding selection for the UTF-8-only HCL source
    contract (RFC 0014 §2; lib.rs:120-164).

    HCL has no declaration, prolog, or encoding negotiation: the encoding
    is always UTF-8 and always selected before formation. `PROFILE_DEFAULT`
    and `EXPLICIT_UTF8` are consistent with the profile; any other explicit
    encoding is a source-contract conflict at formation.
    """

    PROFILE_DEFAULT = "ProfileDefault"
    EXPLICIT_UTF8 = "ExplicitUtf8"
    EXPLICIT_OTHER = "ExplicitOther"

    @classmethod
    def explicit(cls, encoding: SourceEncoding) -> HclEncodingSelection:
        """Builds the selection for one caller-selected encoding
        (lib.rs:138-150)."""
        if encoding == SourceEncoding.utf8():
            return cls.EXPLICIT_UTF8
        return cls.EXPLICIT_OTHER


@dataclass(frozen=True, slots=True)
class HclDocument:
    """One formed HCL document under one exact profile (RFC 0014 §1, §3,
    §5; document.rs:50-217).

    The profile is a private field, not a representation choice: both
    profiles share the one syntax system and the one native model, and the
    profile gates Complete formation (the tfvars top-level restriction of
    RFC 0014 §5) and the operation surface published over this document.
    Every returned fact is an immutable snapshot fact.
    """

    authority: DocumentAuthority
    source: SourceSnapshot
    profile: HclProfile
    structural_index: LosslessStructuralIndex
    syntax_kinds: tuple[HclSyntaxKind, ...]
    _formation_status: FormationStatus
    diagnostics: tuple[HclDiagnostic, ...]
    body: HclBody
    error_regions: tuple[HclErrorRegion, ...]
    parse_limits: HclParseLimits
    _ordinals: dict[int, int]
    _tree_nodes: int

    # -- identity and source -------------------------------------------------

    def snapshot_identity(self) -> object:
        """Snapshot identity to which every NodeRef and Span belongs."""
        return self.authority.identity

    def render(self) -> bytes:
        """Exact original bytes; unmodified rendering is byte-exact."""
        return self.source.bytes()

    def format_family(self) -> FormatFamilyId:
        """Stable HCL format family identity (document.rs:162-166)."""
        return FormatFamilyId.new("hcl", 1)

    def profile_id(self) -> ProfileId:
        """Exact language profile (document.rs:156-160)."""
        return self.profile.id()

    def formation_status(self) -> FormationStatus:
        """Complete or explicitly recovered formation state (RFC 0014 §3)."""
        return self._formation_status

    def diagnostic_records(self) -> tuple[HclDiagnostic, ...]:
        """Ordered diagnostics from formation, deterministically sorted."""
        return self.diagnostics

    def lossless_structural_index(self) -> LosslessStructuralIndex:
        """Exhaustive ordered lossless piece coverage of the raw bytes
        (RFC 0014 §7.2); always present under both profiles."""
        return self.structural_index

    def lossless_syntax_kinds(self) -> tuple[HclSyntaxKind, ...]:
        """Ordered syntax kinds, parallel to the lossless structural pieces
        (RFC 0014 §7.2)."""
        return self.syntax_kinds

    def error_region_records(self) -> tuple[HclErrorRegion, ...]:
        """Recovered error regions in source order (RFC 0014 §3, §7.2).

        The tfvars gate never contributes an error region: a rejected
        top-level block is a proven construct, not a recovered region.
        """
        return self.error_regions

    def document(self) -> HclDocument:
        """The native body tree bound to the frozen source; always present
        under both profiles, an empty body being a valid body."""
        return self

    def root_body(self) -> HclBody:
        """The root native body (RFC 0014 §6)."""
        return self.body

    # -- node identity -------------------------------------------------------

    def node_ref(self, node) -> object:
        """One snapshot-bound handle for a native tree node or error region
        (query.rs pre-order rank identity)."""
        return self.authority.node_ref(self.ordinal_of(node), _node_role_of(node))

    def ordinal_of(self, node) -> int:
        """The deterministic pre-order ordinal of one native tree node."""
        return self._ordinals[id(node)]

    def tree_node_count(self) -> int:
        """Total native tree nodes; error regions continue after them."""
        return self._tree_nodes


def _node_role_of(node) -> NodeRole:
    """The frozen node role of one native tree node (consema-document
    NodeRole closed vocabulary, structural.py:151-159)."""
    from consema.hcl.native import (
        HclAttribute,
        HclBlock,
        HclBlockLabel,
        HclBody,
        HclErrorRegion,
    )
    from consema.hcl.expression import HclExpression, HclTemplatePart

    if isinstance(node, HclBody):
        return NodeRole.HCL_BODY
    if isinstance(node, HclAttribute):
        return NodeRole.HCL_ATTRIBUTE
    if isinstance(node, HclBlock):
        return NodeRole.HCL_BLOCK
    if isinstance(node, HclBlockLabel):
        return NodeRole.HCL_BLOCK_LABEL
    if isinstance(node, HclExpression):
        return NodeRole.HCL_EXPRESSION
    if isinstance(node, HclTemplatePart):
        return NodeRole.HCL_TEMPLATE_PART
    if isinstance(node, HclErrorRegion):
        return NodeRole.HCL_ERROR_REGION
    raise TypeError(f"not an HCL native node: {node!r}")


def parse(
    source: bytes,
    profile: HclProfile,
    selection: HclEncodingSelection = HclEncodingSelection.PROFILE_DEFAULT,
    limits: HclParseLimits = None,
) -> HclDocument:
    """Forms one HCL document from raw bytes under one exact profile (RFC
    0014 §1, §3, §5; lib.rs:275-310, document.rs:87-116).

    The profile is selected by the caller before formation; neither the
    `.tf` nor the `.tfvars` extension selects a profile, representation, or
    encoding. A non-UTF-8 explicit selection is a caller-side
    source-contract conflict and fails fatally with `hcl.parse.encoding@1`
    before any byte is read; a BOM in the source stays content-level
    recovery under either consistent selection.
    """
    if limits is None:
        limits = HclParseLimits()
    if selection is HclEncodingSelection.EXPLICIT_OTHER:
        raise HclFormationFailure(HclFormationFailureKind.ENCODING)
    formed: HclFormed = parse_hcl(source, limits)
    diagnostics = list(formed.diagnostics)
    status = formed.status
    if profile is HclProfile.TFVARS_V1:
        for item in formed.body.items:
            block = item.as_block()
            if block is not None:
                status = FormationStatus.RECOVERED
                diagnostics.append(
                    HclDiagnostic(
                        code=T_FVARS_BLOCK_NOT_ALLOWED,
                        category=DiagnosticCategory.SYNTAX,
                        severity=HclSeverity.ERROR,
                        primary=block.span,
                    )
                )
    sort_diagnostics(diagnostics)
    return HclDocument(
        authority=formed.authority,
        source=formed.source,
        profile=profile,
        structural_index=formed.syntax,
        syntax_kinds=formed.syntax_kinds,
        _formation_status=status,
        diagnostics=tuple(diagnostics),
        body=formed.body,
        error_regions=formed.error_regions,
        parse_limits=limits,
        _ordinals=formed.ordinals,
        _tree_nodes=formed.tree_nodes,
    )
