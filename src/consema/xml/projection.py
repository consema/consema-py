"""XML projection targets and explicit mapping policies (RFC 0012 §9).

Authority:

- RFC 0012 §9 (docs/rfcs/0012-xml-1.0-safe-profile-v1.md:313-348): the
  exact default target is the versioned ``xml.element-tree@1`` record
  containing declaration facts, admitted internal entity declarations, one
  namespace-aware root, ordered namespace declarations, ordered attributes,
  ordered mixed content, exact text/reference fragments, CDATA, comments,
  and PI; additional explicit targets are
  ``xml.projection.text-content@1`` (always Transformed, requires a policy
  for descendant text and CDATA, reports every discarded element,
  attribute, comment, PI, and source-reference distinction) and
  ``xml.projection.simple-entry-mapping@1`` (admitted only without mixed
  content, comments, PI, duplicate expanded child names, non-text child
  values, namespace collision, or attribute/child ambiguity; the default
  for any omitted policy is failure, not LastWins or convention guessing).
  There is no ``xml-to-json-default``, automatic attribute ``@`` prefix,
  automatic text ``#text`` key, or child grouping.
- The record shapes and every policy transcribe
  crates/consema-xml/src/projection.rs:20-469 (ProjectionTarget:21-29,
  TextContentInclude:31-38, AttributePolicy:39-49, TextKeyPolicy:51-58,
  RepeatedChildPolicy:60-69, ExpandedNameKeyPolicy:71-81, CollisionPolicy:
  116-124, ProjectionRequest:126-213, ProjectionLimits:215-237,
  Fidelity:239-248, ProjectedLocation:250-257, ProvenanceRelation:259-270,
  SourceOrigin:272-283, ProvenanceEntry:285-292, ProvenanceMap:294-323,
  ProjectionEventKind:325-346, ProjectionEvent:348-357, ProjectionReport:
  359-388, CompleteProjection:390-401, FailedProjectionAttempt:403-410,
  ProjectionResult:412-419, ProjectionFailure:421-469) and the tree walk
  projection.rs:471-1455 — byte/registry arbitration only.
- Provenance is the reverse-direction map of materialization provenance
  (RFC 0004 §8, lines 193-197); value/association paths are the
  semantic-model records (consema.xml.paths).
- Vector coverage: conformance/vectors/xml-1-0-safe-v1.json cases
  ``xml.projection.element-tree-record`` (lines 311-325),
  ``xml.projection.namespace-record`` (327-339),
  ``xml.projection.recovered-never-projects`` (341-350).

go/xml is a cross-reference only; no code structure is copied.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.core.value import PortableValue
from consema.document.structural import (
    FormationStatus,
    NodeRef,
    NodeRole,
    SnapshotIdentity,
    Span,
)

from consema.xml.document import (
    Document,
    ReferenceFragmentKind,
    XmlContent,
    XmlContentKind,
    XmlDeclarationData,
    XmlElementData,
    text_semantic,
)
from consema.xml.errors import XmlDiagnostic, XmlProjectionFailure, XmlProjectionFailureKind
from consema.xml.paths import (
    AssociationLocation,
    AssociationRole,
    ValuePath,
    ValuePathSegment,
)


class ProjectionTarget(enum.Enum):
    """Versioned XML projection target (projection.rs:21-29)."""

    ELEMENT_TREE_V1 = "xml.element-tree@1"
    TEXT_CONTENT_V1 = "xml.projection.text-content@1"
    SIMPLE_ENTRY_MAPPING_V1 = "xml.projection.simple-entry-mapping@1"


class TextContentInclude(enum.Enum):
    """Descendant text inclusion for TextContentV1 (projection.rs:31-38)."""

    TEXT_AND_CDATA = "TextAndCdata"
    TEXT_ONLY = "TextOnly"


class AttributePolicy(enum.Enum):
    """Attribute handling for SimpleEntryMappingV1 (projection.rs:39-49)."""

    REJECT_ATTRIBUTES = "RejectAttributes"
    IGNORE_ATTRIBUTES = "IgnoreAttributes"
    PREFIX_ATTRIBUTE_KEYS = "PrefixAttributeKeys"


class TextKeyPolicy(enum.Enum):
    """Text child handling for SimpleEntryMappingV1 (projection.rs:51-58)."""

    REJECT_TEXT = "RejectText"
    IGNORE_TEXT = "IgnoreText"


class RepeatedChildPolicy(enum.Enum):
    """Repeated expanded-child-name handling (projection.rs:60-69)."""

    REJECT = "Reject"
    FIRST = "First"
    LAST = "Last"


class ExpandedNameKeyPolicy(enum.Enum):
    """Entry-key spelling for SimpleEntryMappingV1 (projection.rs:71-81)."""

    LOCAL_ONLY = "LocalOnly"
    PREFIXED_SPELLING = "PrefixedSpelling"
    URI_BRACKETED = "UriBracketed"


class CollisionPolicy(enum.Enum):
    """Collision resolution direction (projection.rs:116-124)."""

    REJECT = "Reject"
    FIRST = "First"
    LAST = "Last"


@dataclass(frozen=True, slots=True)
class ProjectionRequest:
    """Explicit XML projection request; every policy is mandatory
    (projection.rs:126-213)."""

    target: ProjectionTarget
    subtree: int | None = None
    include: TextContentInclude = TextContentInclude.TEXT_AND_CDATA
    attributes: AttributePolicy = AttributePolicy.REJECT_ATTRIBUTES
    text_key: TextKeyPolicy = TextKeyPolicy.REJECT_TEXT
    repeated_child: RepeatedChildPolicy = RepeatedChildPolicy.REJECT
    key_spelling: ExpandedNameKeyPolicy = ExpandedNameKeyPolicy.LOCAL_ONLY
    collision: CollisionPolicy = CollisionPolicy.REJECT
    limits: "ProjectionLimits" = field(default_factory=lambda: ProjectionLimits())

    @classmethod
    def element_tree(cls) -> ProjectionRequest:
        """Exact ``xml.element-tree@1`` record request for the document
        root (projection.rs:141-155)."""
        return cls(target=ProjectionTarget.ELEMENT_TREE_V1)

    @classmethod
    def simple_entry_mapping(
        cls,
        subtree: NodeRef,
        attributes: AttributePolicy,
        text_key: TextKeyPolicy,
        repeated_child: RepeatedChildPolicy,
        key_spelling: ExpandedNameKeyPolicy,
        collision: CollisionPolicy,
    ) -> ProjectionRequest:
        """Explicit SimpleEntryMappingV1 request over one subtree
        (projection.rs:158-178)."""
        return cls(
            target=ProjectionTarget.SIMPLE_ENTRY_MAPPING_V1,
            subtree=subtree.index,
            attributes=attributes,
            text_key=text_key,
            repeated_child=repeated_child,
            key_spelling=key_spelling,
            collision=collision,
        )

    @classmethod
    def text_content(cls, subtree: NodeRef, include: TextContentInclude) -> ProjectionRequest:
        """Explicit TextContentV1 request over one subtree
        (projection.rs:180-194)."""
        return cls(
            target=ProjectionTarget.TEXT_CONTENT_V1,
            subtree=subtree.index,
            include=include,
        )


@dataclass(frozen=True, slots=True)
class ProjectionLimits:
    """XML projection resource limits (projection.rs:215-237)."""

    max_source_nodes: int = 2_000_000
    max_value_nodes: int = 2_000_000
    max_report_entries: int = 100_000
    max_provenance_units: int = 4_000_000


class Fidelity(enum.Enum):
    """Projection fidelity classification (projection.rs:239-248)."""

    EXACT = "Exact"
    TRANSFORMED = "Transformed"
    LOSSY = "Lossy"


class ProjectedLocationKind(enum.Enum):
    """Projected value or association location kind (projection.rs:250-257)."""

    VALUE = "Value"
    ASSOCIATION = "Association"


@dataclass(frozen=True, slots=True)
class ProjectedLocation:
    """Projected value or association location (projection.rs:250-257)."""

    kind: ProjectedLocationKind
    path: ValuePath | None = None
    association: AssociationLocation | None = None

    @classmethod
    def value(cls, path: ValuePath) -> ProjectedLocation:
        return cls(kind=ProjectedLocationKind.VALUE, path=path)

    @classmethod
    def of_association(cls, location: AssociationLocation) -> ProjectedLocation:
        """The association variant; spelled ``of_association`` because the
        ``association`` field owns the plain name."""
        return cls(kind=ProjectedLocationKind.ASSOCIATION, association=location)


class ProvenanceRelation(enum.Enum):
    """Source-to-projection relation (projection.rs:259-270)."""

    DIRECT = "Direct"
    DERIVED = "Derived"
    COLLAPSED = "Collapsed"
    REFERENCE_DERIVED = "ReferenceDerived"


@dataclass(frozen=True, slots=True)
class SourceOrigin:
    """One exact source origin (projection.rs:272-283)."""

    snapshot: SnapshotIdentity
    node: NodeRef
    span: Span
    relation: ProvenanceRelation


@dataclass(frozen=True, slots=True)
class ProvenanceEntry:
    """One many-valued provenance entry (projection.rs:285-292)."""

    projected: ProjectedLocation
    origins: tuple[SourceOrigin, ...]


@dataclass(frozen=True, slots=True)
class ProvenanceMap:
    """Immutable many-valued provenance mapping (projection.rs:294-323)."""

    entries: tuple[ProvenanceEntry, ...] = field(default_factory=tuple)


class ProjectionEventKind(enum.Enum):
    """Projection report category (projection.rs:325-346)."""

    ELEMENT_DISCARDED = "element-discarded"
    ATTRIBUTE_DISCARDED = "attribute-discarded"
    TEXT_DISCARDED = "text-discarded"
    CDATA_DISCARDED = "cdata-discarded"
    COMMENT_DISCARDED = "comment-discarded"
    PROCESSING_INSTRUCTION_DISCARDED = "processing-instruction-discarded"
    REFERENCE_COLLAPSED = "reference-collapsed"
    CHILD_COLLAPSED = "child-collapsed"
    NAMESPACE_COLLAPSED = "namespace-collapsed"


@dataclass(frozen=True, slots=True)
class ProjectionEvent:
    """One explicit transformation event (projection.rs:348-357)."""

    kind: ProjectionEventKind
    discarded: NodeRef
    impact: Fidelity


@dataclass(frozen=True, slots=True)
class ProjectionReport:
    """Complete ordered projection report (projection.rs:359-388)."""

    events: tuple[ProjectionEvent, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class CompleteProjection:
    """Complete successful projection (projection.rs:390-401)."""

    value: PortableValue
    fidelity: Fidelity
    report: ProjectionReport = field(default_factory=ProjectionReport)
    provenance: ProvenanceMap = field(default_factory=ProvenanceMap)


@dataclass(frozen=True, slots=True)
class FailedProjectionAttempt:
    """Failed projection attempt without a partial value
    (projection.rs:403-410)."""

    diagnostics: tuple[XmlDiagnostic, ...]
    report: ProjectionReport = field(default_factory=ProjectionReport)


# The closed projection completion algebra (projection.rs:412-419):
# Complete success or failure with no value or provenance map.
ProjectionResult = CompleteProjection | FailedProjectionAttempt


class _Context:
    __slots__ = (
        "document",
        "limits",
        "events",
        "provenance",
        "value_nodes",
        "source_nodes",
    )

    def __init__(self, document: Document, limits: ProjectionLimits) -> None:
        self.document = document
        self.limits = limits
        self.events: list[ProjectionEvent] = []
        self.provenance: list[ProvenanceEntry] = []
        self.value_nodes = 0
        self.source_nodes = 0

    def step(self) -> None:
        self.source_nodes += 1
        if self.source_nodes > self.limits.max_source_nodes:
            raise XmlProjectionFailure(
                XmlProjectionFailureKind.RESOURCE_LIMIT, limit_name="max_source_nodes"
            )

    def reserve_value(self, count: int) -> None:
        self.value_nodes += count
        if self.value_nodes > self.limits.max_value_nodes:
            raise XmlProjectionFailure(
                XmlProjectionFailureKind.RESOURCE_LIMIT, limit_name="max_value_nodes"
            )

    def event(self, kind: ProjectionEventKind, discarded: NodeRef, impact: Fidelity) -> None:
        if len(self.events) >= self.limits.max_report_entries:
            raise XmlProjectionFailure(
                XmlProjectionFailureKind.RESOURCE_LIMIT, limit_name="max_report_entries"
            )
        self.events.append(ProjectionEvent(kind=kind, discarded=discarded, impact=impact))

    def origin(self, projected: ProjectedLocation, node: NodeRef, span: Span, relation) -> None:
        if len(self.provenance) >= self.limits.max_provenance_units:
            raise XmlProjectionFailure(
                XmlProjectionFailureKind.RESOURCE_LIMIT, limit_name="max_provenance_units"
            )
        self.provenance.append(
            ProvenanceEntry(
                projected=projected,
                origins=(
                    SourceOrigin(
                        snapshot=self.document.snapshot_identity(),
                        node=node,
                        span=span,
                        relation=relation,
                    ),
                ),
            )
        )

    def element_data(self, index: int) -> XmlElementData:
        return self.document._element_data(index)

    def element_node_ref(self, index: int) -> NodeRef:
        return self.document.node_ref(index, NodeRole.XML_ELEMENT)

    def occurrence_node_ref(self, ordinal: int, role: NodeRole) -> NodeRef:
        return self.document.occurrence_node_ref(ordinal, role)


def project_document(document: Document, request: ProjectionRequest) -> ProjectionResult:
    """Projects one snapshot under one explicit target and policy contract
    (projection.rs:471-503)."""
    if document.status is not FormationStatus.COMPLETE:
        return _failed(XmlProjectionFailure(XmlProjectionFailureKind.RECOVERED_DOCUMENT))
    context = _Context(document, request.limits)
    try:
        if request.target is ProjectionTarget.ELEMENT_TREE_V1:
            value, fidelity = _project_element_tree(context)
        elif request.target is ProjectionTarget.TEXT_CONTENT_V1:
            value, fidelity = _project_text_content(context, request.subtree, request.include)
        else:
            value, fidelity = _project_entry_mapping(context, request)
    except XmlProjectionFailure as failure:
        return _failed(failure)
    return CompleteProjection(
        value=value,
        fidelity=fidelity,
        report=ProjectionReport(events=tuple(context.events)),
        provenance=ProvenanceMap(entries=tuple(context.provenance)),
    )


def _failed(failure: XmlProjectionFailure) -> FailedProjectionAttempt:
    return FailedProjectionAttempt(diagnostics=(failure.to_diagnostic(),))


def _item_path(container: ValuePath, field_name: str, index: int) -> ValuePath:
    return container.child(ValuePathSegment.object_value(field_name)).child(
        ValuePathSegment.sequence_element(index)
    )


def _project_element_tree(context: _Context) -> tuple[PortableValue, Fidelity]:
    """Exact ``xml.element-tree@1`` record for the document root
    (projection.rs:600-644)."""
    root = context.document.root()
    if root is None:
        raise XmlProjectionFailure(
            XmlProjectionFailureKind.MAPPING_ADMISSION, reason="missing root"
        )
    entries: list[tuple[str, PortableValue]] = [
        ("record", PortableValue.string("xml.element-tree@1"))
    ]
    declared = context.document.declaration()
    if declared is not None:
        entries.append(("declaration", _declaration_value(declared)))
    doctype = context.document.doctype()
    if doctype is not None and doctype.entities:
        entity_list = [
            PortableValue.object(
                (
                    ("name", PortableValue.string(entity.name)),
                    ("replacement", PortableValue.string(entity.replacement)),
                )
            )
            for entity in doctype.entities
        ]
        entries.append(("entities", PortableValue.sequence(entity_list)))
    root_path = ValuePath.root().child(ValuePathSegment.object_value("root"))
    root_value, _ = _element_value(context, root.index, root_path)
    entries.append(("root", root_value))
    return PortableValue.object(entries), Fidelity.EXACT


def _declaration_value(declared: XmlDeclarationData) -> PortableValue:
    """One declaration record (projection.rs:646-667)."""
    entries: list[tuple[str, PortableValue]] = [
        ("version", PortableValue.string(declared.version))
    ]
    if declared.encoding is not None:
        entries.append(("encoding", PortableValue.string(declared.encoding[1])))
    if declared.standalone is not None:
        entries.append(("standalone", PortableValue.boolean(declared.standalone[1])))
    return PortableValue.object(entries)


def _element_value(
    context: _Context, index: int, path: ValuePath
) -> tuple[PortableValue, int]:
    """Recursive element record (projection.rs:671-797)."""
    context.step()
    data = context.element_data(index)
    span = data.span
    if data.expanded is not None:
        namespace, local = data.expanded.namespace, data.expanded.local
    else:
        namespace, local = None, data.qname.local
    name = PortableValue.object(
        (
            (
                "namespace",
                PortableValue.null()
                if namespace is None
                else PortableValue.string(namespace),
            ),
            ("local", PortableValue.string(local)),
        )
    )
    entries: list[tuple[str, PortableValue]] = [("expanded-name", name)]
    if data.namespaces:
        namespace_list = []
        for item, binding in enumerate(data.namespaces):
            binding_value = PortableValue.object(
                (
                    (
                        "prefix",
                        PortableValue.null()
                        if binding.prefix is None
                        else PortableValue.string(binding.prefix),
                    ),
                    ("uri", PortableValue.string(binding.uri)),
                )
            )
            context.origin(
                ProjectedLocation.value(_item_path(path, "namespaces", item)),
                context.occurrence_node_ref(binding.ordinal, NodeRole.XML_NAMESPACE_BINDING),
                binding.span,
                ProvenanceRelation.DIRECT,
            )
            namespace_list.append(binding_value)
        entries.append(("namespaces", PortableValue.sequence(namespace_list)))
    if data.attributes:
        attribute_list = []
        for item, attribute in enumerate(data.attributes):
            if attribute.expanded is not None:
                attr_namespace, attr_local = attribute.expanded.namespace, attribute.expanded.local
            else:
                attr_namespace, attr_local = None, attribute.qname.local
            attr_name = PortableValue.object(
                (
                    (
                        "namespace",
                        PortableValue.null()
                        if attr_namespace is None
                        else PortableValue.string(attr_namespace),
                    ),
                    ("local", PortableValue.string(attr_local)),
                )
            )
            attribute_value = PortableValue.object(
                (
                    ("expanded-name", attr_name),
                    ("value", PortableValue.string(attribute.normalized_value)),
                )
            )
            context.origin(
                ProjectedLocation.value(_item_path(path, "attributes", item)),
                context.occurrence_node_ref(attribute.ordinal, NodeRole.XML_ATTRIBUTE),
                attribute.span,
                ProvenanceRelation.DIRECT,
            )
            attribute_list.append(attribute_value)
        entries.append(("attributes", PortableValue.sequence(attribute_list)))
    if data.children:
        content_list = []
        for item, child in enumerate(data.children):
            value, _ = _content_value(context, child, _item_path(path, "content", item))
            content_list.append(value)
        entries.append(("content", PortableValue.sequence(content_list)))
    value = PortableValue.object(entries)
    context.reserve_value(1)
    context.origin(
        ProjectedLocation.value(path),
        context.element_node_ref(index),
        span,
        ProvenanceRelation.DIRECT,
    )
    return value, index


def _content_value(
    context: _Context, index: int, path: ValuePath
) -> tuple[PortableValue, int]:
    """One ordered content item record (projection.rs:800-973)."""
    context.step()
    content: XmlContent = context.document._nodes[index]
    if content.kind is XmlContentKind.ELEMENT:
        return _element_value(context, index, path)
    if content.kind is XmlContentKind.TEXT:
        data = content.data
        fragment_list = []
        for item, fragment in enumerate(data.fragments):
            if fragment.kind is ReferenceFragmentKind.LITERAL:
                fragment_value = PortableValue.object(
                    (
                        ("kind", PortableValue.string("literal")),
                        ("text", PortableValue.string(fragment.text or "")),
                    )
                )
            elif fragment.kind is ReferenceFragmentKind.CHARACTER_REFERENCE:
                fragment_value = PortableValue.object(
                    (
                        ("kind", PortableValue.string("character-reference")),
                        ("resolved", PortableValue.string(fragment.resolved or "")),
                    )
                )
            elif fragment.kind is ReferenceFragmentKind.PREDEFINED_ENTITY:
                fragment_value = PortableValue.object(
                    (
                        ("kind", PortableValue.string("predefined-entity")),
                        ("name", PortableValue.string(fragment.name or "")),
                        ("resolved", PortableValue.string(fragment.resolved or "")),
                    )
                )
            else:
                fragment_value = PortableValue.object(
                    (
                        ("kind", PortableValue.string("general-entity")),
                        ("name", PortableValue.string(fragment.name or "")),
                        ("resolved", PortableValue.string(fragment.resolved or "")),
                    )
                )
            context.origin(
                ProjectedLocation.value(_item_path(path, "fragments", item)),
                context.occurrence_node_ref(data.ordinal, NodeRole.XML_ENTITY_REFERENCE),
                fragment.span,
                ProvenanceRelation.REFERENCE_DERIVED,
            )
            fragment_list.append(fragment_value)
        value = PortableValue.object(
            (
                ("kind", PortableValue.string("text")),
                ("fragments", PortableValue.sequence(fragment_list)),
            )
        )
        context.reserve_value(1)
        context.origin(
            ProjectedLocation.value(path),
            context.occurrence_node_ref(data.ordinal, NodeRole.XML_TEXT),
            data.span,
            ProvenanceRelation.DIRECT,
        )
        return value, index
    if content.kind is XmlContentKind.CDATA:
        data = content.data
        value = PortableValue.object(
            (("kind", PortableValue.string("cdata")), ("text", PortableValue.string(data.text)))
        )
        context.reserve_value(1)
        context.origin(
            ProjectedLocation.value(path),
            context.occurrence_node_ref(data.ordinal, NodeRole.XML_CDATA),
            data.span,
            ProvenanceRelation.DIRECT,
        )
        return value, index
    if content.kind is XmlContentKind.COMMENT:
        data = content.data
        value = PortableValue.object(
            (
                ("kind", PortableValue.string("comment")),
                ("text", PortableValue.string(data.text)),
            )
        )
        context.reserve_value(1)
        context.origin(
            ProjectedLocation.value(path),
            context.occurrence_node_ref(data.ordinal, NodeRole.XML_COMMENT),
            data.span,
            ProvenanceRelation.DIRECT,
        )
        return value, index
    if content.kind is XmlContentKind.PROCESSING_INSTRUCTION:
        data = content.data
        entries: list[tuple[str, PortableValue]] = [
            ("kind", PortableValue.string("processing-instruction")),
            ("target", PortableValue.string(data.target)),
        ]
        if data.content is not None:
            entries.append(("content", PortableValue.string(data.content[1])))
        value = PortableValue.object(entries)
        context.reserve_value(1)
        context.origin(
            ProjectedLocation.value(path),
            context.occurrence_node_ref(data.ordinal, NodeRole.XML_PROCESSING_INSTRUCTION),
            data.span,
            ProvenanceRelation.DIRECT,
        )
        return value, index
    data = content.data
    value = PortableValue.object((("kind", PortableValue.string("error-region")),))
    context.reserve_value(1)
    context.origin(
        ProjectedLocation.value(path),
        context.occurrence_node_ref(data.ordinal, NodeRole.XML_ERROR_REGION),
        data.span,
        ProvenanceRelation.DIRECT,
    )
    return value, index


def _project_text_content(
    context: _Context, subtree: int | None, include: TextContentInclude
) -> tuple[PortableValue, Fidelity]:
    """Always-transformed descendant text content (projection.rs:976-1095)."""
    root = context.document.root()
    if root is None:
        raise XmlProjectionFailure(
            XmlProjectionFailureKind.MAPPING_ADMISSION, reason="missing root"
        )
    start = root.index if subtree is None else subtree
    if context.document._nodes[start].kind is not XmlContentKind.ELEMENT:
        raise XmlProjectionFailure(XmlProjectionFailureKind.SUBTREE_NOT_ELEMENT)
    output: list[str] = []
    _collect_text(context, start, include, output)
    value = PortableValue.string("".join(output))
    context.reserve_value(1)
    context.origin(
        ProjectedLocation.value(ValuePath.root()),
        context.element_node_ref(start),
        context.element_data(start).span,
        ProvenanceRelation.DERIVED,
    )
    return value, Fidelity.TRANSFORMED


def _collect_text(
    context: _Context, index: int, include: TextContentInclude, output: list[str]
) -> None:
    """One descendant text walk (projection.rs:1009-1095)."""
    data = context.element_data(index)
    for child in data.children:
        content = context.document._nodes[child]
        if content.kind is XmlContentKind.ELEMENT:
            child_data = content.data
            context.event(
                ProjectionEventKind.ELEMENT_DISCARDED,
                context.element_node_ref(child),
                Fidelity.TRANSFORMED,
            )
            for attribute in child_data.attributes:
                context.event(
                    ProjectionEventKind.ATTRIBUTE_DISCARDED,
                    context.occurrence_node_ref(attribute.ordinal, NodeRole.XML_ATTRIBUTE),
                    Fidelity.TRANSFORMED,
                )
            _collect_text(context, child, include, output)
        elif content.kind is XmlContentKind.TEXT:
            text_data = content.data
            for fragment in text_data.fragments:
                if fragment.kind is not ReferenceFragmentKind.LITERAL:
                    context.event(
                        ProjectionEventKind.REFERENCE_COLLAPSED,
                        context.occurrence_node_ref(
                            text_data.ordinal, NodeRole.XML_ENTITY_REFERENCE
                        ),
                        Fidelity.TRANSFORMED,
                    )
            output.append(text_semantic(text_data))
        elif content.kind is XmlContentKind.CDATA:
            cdata_data = content.data
            if include is TextContentInclude.TEXT_AND_CDATA:
                output.append(cdata_data.text)
            else:
                context.event(
                    ProjectionEventKind.CDATA_DISCARDED,
                    context.occurrence_node_ref(cdata_data.ordinal, NodeRole.XML_CDATA),
                    Fidelity.TRANSFORMED,
                )
        elif content.kind is XmlContentKind.COMMENT:
            comment_data = content.data
            context.event(
                ProjectionEventKind.COMMENT_DISCARDED,
                context.occurrence_node_ref(comment_data.ordinal, NodeRole.XML_COMMENT),
                Fidelity.TRANSFORMED,
            )
        elif content.kind is XmlContentKind.PROCESSING_INSTRUCTION:
            pi_data = content.data
            context.event(
                ProjectionEventKind.PROCESSING_INSTRUCTION_DISCARDED,
                context.occurrence_node_ref(
                    pi_data.ordinal, NodeRole.XML_PROCESSING_INSTRUCTION
                ),
                Fidelity.TRANSFORMED,
            )


class _EntrySet:
    """Ordered mapping entries with their expanded-name identities
    (projection.rs:91-113)."""

    __slots__ = ("ordered", "seen")

    def __init__(self) -> None:
        self.ordered: list[tuple[str, PortableValue]] = []
        self.seen: dict[str, tuple[int, object]] = {}


def _project_entry_mapping(
    context: _Context, request: ProjectionRequest
) -> tuple[PortableValue, Fidelity]:
    """Explicit-policy entry mapping of one selected subtree
    (projection.rs:1098-1126)."""
    root = context.document.root()
    if root is None:
        raise XmlProjectionFailure(
            XmlProjectionFailureKind.MAPPING_ADMISSION, reason="missing root"
        )
    start = root.index if request.subtree is None else request.subtree
    if context.document._nodes[start].kind is not XmlContentKind.ELEMENT:
        raise XmlProjectionFailure(XmlProjectionFailureKind.SUBTREE_NOT_ELEMENT)
    entries = _EntrySet()
    _map_children(context, start, ValuePath.root(), entries, request)
    return PortableValue.entry_mapping(
        [(PortableValue.string(key), value) for key, value in entries.ordered]
    ), Fidelity.TRANSFORMED


def _keep_from_repeated(policy: RepeatedChildPolicy) -> str:
    return {
        RepeatedChildPolicy.REJECT: "Reject",
        RepeatedChildPolicy.FIRST: "First",
        RepeatedChildPolicy.LAST: "Last",
    }[policy]


def _keep_from_collision(policy: CollisionPolicy) -> str:
    return {
        CollisionPolicy.REJECT: "Reject",
        CollisionPolicy.FIRST: "First",
        CollisionPolicy.LAST: "Last",
    }[policy]


def _entry_ordinal(
    context: _Context,
    entries: _EntrySet,
    key: str,
    candidate,
    request: ProjectionRequest,
    origin: NodeRef,
    collapse: ProjectionEventKind,
) -> int:
    """Resolves the entry ordinal under the explicit request policies
    (projection.rs:1144-1200)."""
    keep_repeated = _keep_from_repeated(request.repeated_child)
    keep_collision = _keep_from_collision(request.collision)
    seen = entries.seen.get(key)
    if seen is None:
        ordinal = len(entries.ordered)
        entries.seen[key] = (ordinal, candidate)
        return ordinal
    position, existing = seen
    repeated = existing is not None and candidate is not None and existing == candidate
    keep = keep_repeated if repeated else keep_collision
    if keep == "Reject":
        raise XmlProjectionFailure(XmlProjectionFailureKind.COLLISION, reason=key)
    event_kind = collapse if repeated else ProjectionEventKind.NAMESPACE_COLLAPSED
    context.event(event_kind, origin, Fidelity.TRANSFORMED)
    return position


def _commit_entry(
    context: _Context,
    entries: _EntrySet,
    key: str,
    value: PortableValue,
    ordinal: int,
    source: tuple[NodeRef, Span],
    container: ValuePath,
) -> None:
    """Records one committed entry and its value/association provenance
    (projection.rs:1202-1236)."""
    if ordinal < len(entries.ordered):
        entries.ordered[ordinal] = (key, value)
    else:
        entries.ordered.append((key, value))
    context.reserve_value(1)
    association = AssociationLocation(
        path=container, ordinal=ordinal, role=AssociationRole.ENTRY_MAPPING_ENTRY
    )
    context.origin(
        ProjectedLocation.of_association(association),
        source[0],
        source[1],
        ProvenanceRelation.DIRECT,
    )
    context.origin(
        ProjectedLocation.value(container.child(ValuePathSegment.entry_value(ordinal))),
        source[0],
        source[1],
        ProvenanceRelation.DIRECT,
    )


def _map_children(
    context: _Context,
    element: int,
    container: ValuePath,
    entries: _EntrySet,
    request: ProjectionRequest,
) -> None:
    """One subtree mapping walk (projection.rs:1238-1402)."""
    data = context.element_data(element)
    if data.namespaces:
        raise XmlProjectionFailure(
            XmlProjectionFailureKind.MAPPING_ADMISSION,
            reason="namespace declarations on the mapped element",
        )
    for attribute in data.attributes:
        origin = context.occurrence_node_ref(attribute.ordinal, NodeRole.XML_ATTRIBUTE)
        if request.attributes is AttributePolicy.REJECT_ATTRIBUTES:
            raise XmlProjectionFailure(
                XmlProjectionFailureKind.MAPPING_ADMISSION,
                reason="attributes present under RejectAttributes",
            )
        if request.attributes is AttributePolicy.IGNORE_ATTRIBUTES:
            context.event(
                ProjectionEventKind.ATTRIBUTE_DISCARDED, origin, Fidelity.TRANSFORMED
            )
        else:
            key = f"@{attribute.qname.local}"
            ordinal = _entry_ordinal(
                context,
                entries,
                key,
                None,
                request,
                origin,
                ProjectionEventKind.ATTRIBUTE_DISCARDED,
            )
            value = PortableValue.string(attribute.normalized_value)
            _commit_entry(
                context, entries, key, value, ordinal, (origin, attribute.span), container
            )
    for child in data.children:
        content = context.document._nodes[child]
        if content.kind is XmlContentKind.ELEMENT:
            child_data = content.data
            if child_data.expanded is not None:
                namespace = child_data.expanded.namespace or ""
                local = child_data.expanded.local
            else:
                namespace = ""
                local = child_data.qname.local
            key = {
                ExpandedNameKeyPolicy.LOCAL_ONLY: local,
                ExpandedNameKeyPolicy.PREFIXED_SPELLING: child_data.qname.qname().as_str,
                ExpandedNameKeyPolicy.URI_BRACKETED: f"{{{namespace}}}{local}",
            }[request.key_spelling]
            origin = context.element_node_ref(child)
            ordinal = _entry_ordinal(
                context,
                entries,
                key,
                child_data.expanded,
                request,
                origin,
                ProjectionEventKind.CHILD_COLLAPSED,
            )
            has_element_children = any(
                context.document._nodes[grandchild].kind is XmlContentKind.ELEMENT
                for grandchild in child_data.children
            )
            if has_element_children:
                nested_container = container.child(ValuePathSegment.entry_value(ordinal))
                nested = _EntrySet()
                _map_children(context, child, nested_container, nested, request)
                child_value = PortableValue.entry_mapping(
                    [
                        (PortableValue.string(nested_key), nested_value)
                        for nested_key, nested_value in nested.ordered
                    ]
                )
            else:
                child_value = _leaf_value(context, child, request)
            _commit_entry(
                context,
                entries,
                key,
                child_value,
                ordinal,
                (origin, child_data.span),
                container,
            )
        elif content.kind is XmlContentKind.TEXT:
            text_data = content.data
            if request.text_key is TextKeyPolicy.REJECT_TEXT:
                if text_semantic(text_data).strip():
                    raise XmlProjectionFailure(
                        XmlProjectionFailureKind.MAPPING_ADMISSION,
                        reason="text content under RejectText",
                    )
            else:
                context.event(
                    ProjectionEventKind.TEXT_DISCARDED,
                    context.occurrence_node_ref(text_data.ordinal, NodeRole.XML_TEXT),
                    Fidelity.TRANSFORMED,
                )
        elif content.kind is XmlContentKind.CDATA:
            cdata_data = content.data
            if request.text_key is TextKeyPolicy.REJECT_TEXT:
                raise XmlProjectionFailure(
                    XmlProjectionFailureKind.MAPPING_ADMISSION,
                    reason="CDATA content under RejectText",
                )
            context.event(
                ProjectionEventKind.CDATA_DISCARDED,
                context.occurrence_node_ref(cdata_data.ordinal, NodeRole.XML_CDATA),
                Fidelity.TRANSFORMED,
            )
        elif content.kind is XmlContentKind.COMMENT:
            comment_data = content.data
            context.event(
                ProjectionEventKind.COMMENT_DISCARDED,
                context.occurrence_node_ref(comment_data.ordinal, NodeRole.XML_COMMENT),
                Fidelity.TRANSFORMED,
            )
        elif content.kind is XmlContentKind.PROCESSING_INSTRUCTION:
            pi_data = content.data
            context.event(
                ProjectionEventKind.PROCESSING_INSTRUCTION_DISCARDED,
                context.occurrence_node_ref(
                    pi_data.ordinal, NodeRole.XML_PROCESSING_INSTRUCTION
                ),
                Fidelity.TRANSFORMED,
            )


def _leaf_value(
    context: _Context, element: int, request: ProjectionRequest
) -> PortableValue:
    """The leaf value of one element without element children
    (projection.rs:1405-1455)."""
    data = context.element_data(element)
    text_parts: list[str] = []
    for child in data.children:
        content = context.document._nodes[child]
        if content.kind is XmlContentKind.TEXT:
            text_parts.append(text_semantic(content.data))
        elif content.kind is XmlContentKind.CDATA:
            if request.text_key is TextKeyPolicy.REJECT_TEXT:
                raise XmlProjectionFailure(
                    XmlProjectionFailureKind.MAPPING_ADMISSION,
                    reason="CDATA content under RejectText",
                )
            context.event(
                ProjectionEventKind.CDATA_DISCARDED,
                context.occurrence_node_ref(content.data.ordinal, NodeRole.XML_CDATA),
                Fidelity.TRANSFORMED,
            )
    return PortableValue.string("".join(text_parts))
