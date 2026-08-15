"""HCL body projection: the `hcl.projection.body@1` record and the
`hcl.expression@1` ExtendedValue (RFC 0014 §8).

The default exact target is `hcl.projection.body@1`: one ordered body of
items, each an attribute (name string + value) or a block (type, ordered
labels, nested `hcl.body@1`), where every attribute value is
literal-complete and rendered as a typed member — string (exact code
points), integer or real (exact canonical decimal), boolean, null, tuple,
or object. Attribute order, block order, label order, and duplicate
object-constructor keys are preserved exactly.

A derived expression has no default rendering: projection of a body
containing a derived expression fails atomically with
`hcl.projection.non-literal-expression@1` unless the caller supplies the
explicit `ProjectExpression` policy; under that policy each derived
expression is projected as the authorized `hcl.expression@1` ExtendedValue
(kind family spelling, exact source text, structural fingerprint) with one
`Transformed` event per substituted expression (hard gate 4). A Recovered
Document never projects (RFC 0014 §8.2).

An object-constructor parenthesized key whose literal value is a tuple or
object has no canonical string spelling and fails the projection atomically
with `hcl.projection.unrepresentable@1` (object-key), never silently
rendered.

Authority (language-neutral first; Rust only for byte/registry
arbitration): https://github.com/consema/consema-rs/blob/main/consema-hcl/src/projection.rs — the record contract
projection.rs, the target and policy projection.rs, limits
projection.rs, failure algebra projection.rs, the
`hcl.expression@1` contract projection.rs, kind-family mapping
projection.rs, the body walk projection.rs, and the
provenance pre-order walk projection.rs.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.core.value import Decimal, PortableValue
from consema.document.structural import FormationStatus, NodeRef, SnapshotIdentity, Span
from consema.hcl.document import HclDocument
from consema.hcl.errors import (
    HclDiagnostic,
    HclProjectionFailure,
    HclProjectionFailureKind,
    HclSeverity,
)
from consema.hcl.expression import (
    HclExpression,
    HclExpressionKindName,
    HclLiteralValue,
    is_literal_complete,
    literal_value,
    structural_fingerprint_hex,
)
from consema.hcl.native import HclBody, HclBodyItem
from consema.protocol.error_registry import DiagnosticCategory

HCL_BODY_RECORD = "hcl.body@1"
HCL_EXPRESSION_RECORD = "hcl.expression@1"


class ProjectionTarget(enum.Enum):
    """Versioned HCL projection target (projection.rs)."""

    BODY_V1 = "hcl.projection.body@1"


class ExpressionPolicy(enum.Enum):
    """Derived-expression handling for the body target (RFC 0014 §8.2;
    projection.rs)."""

    FAIL = "Fail"
    PROJECT_EXPRESSION = "ProjectExpression"


@dataclass(frozen=True, slots=True)
class ProjectionLimits:
    """HCL projection resource limits (projection.rs)."""

    max_source_nodes: int = 2_000_000
    max_value_nodes: int = 2_000_000
    max_report_entries: int = 100_000
    max_provenance_units: int = 4_000_000


@dataclass(frozen=True, slots=True)
class ProjectionRequest:
    """Explicit HCL projection request; every policy is mandatory
    (projection.rs)."""

    target: ProjectionTarget = ProjectionTarget.BODY_V1
    expression_policy: ExpressionPolicy = ExpressionPolicy.FAIL
    limits: ProjectionLimits = field(default_factory=ProjectionLimits)

    @classmethod
    def body(cls) -> ProjectionRequest:
        """Exact `hcl.projection.body@1` record request; a derived
        expression fails the projection atomically (projection.rs)."""
        return cls()

    @classmethod
    def body_with_expression_policy(cls, policy: ExpressionPolicy) -> ProjectionRequest:
        """Exact body request with an explicit derived-expression policy
        (projection.rs)."""
        return cls(expression_policy=policy)


class Fidelity(enum.Enum):
    """Projection fidelity classification (projection.rs)."""

    EXACT = "Exact"
    TRANSFORMED = "Transformed"
    LOSSY = "Lossy"


class ProvenanceRelation(enum.Enum):
    """Source-to-projection relation (projection.rs)."""

    DIRECT = "Direct"
    DERIVED = "Derived"
    COLLAPSED = "Collapsed"
    REFERENCE_DERIVED = "ReferenceDerived"


@dataclass(frozen=True, slots=True)
class SourceOrigin:
    """One exact source origin (projection.rs)."""

    snapshot: SnapshotIdentity
    node: NodeRef
    span: Span
    relation: ProvenanceRelation


@dataclass(frozen=True, slots=True)
class ProvenanceEntry:
    """One many-valued provenance entry: one projected record location and
    its ordered source origins (projection.rs)."""

    projected: ValuePath
    origins: tuple[SourceOrigin, ...]


@dataclass(frozen=True, slots=True)
class ProvenanceMap:
    """Immutable many-valued provenance mapping (projection.rs)."""

    entries: tuple[ProvenanceEntry, ...] = ()


class ProjectionEventKind(enum.Enum):
    """Projection report category (projection.rs)."""

    EXPRESSION_SUBSTITUTED = "ExpressionSubstituted"


@dataclass(frozen=True, slots=True)
class ProjectionEvent:
    """One explicit transformation event (projection.rs)."""

    kind: ProjectionEventKind
    expression: NodeRef
    value: ValuePath
    impact: Fidelity


@dataclass(frozen=True, slots=True)
class ProjectionReport:
    """Complete ordered projection report (projection.rs)."""

    events: tuple[ProjectionEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class CompleteProjection:
    """Complete successful projection (projection.rs)."""

    value: PortableValue
    fidelity: Fidelity
    report: ProjectionReport
    provenance: ProvenanceMap


@dataclass(frozen=True, slots=True)
class FailedProjectionAttempt:
    """Failed projection attempt without a partial value
    (projection.rs)."""

    diagnostics: tuple[HclDiagnostic, ...]
    report: ProjectionReport


ProjectionResult = CompleteProjection | FailedProjectionAttempt


# -- portable input locations (RFC 0004 §8; the semantic-model records) -----


class ValuePathSegmentKind(enum.Enum):
    """Value-path segment kinds of the semantic model (RFC 0004 §8)."""

    SEQUENCE_ELEMENT = "SequenceElement"
    OBJECT_VALUE = "ObjectValue"
    ENTRY_KEY = "EntryKey"
    ENTRY_VALUE = "EntryValue"


@dataclass(frozen=True, slots=True)
class ValuePathSegment:
    """One value-path segment."""

    kind: ValuePathSegmentKind
    key: object  # int ordinal or str name


@dataclass(frozen=True, slots=True)
class ValuePath:
    """Portable input value path (ValuePath, semantic model)."""

    segments: tuple[ValuePathSegment, ...] = ()

    @classmethod
    def root(cls) -> ValuePath:
        return cls()

    def child(self, segment: ValuePathSegment) -> ValuePath:
        return ValuePath(self.segments + (segment,))

    def __repr__(self) -> str:
        if not self.segments:
            return "Root"
        return "Root/" + "/".join(
            f"{segment.kind.value}({segment.key})" for segment in self.segments
        )


class _Context:
    def __init__(self, document: HclDocument, request: ProjectionRequest) -> None:
        self.document = document
        self.request = request
        self.report: list[ProjectionEvent] = []
        self.provenance: list[ProvenanceEntry] = []
        self.fidelity = Fidelity.EXACT
        self.source_nodes = 0
        self.value_nodes = 0

    def step(self) -> None:
        self.source_nodes += 1
        if self.source_nodes > self.request.limits.max_source_nodes:
            raise HclProjectionFailure(
                HclProjectionFailureKind.RESOURCE_LIMIT,
                resource_name="max_source_nodes",
            )

    def reserve_value(self, count: int) -> None:
        self.value_nodes += count
        if self.value_nodes > self.request.limits.max_value_nodes:
            raise HclProjectionFailure(
                HclProjectionFailureKind.RESOURCE_LIMIT,
                resource_name="max_value_nodes",
            )

    def event(self, expression: NodeRef, value: ValuePath) -> None:
        if len(self.report) >= self.request.limits.max_report_entries:
            raise HclProjectionFailure(
                HclProjectionFailureKind.RESOURCE_LIMIT,
                resource_name="max_report_entries",
            )
        self.report.append(
            ProjectionEvent(
                kind=ProjectionEventKind.EXPRESSION_SUBSTITUTED,
                expression=expression,
                value=value,
                impact=Fidelity.TRANSFORMED,
            )
        )
        self.fidelity = Fidelity.TRANSFORMED

    def item_span(self, item: HclBodyItem) -> Span:
        """One item's origin span: the name-through-expression span of an
        attribute, the whole block span of a block (materialization.rs
        provenance contract)."""
        attribute = item.as_attribute()
        if attribute is not None:
            return self.document.authority.span(
                attribute.name_span.start_byte, attribute.expression.span.end_byte
            )
        return item.as_block().span

    def origin(self, projected: ValuePath, node: NodeRef, span: Span) -> None:
        if len(self.provenance) >= self.request.limits.max_provenance_units:
            raise HclProjectionFailure(
                HclProjectionFailureKind.RESOURCE_LIMIT,
                resource_name="max_provenance_units",
            )
        self.provenance.append(
            ProvenanceEntry(
                projected=projected,
                origins=(
                    SourceOrigin(
                        snapshot=self.document.snapshot_identity(),
                        node=node,
                        span=span,
                        relation=ProvenanceRelation.DIRECT,
                    ),
                ),
            )
        )

    # -- record walk --------------------------------------------------------

    def project_body_record(self) -> PortableValue:
        items_path = ValuePath.root().child(
            ValuePathSegment(ValuePathSegmentKind.OBJECT_VALUE, "items")
        )
        item_values: list[PortableValue] = []
        for ordinal, item in enumerate(self.document.body.items):
            self.step()
            item_path = items_path.child(
                ValuePathSegment(ValuePathSegmentKind.SEQUENCE_ELEMENT, ordinal)
            )
            self.origin(item_path, self.document.node_ref(_item_node(item)), self.item_span(item))
            if item.as_attribute() is not None:
                item_values.append(self.project_attribute(item.as_attribute(), item_path))
            else:
                item_values.append(self.project_block(item.as_block(), item_path))
        self.reserve_value(1)
        return PortableValue.object(
            (
                ("record", PortableValue.string(HCL_BODY_RECORD)),
                ("items", PortableValue.sequence(item_values)),
            )
        )

    def project_attribute(self, attribute, item_path: ValuePath) -> PortableValue:
        self.step()
        value_path = item_path.child(ValuePathSegment(ValuePathSegmentKind.OBJECT_VALUE, "value"))
        value = self.project_value(attribute.expression, value_path)
        self.reserve_value(2)
        return PortableValue.object(
            (
                ("kind", PortableValue.string("attribute")),
                ("name", PortableValue.string(attribute.name)),
                ("value", value),
            )
        )

    def project_block(self, block, item_path: ValuePath) -> PortableValue:
        self.step()
        label_values = []
        for label in block.labels:
            self.step()
            label_values.append(PortableValue.string(label.text))
        body_path = item_path.child(ValuePathSegment(ValuePathSegmentKind.OBJECT_VALUE, "body"))
        body_value = self.project_nested_body(block.body, body_path)
        self.reserve_value(3)
        return PortableValue.object(
            (
                ("kind", PortableValue.string("block")),
                ("type", PortableValue.string(block.block_type)),
                ("labels", PortableValue.sequence(label_values)),
                ("body", body_value),
            )
        )

    def project_nested_body(self, body: HclBody, body_path: ValuePath) -> PortableValue:
        items_path = body_path.child(
            ValuePathSegment(ValuePathSegmentKind.OBJECT_VALUE, "items")
        )
        item_values: list[PortableValue] = []
        for ordinal, item in enumerate(body.items):
            self.step()
            item_path = items_path.child(
                ValuePathSegment(ValuePathSegmentKind.SEQUENCE_ELEMENT, ordinal)
            )
            self.origin(item_path, self.document.node_ref(_item_node(item)), self.item_span(item))
            if item.as_attribute() is not None:
                item_values.append(self.project_attribute(item.as_attribute(), item_path))
            else:
                item_values.append(self.project_block(item.as_block(), item_path))
        self.reserve_value(1)
        return PortableValue.object(
            (
                ("record", PortableValue.string(HCL_BODY_RECORD)),
                ("items", PortableValue.sequence(item_values)),
            )
        )

    def project_value(self, expression: HclExpression, path: ValuePath) -> PortableValue:
        self.step()
        self.reserve_value(1)
        self.origin(path, self.document.node_ref(expression), expression.span)
        if not is_literal_complete(expression):
            if self.request.expression_policy is ExpressionPolicy.FAIL:
                raise HclProjectionFailure(
                    HclProjectionFailureKind.NON_LITERAL_EXPRESSION,
                    text=expression.text(self.document.source),
                )
            self.event(self.document.node_ref(expression), path)
            # Under the ProjectExpression policy each derived expression is
            # the authorized `hcl.expression@1` record itself, not a
            # {kind, expression} wrapper (https://github.com/consema/consema-go/blob/main/go/hcl/projection.go;
            # hcl-v1.json hcl.projection.project-expression-policy).
            return self.expression_record(expression)
        literal = literal_value(expression)
        return _literal_to_value(literal)

    def expression_record(self, expression: HclExpression) -> PortableValue:
        """The authorized `hcl.expression@1` ExtendedValue record (RFC
        0014 §8.2): kind family spelling, exact source text, structural
        fingerprint (projection.rs)."""
        self.reserve_value(4)
        return PortableValue.object(
            (
                ("record", PortableValue.string(HCL_EXPRESSION_RECORD)),
                ("kind", PortableValue.string(expression.kind.kind_family())),
                ("text", PortableValue.string(expression.text(self.document.source))),
                (
                    "fingerprint",
                    PortableValue.string(structural_fingerprint_hex(expression)),
                ),
            )
        )


def _item_node(item: HclBodyItem):
    attribute = item.as_attribute()
    if attribute is not None:
        return attribute
    return item.as_block()


def _attribute_span(attribute) -> Span:
    """The attribute's full source range: the union of the name, equals,
    and expression spans (RFC 0014 §6)."""
    return attribute.expression.span


def _int_decimal(text: str) -> int:
    """Exact decimal-string to int, immune to the interpreter's int()
    string-conversion limit (CPython default 4300 digits; the formation
    magnitude bound above it already passed, so digits are 0-9).

    The fallback chunks 4 digits at a time; the leading chunk carries the
    ``len % 4`` remainder so every digit keeps its exact place value for
    any length, not only multiples of four."""
    negative = text.startswith("-")
    digits = text[1:] if text[:1] in ("+", "-") else text
    try:
        value = int(digits)
    except ValueError:
        start = len(digits) % 4
        value = int(digits[:start]) if start else 0
        for index in range(start, len(digits), 4):
            value = value * 10_000 + int(digits[index : index + 4])
    return -value if negative else value


def _literal_to_value(literal: HclLiteralValue) -> PortableValue:
    kind = literal.kind
    if kind == "integer":
        return PortableValue.integer(_int_decimal(literal.text))
    if kind == "real":
        return _decimal_value(literal.text)
    if kind == "string":
        return PortableValue.string(literal.text)
    if kind == "boolean":
        return PortableValue.boolean(literal.flag)
    if kind == "null":
        return PortableValue.null()
    if kind == "tuple":
        return PortableValue.sequence(tuple(_literal_to_value(element) for element in literal.elements))
    entries = []
    for entry in literal.entries:
        key = _literal_key_text(entry.key)
        entries.append((PortableValue.string(key), _literal_to_value(entry.value)))
    return PortableValue.entry_mapping(entries)


def _literal_key_text(key) -> str:
    if key.kind == "identifier":
        return key.text
    if key.kind == "number":
        return key.text
    if key.kind == "string":
        return key.text
    value = key.value
    if value.kind in ("integer", "real"):
        return value.text
    if value.kind == "boolean":
        return "true" if value.flag else "false"
    if value.kind == "null":
        return "null"
    if value.kind == "string":
        return value.text
    raise HclProjectionFailure(
        HclProjectionFailureKind.UNREPRESENTABLE, fact="object-key"
    )


def _decimal_value(canonical: str) -> PortableValue:
    """One canonical decimal spelling as the exact core Decimal
    (coefficient × 10^exponent)."""
    negative = canonical.startswith("-")
    unsigned = canonical[1:] if negative else canonical
    if "." in unsigned:
        whole, fraction = unsigned.split(".", 1)
        coefficient = _int_decimal(whole + fraction)
        exponent = -len(fraction)
    else:
        coefficient = _int_decimal(unsigned)
        exponent = 0
    if negative and coefficient != 0:
        coefficient = -coefficient
    return PortableValue.decimal(Decimal(coefficient, exponent))


def project(document: HclDocument, request: ProjectionRequest) -> ProjectionResult:
    """Projects one complete HCL document under one explicit target and
    policy contract (RFC 0014 §8; projection.rs).

    The projection is atomic: a recovered source, a derived expression
    under the default policy, an unrepresentable native fact, or a resource
    limit returns no partial value, provenance, or report (hard gate 4). A
    Recovered Document never projects (RFC 0014 §8.2).
    """
    if document.formation_status() is not FormationStatus.COMPLETE:
        return _failed(HclProjectionFailureKind.INCOMPLETE_DOCUMENT)
    context = _Context(document, request)
    try:
        value = context.project_body_record()
    except HclProjectionFailure as failure:
        return _failed_with(failure)
    return CompleteProjection(
        value=value,
        fidelity=context.fidelity,
        report=ProjectionReport(events=tuple(context.report)),
        provenance=ProvenanceMap(entries=tuple(context.provenance)),
    )


def _failed(kind: HclProjectionFailureKind) -> FailedProjectionAttempt:
    return FailedProjectionAttempt(
        diagnostics=(
            HclDiagnostic(
                code=HclProjectionFailure(kind).code,
                category=DiagnosticCategory.PROJECTION,
                severity=HclSeverity.ERROR,
                primary=None,
            ),
        ),
        report=ProjectionReport(),
    )


def _failed_with(failure: HclProjectionFailure) -> FailedProjectionAttempt:
    return FailedProjectionAttempt(
        diagnostics=(
            HclDiagnostic(
                code=failure.code,
                category=DiagnosticCategory.PROJECTION,
                severity=HclSeverity.ERROR,
                primary=None,
            ),
        ),
        report=ProjectionReport(),
    )
