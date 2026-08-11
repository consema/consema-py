"""Registered-payload validation dispatch for the protocol envelope.

Authority: crates/consema-protocol/src/payload.rs (validate_registered_payload).
The record codecs are transcribed from the Rust protocol records modules
(execution.rs, projection.rs, query.rs, change.rs, source.rs,
materialization.rs, conversion.rs, operation.rs, portable_graph.rs,
graph_projection.rs, graph_query.rs, line_query.rs, java_utf16.rs,
yaml_query.rs); Go (go/protocol/payload.go and the records_*.go files) is a
cross-reference only.

Every registered contract of the semantic-model v1-v7 surface is validated
with its full record decoder, so a malformed payload (unknown field, wrong
type, contradictory facts) is rejected at the envelope level instead of
passing at the schema discriminator. The decoders never trust the
discriminator: they re-verify the exact fixed field sets and the cross
constraints (completion state invariants, projection rule conflicts,
provenance ordering, digest/status reconciliation, patch budgets, version
dispatch).
"""

from __future__ import annotations

import enum

from consema.core.value import Kind, PortableValue
from consema.core.value import equal as core_equal
from consema.document.ids import ContentDigest
from consema.document.source import (
    BomKind,
    BomPolicy,
    EncodingFacts,
    EncodingRequest,
    SourceEncoding,
    SourceEncodingKind,
    SourceError,
    SourceErrorKind,
    SourceLimits,
    SourceSnapshot,
    WindowsCodePage,
)
from consema.document.source_patch import (
    SourcePatch,
    SourcePatchLimits,
    SourceReplacement,
)
from consema.graph.graph import GraphBuilder, GraphBuildError, GraphMappingEntry
from consema.graph.pgce import (
    PgceDecodeError,
    PgceLimits,
    decode_pgce,
    encode_pgce,
    encode_pgce_bounded,
)
from consema.properties.java_string import JavaString, JavaStringStatus
from consema.protocol.cli import (
    BatchPlanMessage,
    BatchResultMessage,
    CliOutputMessage,
)
from consema.protocol.contract import ContractId, ContractRegistry
from consema.protocol.diagnostic import Diagnostic
from consema.protocol.error_registry import (
    ErrorCodeRegistry,
    validate_error_code_manifest_value,
)
from consema.protocol.errors import (
    ProtocolError,
    ProtocolErrorKind,
    invalid,
    protocol_error,
    resource,
)
from consema.protocol.limits import ProtocolLimits
from consema.protocol.query import MatchRole, QueryDomain, QueryDefinitionCodec
from consema.protocol.registry_descriptor import (
    CapabilityDeclaration,
    ProfileDescriptor,
    ProfileReference,
    RegistryManifest,
)
from consema.protocol.schema import (
    boolean_of,
    exact_fields,
    nullable_string,
    optional_string,
    schema_fields,
    sequence_of,
    signed32,
    string_map_from_object,
    string_map_object,
    string_of,
    unsigned32,
    unsigned64,
)


# ---------------------------------------------------------------------------
# core.completion@1, core.cancellation-request@1, core.execution-policy@1
# (execution.rs)
# ---------------------------------------------------------------------------


class CompletionStatus(enum.Enum):
    SUCCESS = "Success"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    RESOURCE_LIMITED = "ResourceLimited"
    UNSUPPORTED = "Unsupported"
    NOT_APPLICABLE = "NotApplicable"


class Completion:
    """The ``core.completion@1`` control-flow facts record (execution.rs:40-49)."""

    __slots__ = ("status", "processed", "produced", "limit_name", "failure_code")

    def __init__(self, status, processed, produced, limit_name=None, failure_code=None):
        self.status = status
        self.processed = processed
        self.produced = produced
        self.limit_name = limit_name
        self.failure_code = failure_code

    @classmethod
    def from_value_with_registry(cls, value: PortableValue, registry: ErrorCodeRegistry) -> "Completion":
        """Validates the state-specific completion invariants against one
        explicit semantic-model registry (execution.rs:51-67)."""
        fields = schema_fields(
            value,
            "core.completion@1",
            ["schema", "status", "processed", "produced", "limit_name", "failure_code"],
            "$",
        )
        status = _parse_completion_status(fields[1], "$.status")
        processed = unsigned64(fields[2], "$.processed")
        produced = unsigned64(fields[3], "$.produced")
        limit_name = optional_string(fields[4], "$.limit_name")
        failure_code = optional_string(fields[5], "$.failure_code")
        if failure_code is not None:
            registry.validate(failure_code, "$.failure_code")
        valid = False
        if status in (CompletionStatus.SUCCESS, CompletionStatus.CANCELLED):
            valid = limit_name is None and failure_code is None
        elif status is CompletionStatus.RESOURCE_LIMITED:
            valid = limit_name is not None and limit_name != "" and failure_code is None
        elif status in (
            CompletionStatus.FAILED,
            CompletionStatus.UNSUPPORTED,
            CompletionStatus.NOT_APPLICABLE,
        ):
            valid = limit_name is None and failure_code is not None and failure_code != ""
        if not valid:
            raise invalid("$", "completion status contradicts limit/failure fields")
        return cls(status, processed, produced, limit_name, failure_code)


def _parse_completion_status(value: PortableValue, path: str) -> CompletionStatus:
    text = string_of(value, path)
    try:
        return CompletionStatus(text)
    except ValueError:
        raise invalid(path, "unknown completion status") from None


class ExecutionPolicy:
    """The transferable ``core.execution-policy@1`` record (execution.rs:189-195)."""

    __slots__ = ("limits", "cancellation_request_id")

    def __init__(self, limits, cancellation_request_id=None):
        self.limits = dict(limits)
        self.cancellation_request_id = cancellation_request_id

    @classmethod
    def from_value(cls, value: PortableValue) -> "ExecutionPolicy":
        fields = schema_fields(
            value,
            "core.execution-policy@1",
            ["schema", "limits", "cancellation_request_id"],
            "$",
        )
        limits = {}
        if fields[1].kind is not Kind.OBJECT:
            raise protocol_error(ProtocolErrorKind.WRONG_TYPE, "$.limits", "expected Object<String, Integer>")
        for key, item in fields[1].as_object():
            limits[key] = unsigned64(item, "$.limits." + key)
        for name in limits:
            if not _valid_limit_name(name):
                raise invalid("$.limits", "limit names must be stable lowercase identifiers")
        cancellation_id = optional_string(fields[2], "$.cancellation_request_id")
        if cancellation_id is not None and (
            cancellation_id == "" or len(cancellation_id) > 1024
        ):
            raise invalid("$.cancellation_request_id", "invalid cancellation request ID")
        return cls(limits, cancellation_id)


class CancellationRequest:
    """The idempotent outer-transport ``core.cancellation-request@1`` record
    (execution.rs:279-290)."""

    __slots__ = ("request_id", "reason")

    def __init__(self, request_id: str, reason=None):
        self.request_id = request_id
        self.reason = reason

    @classmethod
    def from_value(cls, value: PortableValue) -> "CancellationRequest":
        fields = schema_fields(
            value,
            "core.cancellation-request@1",
            ["schema", "request_id", "reason"],
            "$",
        )
        request_id = string_of(fields[1], "$.request_id")
        reason = optional_string(fields[2], "$.reason")
        if not request_id or len(request_id) > 1024:
            raise invalid("$.request_id", "invalid request ID")
        return cls(request_id, reason)


def _valid_limit_name(name: str) -> bool:
    if not name or len(name) > 255:
        return False
    return all(
        ("a" <= character <= "z") or ("0" <= character <= "9") or character in "_"
        for character in name
    )


# ---------------------------------------------------------------------------
# value paths and association locations
# ---------------------------------------------------------------------------


class ValuePathSegmentKind(enum.Enum):
    SEQUENCE_ELEMENT = "SequenceElement"
    OBJECT_VALUE = "ObjectValue"
    ENTRY_KEY = "EntryKey"
    ENTRY_VALUE = "EntryValue"


class ValuePath:
    """The portable value path wire record (records_valuepath.go)."""

    __slots__ = ("segments",)

    def __init__(self, segments=()):
        self.segments = tuple(segments)

    @classmethod
    def from_value(cls, value: PortableValue) -> "ValuePath":
        fields = schema_fields(value, "core.value-path@1", ["schema", "segments"], "$")
        segments = []
        for item in sequence_of(fields[1], "$.segments"):
            segment = exact_fields(item, ["kind", "key"], "$.segments")
            kind = string_of(segment[0], "$.segments.kind")
            if segment[1].kind is Kind.INTEGER:
                key = segment[1].as_integer()
            elif segment[1].kind is Kind.STRING:
                key = segment[1].as_string()
            else:
                raise protocol_error(ProtocolErrorKind.WRONG_TYPE, "$.segments.key", "expected Integer or String")
            segments.append((kind, key))
        return cls(segments)


class AssociationRole(enum.Enum):
    OBJECT_ENTRY = "ObjectEntry"
    OBJECT_KEY = "ObjectKey"
    ENTRY_MAPPING_ENTRY = "EntryMappingEntry"


class AssociationLocation:
    """The portable association location wire record."""

    __slots__ = ("path", "ordinal", "role")

    def __init__(self, path: ValuePath, ordinal: int, role: AssociationRole):
        self.path = path
        self.ordinal = ordinal
        self.role = role

    @classmethod
    def from_value(cls, value: PortableValue) -> "AssociationLocation":
        fields = schema_fields(
            value,
            "core.association-location@1",
            ["schema", "path", "ordinal", "role"],
            "$",
        )
        path = ValuePath.from_value(fields[1])
        ordinal = unsigned64(fields[2], "$.ordinal")
        role_text = string_of(fields[3], "$.role")
        try:
            role = AssociationRole(role_text)
        except ValueError:
            raise invalid("$.role", "unknown association role") from None
        return cls(path, ordinal, role)


# ---------------------------------------------------------------------------
# core.projection-request@1 (projection.rs)
# ---------------------------------------------------------------------------


class ProjectionPolicy:
    __slots__ = ("contract", "arguments")

    def __init__(self, contract: ContractId, arguments):
        self.contract = contract
        self.arguments = dict(arguments)

    @classmethod
    def from_value(cls, value: PortableValue, path: str) -> "ProjectionPolicy":
        fields = exact_fields(value, ["id", "version", "arguments"], path)
        identifier = string_of(fields[0], path + ".id")
        version = unsigned32(fields[1], path + ".version")
        if fields[2].kind is not Kind.OBJECT:
            raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path + ".arguments", "expected Object")
        arguments = dict(fields[2].as_object())
        return cls(ContractId(identifier, version), arguments)


class ProjectionScope:
    __slots__ = ("kind", "source_id", "path", "query")

    def __init__(self, kind: str, source_id: str = "", path: str = "", query=None):
        self.kind = kind
        self.source_id = source_id
        self.path = path
        self.query = query

    @classmethod
    def from_value(cls, value: PortableValue, path: str) -> "ProjectionScope":
        if value.kind is not Kind.OBJECT or not value.as_object():
            raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected scope Object")
        entries = value.as_object()
        if entries[0][0] != "kind":
            raise invalid(path, "scope kind must be first")
        kind = string_of(entries[0][1], path + ".kind")
        if kind == "Global":
            exact_fields(value, ["kind"], path)
            return cls("Global")
        if kind == "ExactNativePath":
            fields = exact_fields(value, ["kind", "source_id", "path"], path)
            scope = cls(
                "ExactNativePath",
                string_of(fields[1], path + ".source_id"),
                string_of(fields[2], path + ".path"),
            )
        elif kind == "ResolvedQuery":
            fields = exact_fields(value, ["kind", "query"], path)
            scope = cls("ResolvedQuery", query=QueryDefinitionCodec.from_value(fields[1]))
        else:
            raise invalid(path, "unknown projection scope")
        if scope.kind == "ExactNativePath":
            if (
                not scope.source_id
                or len(scope.source_id) > 1024
                or not scope.path
                or len(scope.path) > 4096
            ):
                raise invalid("$.scope", "invalid exact native path scope")
        elif scope.kind == "ResolvedQuery":
            if scope.query is None:
                raise invalid("$.scope.query", "invalid query scope")
            scope.query.validate()
        return scope


class ProjectionRule:
    __slots__ = ("rule_id", "scope", "priority", "policy")

    def __init__(self, rule_id: str, scope: ProjectionScope, priority: int, policy: ProjectionPolicy):
        self.rule_id = rule_id
        self.scope = scope
        self.priority = priority
        self.policy = policy

    @classmethod
    def from_value(cls, value: PortableValue, path: str) -> "ProjectionRule":
        fields = exact_fields(value, ["rule_id", "scope", "priority", "policy"], path)
        rule_id = string_of(fields[0], path + ".rule_id")
        scope = ProjectionScope.from_value(fields[1], path + ".scope")
        priority = signed32(fields[2], path + ".priority")
        policy = ProjectionPolicy.from_value(fields[3], path + ".policy")
        return cls(rule_id, scope, priority, policy)


class ProjectionRequestMessage:
    """The ``core.projection-request@1`` record (projection.rs:89-97)."""

    __slots__ = ("target", "default_policy", "rules", "limits")

    def __init__(self, target: ContractId, default_policy: ProjectionPolicy, rules, limits):
        self.target = target
        self.default_policy = default_policy
        self.rules = rules
        self.limits = dict(limits)

    @classmethod
    def from_value(cls, value: PortableValue) -> "ProjectionRequestMessage":
        fields = schema_fields(
            value,
            "core.projection-request@1",
            ["schema", "target", "default_policy", "rules", "limits"],
            "$",
        )
        target = _parse_reference(fields[1], "$.target")
        default_policy = ProjectionPolicy.from_value(fields[2], "$.default_policy")
        rules = [
            ProjectionRule.from_value(item, f"$.rules[{index}]")
            for index, item in enumerate(sequence_of(fields[3], "$.rules"))
        ]
        limits = {}
        if fields[4].kind is not Kind.OBJECT:
            raise protocol_error(ProtocolErrorKind.WRONG_TYPE, "$.limits", "expected Object<String, Integer>")
        for key, item in fields[4].as_object():
            limits[key] = unsigned64(item, "$.limits." + key)
        rule_ids = set()
        for rule in rules:
            if not rule.rule_id or len(rule.rule_id) > 255 or rule.rule_id in rule_ids:
                raise invalid("$.rules", "rule IDs must be non-empty and unique")
            rule_ids.add(rule.rule_id)
        for index, left in enumerate(rules):
            for right in rules[index + 1:]:
                if (
                    left.priority == right.priority
                    and _scope_equal(left.scope, right.scope)
                    and not _policy_equal(left.policy, right.policy)
                ):
                    raise invalid("$.rules", "same-scope same-priority policies conflict")
        for name in limits:
            if not _valid_limit_name(name):
                raise invalid("$.limits", "limit names must be stable lowercase identifiers")
        return cls(target, default_policy, rules, limits)


def _parse_reference(value: PortableValue, path: str) -> ContractId:
    fields = exact_fields(value, ["id", "version"], path)
    identifier = string_of(fields[0], path + ".id")
    version = unsigned32(fields[1], path + ".version")
    return ContractId(identifier, version)


def _scope_equal(left: ProjectionScope, right: ProjectionScope) -> bool:
    if left.kind != right.kind:
        return False
    if left.kind == "Global":
        return True
    if left.kind == "ExactNativePath":
        return left.source_id == right.source_id and left.path == right.path
    if left.kind == "ResolvedQuery":
        if left.query is None or right.query is None:
            return left.query is right.query
        return core_equal(
            QueryDefinitionCodec.to_value(left.query),
            QueryDefinitionCodec.to_value(right.query),
        )
    return False


def _policy_equal(left: ProjectionPolicy, right: ProjectionPolicy) -> bool:
    if left.contract != right.contract or len(left.arguments) != len(right.arguments):
        return False
    for name, value in left.arguments.items():
        if name not in right.arguments or not core_equal(value, right.arguments[name]):
            return False
    return True


# ---------------------------------------------------------------------------
# core.projection-report@1, core.projection-result@1, core.provenance-map@1
# (projection.rs)
# ---------------------------------------------------------------------------


class ProvenanceRelation(enum.Enum):
    DIRECT = "Direct"
    DERIVED = "Derived"
    EXPANDED = "Expanded"
    MERGED = "Merged"
    GENERATED = "Generated"


class SourceOriginMessage:
    __slots__ = ("source_id", "node_locator", "start_byte", "end_byte", "relation")

    def __init__(self, source_id, node_locator, start_byte, end_byte, relation):
        self.source_id = source_id
        self.node_locator = node_locator
        self.start_byte = start_byte
        self.end_byte = end_byte
        self.relation = relation

    @classmethod
    def from_value(cls, value: PortableValue, path: str) -> "SourceOriginMessage":
        fields = exact_fields(
            value,
            ["source_id", "node_locator", "start_byte", "end_byte", "relation"],
            path,
        )
        source_id = string_of(fields[0], path + ".source_id")
        node_locator = optional_string(fields[1], path + ".node_locator")
        start_byte = unsigned64(fields[2], path + ".start_byte")
        end_byte = unsigned64(fields[3], path + ".end_byte")
        relation_text = string_of(fields[4], path + ".relation")
        try:
            relation = ProvenanceRelation(relation_text)
        except ValueError:
            raise invalid(path + ".relation", "unknown provenance relation") from None
        if (
            not source_id
            or len(source_id) > 1024
            or start_byte > end_byte
            or (node_locator is not None and (node_locator == "" or len(node_locator) > 4096))
        ):
            raise invalid("$.origin", "invalid source identity, locator, or range")
        return cls(source_id, node_locator, start_byte, end_byte, relation)


class ProjectedLocationMessage:
    __slots__ = ("kind", "path", "association")

    def __init__(self, kind: str, path: ValuePath | None = None, association: AssociationLocation | None = None):
        self.kind = kind
        self.path = path
        self.association = association

    @classmethod
    def from_value(cls, value: PortableValue, path: str) -> "ProjectedLocationMessage":
        fields = exact_fields(value, ["kind", "value"], path)
        kind = string_of(fields[0], path + ".kind")
        if kind == "ValuePath":
            return cls("ValuePath", path=ValuePath.from_value(fields[1]))
        if kind == "AssociationLocation":
            return cls("AssociationLocation", association=AssociationLocation.from_value(fields[1]))
        raise invalid(path, "unknown projected location")


class ProvenanceEntryMessage:
    __slots__ = ("projected", "origins")

    def __init__(self, projected: ProjectedLocationMessage, origins):
        self.projected = projected
        self.origins = origins


class ProvenanceMapMessage:
    """The sorted unique ``core.provenance-map@1`` record (projection.rs:321-326)."""

    __slots__ = ("entries",)

    def __init__(self, entries):
        self.entries = entries

    @classmethod
    def from_value(cls, value: PortableValue) -> "ProvenanceMapMessage":
        fields = schema_fields(value, "core.provenance-map@1", ["schema", "entries"], "$")
        entries = []
        for index, entry_value in enumerate(sequence_of(fields[1], "$.entries")):
            path = f"$.entries[{index}]"
            entry_fields = exact_fields(entry_value, ["projected", "origins"], path)
            projected = ProjectedLocationMessage.from_value(entry_fields[0], path + ".projected")
            origins = [
                SourceOriginMessage.from_value(item, path + ".origins")
                for item in sequence_of(entry_fields[1], path + ".origins")
            ]
            entries.append(ProvenanceEntryMessage(projected, origins))
        for entry in entries:
            if not entry.origins:
                raise invalid("$.entries", "provenance locations must be sorted, unique, and have origins")
        for index in range(1, len(entries)):
            if not _projected_less(entries[index - 1].projected, entries[index].projected):
                raise invalid("$.entries", "provenance locations must be sorted, unique, and have origins")
        return cls(entries)


def _projected_less(left: ProjectedLocationMessage, right: ProjectedLocationMessage) -> bool:
    if left.kind != right.kind:
        return left.kind < right.kind
    if left.kind == "AssociationLocation":
        return _association_less(left.association, right.association)
    return _path_less(left.path, right.path)


def _path_less(left: ValuePath, right: ValuePath) -> bool:
    for left_segment, right_segment in zip(left.segments, right.segments):
        if left_segment[0] != right_segment[0]:
            return left_segment[0] < right_segment[0]
        if left_segment[1] != right_segment[1]:
            left_key = left_segment[1]
            right_key = right_segment[1]
            if isinstance(left_key, int) and isinstance(right_key, int):
                return left_key < right_key
            return str(left_key) < str(right_key)
    return len(left.segments) < len(right.segments)


def _association_less(left: AssociationLocation, right: AssociationLocation) -> bool:
    if not _path_less(left.path, right.path) and not _path_less(right.path, left.path):
        if left.ordinal != right.ordinal:
            return left.ordinal < right.ordinal
        return left.role.value < right.role.value
    return _path_less(left.path, right.path)


class LossClassification(enum.Enum):
    NONE = "None"
    REVERSIBLE = "Reversible"
    LOSSY = "Lossy"


class ProjectionEventMessage:
    __slots__ = (
        "code",
        "policy_rule_id",
        "source_locations",
        "projected_location",
        "old_category",
        "new_category",
        "reversible",
        "loss_classification",
        "arguments",
    )

    def __init__(self, code, policy_rule_id=None, source_locations=(), projected_location=None,
                 old_category=None, new_category=None, reversible=False,
                 loss_classification=LossClassification.NONE, arguments=None):
        self.code = code
        self.policy_rule_id = policy_rule_id
        self.source_locations = source_locations
        self.projected_location = projected_location
        self.old_category = old_category
        self.new_category = new_category
        self.reversible = reversible
        self.loss_classification = loss_classification
        self.arguments = dict(arguments or {})

    @classmethod
    def from_value(cls, value: PortableValue, path: str) -> "ProjectionEventMessage":
        fields = exact_fields(
            value,
            ["code", "policy_rule_id", "source_locations", "projected_location",
             "old_category", "new_category", "reversible", "loss_classification", "arguments"],
            path,
        )
        code = string_of(fields[0], path + ".code")
        policy_rule_id = optional_string(fields[1], path + ".policy_rule_id")
        locations = []
        for index, item in enumerate(sequence_of(fields[2], path + ".source_locations")):
            location_path = path + f".source_locations[{index}]"
            location_fields = exact_fields(item, ["source_id", "start_byte", "end_byte"], location_path)
            locations.append(
                _SourceLocation(
                    string_of(location_fields[0], location_path + ".source_id"),
                    unsigned64(location_fields[1], location_path + ".start_byte"),
                    unsigned64(location_fields[2], location_path + ".end_byte"),
                )
            )
        projected = None
        if fields[3].kind is not Kind.NULL:
            projected = ProjectedLocationMessage.from_value(fields[3], path + ".projected_location")
        old_category = optional_string(fields[4], path + ".old_category")
        new_category = optional_string(fields[5], path + ".new_category")
        reversible = boolean_of(fields[6], path + ".reversible")
        loss_text = string_of(fields[7], path + ".loss_classification")
        try:
            loss = LossClassification(loss_text)
        except ValueError:
            raise invalid(path + ".loss_classification", "unknown loss classification") from None
        arguments = string_map_from_object(fields[8], path + ".arguments")
        return cls(code, policy_rule_id, locations, projected, old_category, new_category,
                   reversible, loss, arguments)


class _SourceLocation:
    """The wire source-location fact of one projection event."""

    __slots__ = ("source_id", "start_byte", "end_byte")

    def __init__(self, source_id: str, start_byte: int, end_byte: int):
        self.source_id = source_id
        self.start_byte = start_byte
        self.end_byte = end_byte


class ProjectionReportMessage:
    """The ordered ``core.projection-report@1`` record (projection.rs:439-444)."""

    __slots__ = ("events",)

    def __init__(self, events):
        self.events = events

    @classmethod
    def from_value_with_registry(
        cls, value: PortableValue, registry: ErrorCodeRegistry
    ) -> "ProjectionReportMessage":
        fields = schema_fields(value, "core.projection-report@1", ["schema", "events"], "$")
        events = [
            ProjectionEventMessage.from_value(item, f"$.events[{index}]")
            for index, item in enumerate(sequence_of(fields[1], "$.events"))
        ]
        for event in events:
            registry.validate(event.code, "$.events.code")
        for event in events:
            if (
                not event.code
                or (event.loss_classification is LossClassification.LOSSY and event.reversible)
                or (event.loss_classification is LossClassification.REVERSIBLE and not event.reversible)
            ):
                raise invalid("$.events", "projection event fields are contradictory")
        return cls(events)


class ProjectionResultMessage:
    """The complete or explicitly failed ``core.projection-result@1`` record
    (projection.rs:517-527)."""

    __slots__ = ("completion", "value", "has_value", "fidelity", "report", "provenance", "diagnostics")

    def __init__(self, completion, value, has_value, fidelity, report, provenance, diagnostics):
        self.completion = completion
        self.value = value
        self.has_value = has_value
        self.fidelity = fidelity
        self.report = report
        self.provenance = provenance
        self.diagnostics = diagnostics

    @classmethod
    def from_value_with_registry(
        cls, value: PortableValue, registry: ErrorCodeRegistry
    ) -> "ProjectionResultMessage":
        fields = schema_fields(
            value,
            "core.projection-result@1",
            ["schema", "completion", "value", "fidelity", "report", "provenance", "diagnostics"],
            "$",
        )
        completion = Completion.from_value_with_registry(fields[1], registry)
        has_value = False
        projected = PortableValue.null()
        if fields[2].kind is not Kind.NULL:
            value_fields = exact_fields(fields[2], ["portable_value"], "$.value")
            projected = value_fields[0]
            has_value = True
        fidelity = None
        if fields[3].kind is not Kind.NULL:
            text = string_of(fields[3], "$.fidelity")
            if text not in ("Exact", "Transformed", "Lossy"):
                raise invalid("$.fidelity", "unknown projection fidelity")
            fidelity = text
        report = ProjectionReportMessage.from_value_with_registry(fields[4], registry)
        provenance = ProvenanceMapMessage.from_value(fields[5])
        diagnostics = [
            Diagnostic.from_value(item, registry)
            for item in sequence_of(fields[6], "$.diagnostics")
        ]
        success = completion.status is CompletionStatus.SUCCESS
        if success != has_value or (success and fidelity is None) or (not success and fidelity is not None):
            raise invalid("$", "only successful projection may carry value and fidelity")
        if fidelity is not None and fidelity == "Lossy":
            found = any(event.loss_classification is LossClassification.LOSSY for event in report.events)
            if not found:
                raise invalid("$.report", "Lossy fidelity requires an explicit lossy event")
        if not success and provenance.entries:
            raise invalid("$.provenance", "failed projection cannot claim completed provenance")
        return cls(completion, projected, has_value, fidelity, report, provenance, diagnostics)


# ---------------------------------------------------------------------------
# core.query-result@1 (query.rs)
# ---------------------------------------------------------------------------


class NativeMatchLocator:
    __slots__ = ("source_id", "node_locator", "role", "ordinal")

    def __init__(self, source_id, node_locator, role, ordinal):
        self.source_id = source_id
        self.node_locator = node_locator
        self.role = role
        self.ordinal = ordinal

    @classmethod
    def from_value(cls, value: PortableValue, path: str) -> "NativeMatchLocator":
        fields = exact_fields(value, ["source_id", "node_locator", "role", "ordinal"], path)
        source_id = string_of(fields[0], path + ".source_id")
        node_locator = string_of(fields[1], path + ".node_locator")
        role = _parse_match_role(string_of(fields[2], path + ".role"))
        if role is None or not _is_native_role(role):
            raise invalid(path + ".role", "invalid source, locator, or native role")
        ordinal = unsigned64(fields[3], path + ".ordinal")
        if not source_id or len(source_id) > 1024 or not node_locator or len(node_locator) > 4096:
            raise invalid(path, "invalid source or locator")
        return cls(source_id, node_locator, role, ordinal)


def _is_native_role(role: MatchRole) -> bool:
    return role in (
        MatchRole.JSON_VALUE,
        MatchRole.JSON_OBJECT_MEMBER,
        MatchRole.JSON_ARRAY_ELEMENT,
        MatchRole.TOML_ITEM,
        MatchRole.TOML_ENTRY,
        MatchRole.TOML_ARRAY_ELEMENT,
        MatchRole.JSON_SYNTAX_PIECE,
        MatchRole.TOML_SYNTAX_PIECE,
    )


def _parse_match_role(text: str) -> MatchRole | None:
    try:
        return MatchRole(text)
    except ValueError:
        return None


class ProtocolQueryMatch:
    """One transferable query match (query.rs:127-146)."""

    __slots__ = ("kind", "path", "value", "location", "key", "value_path", "key_path", "native")

    def __init__(self, kind, path=None, value=None, location=None, key=None,
                 value_path=None, key_path=None, native=None):
        self.kind = kind
        self.path = path
        self.value = value
        self.location = location
        self.key = key
        self.value_path = value_path
        self.key_path = key_path
        self.native = native

    def role(self) -> MatchRole:
        if self.kind == "Value":
            return MatchRole.VALUE
        if self.kind == "ObjectEntry":
            return MatchRole.OBJECT_ENTRY
        if self.kind == "EntryMappingEntry":
            return MatchRole.ENTRY_MAPPING_ENTRY
        if self.kind == "Native":
            return self.native.role
        return MatchRole.VALUE

    @classmethod
    def from_value(cls, value: PortableValue, path: str) -> "ProtocolQueryMatch":
        if value.kind is not Kind.OBJECT or not value.as_object():
            raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected match Object")
        entries = value.as_object()
        if entries[0][0] != "kind":
            raise invalid(path, "kind must be the first String field")
        kind = string_of(entries[0][1], path + ".kind")
        if kind == "Value":
            fields = exact_fields(value, ["kind", "path", "value"], path)
            return cls("Value", path=ValuePath.from_value(fields[1]), value=fields[2])
        if kind == "ObjectEntry":
            fields = exact_fields(value, ["kind", "location", "key", "value_path", "value"], path)
            return cls(
                "ObjectEntry",
                location=AssociationLocation.from_value(fields[1]),
                key=fields[2],
                value_path=ValuePath.from_value(fields[3]),
                value=fields[4],
            )
        if kind == "EntryMappingEntry":
            fields = exact_fields(value, ["kind", "location", "key_path", "key", "value_path", "value"], path)
            return cls(
                "EntryMappingEntry",
                location=AssociationLocation.from_value(fields[1]),
                key_path=ValuePath.from_value(fields[2]),
                key=fields[3],
                value_path=ValuePath.from_value(fields[4]),
                value=fields[5],
            )
        if kind == "Native":
            fields = exact_fields(value, ["kind", "role", "source_id", "node_locator", "ordinal"], path)
            role = _parse_match_role(string_of(fields[1], path + ".role"))
            if role is None:
                raise invalid(path + ".role", "unknown match role")
            native = NativeMatchLocator(
                string_of(fields[2], path + ".source_id"),
                string_of(fields[3], path + ".node_locator"),
                role,
                unsigned64(fields[4], path + ".ordinal"),
            )
            return cls("Native", native=native)
        raise invalid(path, "unknown query match kind")


class QueryResultMessage:
    """The complete or explicitly non-complete ``core.query-result@1`` record
    (query.rs:148-155)."""

    __slots__ = ("domain", "role", "matches", "completion", "diagnostics")

    def __init__(self, domain, role, matches, completion, diagnostics):
        self.domain = domain
        self.role = role
        self.matches = matches
        self.completion = completion
        self.diagnostics = diagnostics

    @classmethod
    def from_value_with_registry(
        cls, value: PortableValue, registry: ErrorCodeRegistry
    ) -> "QueryResultMessage":
        fields = schema_fields(
            value,
            "core.query-result@1",
            ["schema", "domain_id", "domain_version", "role", "matches", "completion", "diagnostics"],
            "$",
        )
        domain_id = string_of(fields[1], "$.domain_id")
        domain_version = unsigned32(fields[2], "$.domain_version")
        role = _parse_match_role(string_of(fields[3], "$.role"))
        if role is None:
            raise invalid("$.role", "unknown match role")
        if not _is_v1_role(role):
            raise invalid("$.role", "role is not published by core.query-result@1")
        matches = [
            ProtocolQueryMatch.from_value(item, f"$.matches[{index}]")
            for index, item in enumerate(sequence_of(fields[4], "$.matches"))
        ]
        completion = Completion.from_value_with_registry(fields[5], registry)
        diagnostics = [
            Diagnostic.from_value(item, registry)
            for item in sequence_of(fields[6], "$.diagnostics")
        ]
        if completion.produced != len(matches):
            raise invalid("$", "completion count or match role is inconsistent")
        for match in matches:
            if match.role() is not role:
                raise invalid("$", "completion count or match role is inconsistent")
        previous = 0
        seen = False
        for match in matches:
            if match.kind != "Native":
                continue
            if seen and match.native.ordinal <= previous:
                raise invalid("$.matches", "native match ordinals must be strictly increasing")
            previous = match.native.ordinal
            seen = True
        return cls(QueryDomain(domain_id, domain_version), role, matches, completion, diagnostics)


def _is_v1_role(role: MatchRole) -> bool:
    excluded = {
        MatchRole.GRAPH_NODE, MatchRole.GRAPH_SEQUENCE_ELEMENT, MatchRole.GRAPH_MAPPING_ENTRY,
        MatchRole.YAML_STREAM, MatchRole.YAML_DOCUMENT, MatchRole.YAML_NODE,
        MatchRole.YAML_MAPPING_ENTRY, MatchRole.YAML_SEQUENCE_ELEMENT,
        MatchRole.YAML_ANCHOR_DEFINITION, MatchRole.YAML_ALIAS_OCCURRENCE,
        MatchRole.YAML_SYNTAX_PIECE,
        MatchRole.INI_DOCUMENT, MatchRole.INI_SECTION, MatchRole.INI_DEFAULT_SECTION,
        MatchRole.INI_ENTRY, MatchRole.INI_PHYSICAL_LINE, MatchRole.INI_LOGICAL_LINE,
        MatchRole.INI_ERROR_LINE, MatchRole.INI_SYNTAX_PIECE,
        MatchRole.PROPERTIES_DOCUMENT, MatchRole.PROPERTIES_NATURAL_LINE,
        MatchRole.PROPERTIES_LOGICAL_LINE, MatchRole.PROPERTIES_PROPERTY,
        MatchRole.PROPERTIES_COMMENT, MatchRole.PROPERTIES_ESCAPE,
        MatchRole.PROPERTIES_ERROR_LINE, MatchRole.PROPERTIES_SYNTAX_PIECE,
        MatchRole.XML_DOCUMENT, MatchRole.XML_DECLARATION, MatchRole.XML_DOCTYPE,
        MatchRole.XML_PROLOG_ITEM, MatchRole.XML_ELEMENT, MatchRole.XML_CONTENT_ITEM,
        MatchRole.XML_ATTRIBUTE, MatchRole.XML_NAMESPACE_BINDING, MatchRole.XML_TEXT,
        MatchRole.XML_CDATA, MatchRole.XML_COMMENT, MatchRole.XML_PROCESSING_INSTRUCTION,
        MatchRole.XML_REFERENCE, MatchRole.XML_ERROR_REGION, MatchRole.XML_SYNTAX_PIECE,
        MatchRole.PLIST_VALUE, MatchRole.PLIST_DICT_ENTRY, MatchRole.PLIST_KEY,
        MatchRole.PLIST_ARRAY_ELEMENT, MatchRole.PLIST_SYNTAX_PIECE,
        MatchRole.PLIST_BINARY_STRUCTURE, MatchRole.PLIST_BINARY_OBJECT,
        MatchRole.PLIST_BINARY_OFFSET, MatchRole.PLIST_BINARY_REF,
        MatchRole.PLIST_BINARY_TRAILER,
        MatchRole.HCL_BODY, MatchRole.HCL_ATTRIBUTE, MatchRole.HCL_BLOCK,
        MatchRole.HCL_BLOCK_LABEL, MatchRole.HCL_EXPRESSION, MatchRole.HCL_TEMPLATE_PART,
        MatchRole.HCL_ERROR_REGION, MatchRole.HCL_SYNTAX_PIECE,
    }
    return role not in excluded


# ---------------------------------------------------------------------------
# core.change-set@1 (change.rs)
# ---------------------------------------------------------------------------


class _SourceEditMessage:
    """One exact source edit of the ``core.change-set@1`` record
    (records_change_set.go:13-39)."""

    __slots__ = ("old_start", "old_end", "new_start", "new_end", "replacement")

    def __init__(self, old_start, old_end, new_start, new_end, replacement: bytes):
        if (
            old_start > old_end
            or new_start > new_end
            or new_end - new_start != len(replacement)
        ):
            raise invalid("$.source_edit", "invalid ranges or replacement length")
        self.old_start = old_start
        self.old_end = old_end
        self.new_start = new_start
        self.new_end = new_end
        self.replacement = bytes(replacement)

    @classmethod
    def from_value(cls, value: PortableValue, path: str) -> "_SourceEditMessage":
        fields = exact_fields(
            value,
            ["old_start", "old_end", "new_start", "new_end", "replacement"],
            path,
        )
        old_start = unsigned64(fields[0], path + ".old_start")
        old_end = unsigned64(fields[1], path + ".old_end")
        new_start = unsigned64(fields[2], path + ".new_start")
        new_end = unsigned64(fields[3], path + ".new_end")
        if fields[4].kind is not Kind.BYTES:
            raise protocol_error(
                ProtocolErrorKind.WRONG_TYPE, path + ".replacement", "expected Bytes"
            )
        return cls(old_start, old_end, new_start, new_end, fields[4].as_bytes())


class _NodeMappingMessage:
    """One portable node-mapping fact with caller-defined stable locators
    (records_change_set.go:41-122)."""

    __slots__ = ("old_locators", "new_locators", "status", "reason")

    _STATUSES = ("Preserved", "Replaced", "Deleted", "Split", "Merged", "Unmapped")

    def __init__(self, old_locators, new_locators, status: str, reason=None):
        if len(set(old_locators)) != len(old_locators) or len(set(new_locators)) != len(
            new_locators
        ):
            raise invalid(
                "$.node_mapping", "locators must be non-empty, bounded, and unique per side"
            )
        for locator in list(old_locators) + list(new_locators):
            if locator == "" or len(locator) > 4096:
                raise invalid(
                    "$.node_mapping", "locators must be non-empty, bounded, and unique per side"
                )
        topology = False
        needs_reason = False
        if status == "Preserved":
            topology = len(old_locators) == 1 and len(new_locators) == 1
        elif status == "Replaced":
            topology = len(old_locators) == 1 and len(new_locators) <= 1
            needs_reason = len(new_locators) == 0
        elif status == "Deleted":
            topology = len(old_locators) == 1 and len(new_locators) == 0
            needs_reason = True
        elif status == "Split":
            topology = len(old_locators) == 1 and len(new_locators) >= 2
            needs_reason = True
        elif status == "Merged":
            topology = len(old_locators) >= 2 and len(new_locators) == 1
            needs_reason = True
        elif status == "Unmapped":
            topology = len(old_locators) > 0 and len(new_locators) == 0
            needs_reason = True
        has_reason = reason is not None and reason != "" and len(reason) <= 1024
        if not topology or needs_reason != has_reason:
            raise invalid(
                "$.node_mapping", "mapping topology or reason contradicts status"
            )
        self.old_locators = list(old_locators)
        self.new_locators = list(new_locators)
        self.status = status
        self.reason = reason

    @classmethod
    def from_value(cls, value: PortableValue, path: str) -> "_NodeMappingMessage":
        fields = exact_fields(
            value,
            ["old_locators", "new_locators", "status", "reason"],
            path,
        )
        old_locators = _string_sequence(fields[0], path + ".old_locators")
        new_locators = _string_sequence(fields[1], path + ".new_locators")
        status = string_of(fields[2], path + ".status")
        reason = optional_string(fields[3], path + ".reason")
        return cls(old_locators, new_locators, status, reason)


class ChangeSetMessage:
    """The complete ``core.change-set@1`` record with external source and
    node identities (records_change_set.go:124-343)."""

    __slots__ = ("old_source_id", "new_source_id", "source_edits", "node_mappings", "diagnostics")

    def __init__(self, old_source_id, new_source_id, source_edits, node_mappings, diagnostics):
        if (
            not old_source_id
            or not new_source_id
            or len(old_source_id) > 1024
            or len(new_source_id) > 1024
        ):
            raise invalid("$", "source IDs must be non-empty and bounded")
        for index in range(1, len(source_edits)):
            if (
                source_edits[index - 1].old_end > source_edits[index].old_start
                or source_edits[index - 1].new_end > source_edits[index].new_start
            ):
                raise invalid(
                    "$.source_edits",
                    "edits must be ordered and non-overlapping in both snapshots",
                )
        seen_locators: set[str] = set()
        for mapping in node_mappings:
            for locator in mapping.old_locators:
                if locator in seen_locators:
                    raise invalid(
                        "$.node_mappings",
                        "an old locator may participate in only one mapping fact",
                    )
                seen_locators.add(locator)
        self.old_source_id = old_source_id
        self.new_source_id = new_source_id
        self.source_edits = list(source_edits)
        self.node_mappings = list(node_mappings)
        self.diagnostics = list(diagnostics)

    @classmethod
    def from_value_with_registry(
        cls, value: PortableValue, registry: ErrorCodeRegistry
    ) -> "ChangeSetMessage":
        fields = schema_fields(
            value,
            "core.change-set@1",
            ["schema", "old_source_id", "new_source_id", "source_edits", "node_mappings", "diagnostics"],
            "$",
        )
        old_source_id = string_of(fields[1], "$.old_source_id")
        new_source_id = string_of(fields[2], "$.new_source_id")
        source_edits = [
            _SourceEditMessage.from_value(item, f"$.source_edits[{index}]")
            for index, item in enumerate(sequence_of(fields[3], "$.source_edits"))
        ]
        node_mappings = [
            _NodeMappingMessage.from_value(item, f"$.node_mappings[{index}]")
            for index, item in enumerate(sequence_of(fields[4], "$.node_mappings"))
        ]
        diagnostics = [
            Diagnostic.from_value(item, registry)
            for item in sequence_of(fields[5], "$.diagnostics")
        ]
        return cls(old_source_id, new_source_id, source_edits, node_mappings, diagnostics)


def _string_sequence(value: PortableValue, path: str) -> list[str]:
    output: list[str] = []
    for index, item in enumerate(sequence_of(value, path)):
        output.append(string_of(item, f"{path}[{index}]"))
    return output


# ---------------------------------------------------------------------------
# core.source-encoding@1 (source.rs)
# ---------------------------------------------------------------------------


def _wire_kind(encoding: SourceEncoding) -> str:
    """The Go/Rust wire kind spelling of one document encoding."""
    return {
        SourceEncodingKind.BINARY: "Binary",
        SourceEncodingKind.UTF8: "Utf8",
        SourceEncodingKind.UTF16LE: "Utf16Le",
        SourceEncodingKind.UTF16BE: "Utf16Be",
        SourceEncodingKind.LATIN1: "Latin1",
        SourceEncodingKind.WINDOWS_CODE_PAGE: "WindowsCodePage",
    }[encoding.kind]


def _parse_wire_kind(text: str, path: str) -> SourceEncodingKind:
    kinds = {
        "Binary": SourceEncodingKind.BINARY,
        "Utf8": SourceEncodingKind.UTF8,
        "Utf16Le": SourceEncodingKind.UTF16LE,
        "Utf16Be": SourceEncodingKind.UTF16BE,
        "Latin1": SourceEncodingKind.LATIN1,
        "WindowsCodePage": SourceEncodingKind.WINDOWS_CODE_PAGE,
    }
    kind = kinds.get(text)
    if kind is None:
        raise invalid(path, "unknown source encoding kind")
    return kind


def _source_encoding_value(encoding: SourceEncoding) -> PortableValue:
    code_page: PortableValue = PortableValue.null()
    if encoding.code_page is not None:
        code_page = PortableValue.integer(encoding.code_page.number)
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.source-encoding@1")),
            ("kind", PortableValue.string(_wire_kind(encoding))),
            ("windows_code_page", code_page),
        ]
    )


def parse_source_encoding_value(value: PortableValue, path: str) -> SourceEncoding:
    """Strictly decodes one ``core.source-encoding@1`` record
    (source.rs; records_source.go)."""
    fields = schema_fields(value, "core.source-encoding@1", ["schema", "kind", "windows_code_page"], path)
    kind = _parse_wire_kind(string_of(fields[1], path + ".kind"), path + ".kind")
    code_page = None
    if fields[2].kind is not Kind.NULL:
        page = unsigned32(fields[2], path + ".windows_code_page")
        resolved = WindowsCodePage.from_number(page)
        if resolved is None:
            raise invalid(path + ".windows_code_page", "unsupported Windows code page")
        code_page = resolved
    if kind is SourceEncodingKind.WINDOWS_CODE_PAGE:
        if code_page is None:
            raise invalid(path + ".windows_code_page", "Windows code page requires a number")
        return SourceEncoding(kind=kind, code_page=code_page)
    if code_page is not None:
        raise invalid(path + ".windows_code_page", "non-Windows encoding requires null")
    return SourceEncoding(kind=kind)


def _encoding_facts_value(facts: EncodingFacts) -> PortableValue:
    bom = PortableValue.null()
    if facts.bom is not None:
        bom = PortableValue.string(facts.bom.value)
    declaration = PortableValue.null()
    if facts.declaration is not None:
        declaration = _source_encoding_value(facts.declaration)
    caller_override = PortableValue.null()
    if facts.caller_override is not None:
        caller_override = _source_encoding_value(facts.caller_override)
    return PortableValue.object(
        [
            ("profile_default", _source_encoding_value(facts.profile_default)),
            ("bom_policy", PortableValue.string(facts.bom_policy.value)),
            ("bom", bom),
            ("declaration", declaration),
            ("caller_override", caller_override),
            ("selected", _source_encoding_value(facts.selected)),
        ]
    )


def _parse_encoding_facts_value(value: PortableValue, path: str) -> EncodingFacts:
    fields = exact_fields(
        value,
        ["profile_default", "bom_policy", "bom", "declaration", "caller_override", "selected"],
        path,
    )
    profile_default = parse_source_encoding_value(fields[0], path + ".profile_default")
    policy_text = string_of(fields[1], path + ".bom_policy")
    try:
        policy = BomPolicy(policy_text)
    except ValueError:
        raise invalid(path + ".bom_policy", "unknown BOM policy") from None
    bom = None
    if fields[2].kind is not Kind.NULL:
        bom_text = string_of(fields[2], path + ".bom")
        try:
            bom = BomKind(bom_text)
        except ValueError:
            raise invalid(path + ".bom", "unknown BOM ID") from None
    declaration = None
    if fields[3].kind is not Kind.NULL:
        declaration = parse_source_encoding_value(fields[3], path + ".declaration")
    caller_override = None
    if fields[4].kind is not Kind.NULL:
        caller_override = parse_source_encoding_value(fields[4], path + ".caller_override")
    selected = parse_source_encoding_value(fields[5], path + ".selected")
    try:
        return EncodingFacts.from_claim_with_bom_policy(
            profile_default, policy, bom, declaration, caller_override, selected
        )
    except SourceError as error:
        raise invalid(path, str(error)) from None


# -- the v1 source-encoding facts form (records_source.go encodingFromNameV1) --


def _go_encoding_name(encoding: SourceEncoding) -> str:
    return _wire_kind(encoding)


def _encoding_from_go_name(text: str, path: str) -> SourceEncoding:
    factories = {
        "Binary": SourceEncoding.binary,
        "Utf8": SourceEncoding.utf8,
        "Utf16Le": SourceEncoding.utf16le,
        "Utf16Be": SourceEncoding.utf16be,
        "Latin1": SourceEncoding.latin1,
    }
    factory = factories.get(text)
    if factory is None:
        raise invalid(path, "unknown encoding ID")
    return factory()


def _encoding_value_v1(facts: EncodingFacts) -> PortableValue:
    bom = facts.bom.value if facts.bom is not None else None
    return PortableValue.object(
        [
            ("profile_default", PortableValue.string(_go_encoding_name(facts.profile_default))),
            ("bom", nullable_string(bom)),
            (
                "declaration",
                nullable_string(_go_encoding_name(facts.declaration))
                if facts.declaration is not None
                else PortableValue.null(),
            ),
            (
                "caller_override",
                nullable_string(_go_encoding_name(facts.caller_override))
                if facts.caller_override is not None
                else PortableValue.null(),
            ),
            ("selected", PortableValue.string(_go_encoding_name(facts.selected))),
        ]
    )


def _optional_bom(value: PortableValue, path: str) -> BomKind | None:
    if value.kind is Kind.NULL:
        return None
    text = string_of(value, path)
    try:
        return BomKind(text)
    except ValueError:
        raise invalid(path, "unknown BOM ID") from None


def _optional_encoding_v1(value: PortableValue, path: str) -> SourceEncoding | None:
    if value.kind is Kind.NULL:
        return None
    return _encoding_from_go_name(string_of(value, path), path)


def _map_source_error(path: str, error: SourceError) -> ProtocolError:
    """The protocol mapping of a source construction failure
    (records_source.go mapSourceError)."""
    if error.kind is SourceErrorKind.RESOURCE_LIMIT:
        return resource(path, error.name or "source")
    return invalid(path, str(error))


def _facts_from_claim_v1(
    profile_default: SourceEncoding,
    bom: BomKind | None,
    declaration: SourceEncoding | None,
    caller_override: SourceEncoding | None,
    selected: SourceEncoding,
) -> EncodingFacts:
    """Validates a structurally complete v1 encoding-facts claim
    (records_source.go factsFromClaim; the v1 policy is always
    DetectUnicode)."""
    try:
        return EncodingFacts.from_claim_with_bom_policy(
            profile_default,
            BomPolicy.DETECT_UNICODE,
            bom,
            declaration,
            caller_override,
            selected,
        )
    except SourceError as error:
        raise _map_source_error("$.encoding", error) from None


def _encoding_from_value_v1(value: PortableValue, path: str) -> EncodingFacts:
    fields = exact_fields(
        value,
        ["profile_default", "bom", "declaration", "caller_override", "selected"],
        path,
    )
    profile_default = _encoding_from_go_name(
        string_of(fields[0], path + ".profile_default"), path + ".profile_default"
    )
    bom = _optional_bom(fields[1], path + ".bom")
    declaration = _optional_encoding_v1(fields[2], path + ".declaration")
    caller_override = _optional_encoding_v1(fields[3], path + ".caller_override")
    selected = _encoding_from_go_name(
        string_of(fields[4], path + ".selected"), path + ".selected"
    )
    return _facts_from_claim_v1(profile_default, bom, declaration, caller_override, selected)


def _facts_to_request_v1(facts: EncodingFacts) -> EncodingRequest:
    """Rebuilds the v1 resolution request from claimed facts
    (records_source.go factsToRequestV1)."""
    request = EncodingRequest.new(facts.profile_default)
    if facts.declaration is not None:
        request = request.with_declaration(facts.declaration)
    if facts.caller_override is not None:
        request = request.with_caller_override(facts.caller_override)
    return request


# -- digests ---------------------------------------------------------------


def _digest_value(digest: ContentDigest) -> PortableValue:
    return PortableValue.object(
        [
            ("algorithm", PortableValue.string(digest.algorithm)),
            ("hex", PortableValue.string(digest.hex)),
        ]
    )


def _parse_digest(value: PortableValue, path: str) -> ContentDigest:
    fields = exact_fields(value, ["algorithm", "hex"], path)
    algorithm = string_of(fields[0], path + ".algorithm")
    if algorithm != "sha256":
        raise invalid(path, "expected sha256")
    hex_text = string_of(fields[1], path + ".hex")
    if len(hex_text) != 64 or any(
        character not in "0123456789abcdef" for character in hex_text
    ):
        raise invalid(path, "invalid lowercase sha256")
    return ContentDigest.from_bytes(bytes.fromhex(hex_text))


# ---------------------------------------------------------------------------
# core.source-snapshot@1 / core.source-snapshot@2 (source.rs)
# ---------------------------------------------------------------------------


def _snapshot_value(
    schema: str, snapshot: SourceSnapshot, encoding: PortableValue
) -> PortableValue:
    status = "NotText" if snapshot.decoded_text() is None else "Available"
    return PortableValue.object(
        [
            ("schema", PortableValue.string(schema)),
            ("raw_bytes", PortableValue.bytes_value(snapshot.bytes())),
            ("digest", _digest_value(snapshot.digest())),
            ("encoding", encoding),
            ("decoded_status", PortableValue.string(status)),
        ]
    )


def _source_snapshot_v1_from_value(value: PortableValue, limits: SourceLimits) -> SourceSnapshot:
    fields = schema_fields(
        value,
        "core.source-snapshot@1",
        ["schema", "raw_bytes", "digest", "encoding", "decoded_status"],
        "$",
    )
    raw = fields[1]
    if raw.kind is not Kind.BYTES:
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, "$.raw_bytes", "expected Bytes")
    claimed_digest = _parse_digest(fields[2], "$.digest")
    claimed_encoding = _encoding_from_value_v1(fields[3], "$.encoding")
    decoded_status = string_of(fields[4], "$.decoded_status")
    if decoded_status not in ("Available", "NotText"):
        raise invalid("$.decoded_status", "expected Available or NotText")
    try:
        snapshot = SourceSnapshot.from_raw(
            raw.as_bytes(), _facts_to_request_v1(claimed_encoding), limits
        )
    except SourceError as error:
        raise _map_source_error("$.raw_bytes", error) from None
    if snapshot.digest() != claimed_digest:
        raise invalid("$.digest", "digest does not match raw_bytes")
    if snapshot.encoding_facts() != claimed_encoding:
        raise invalid("$.encoding", "encoding facts do not match raw_bytes resolution")
    actual_status = "NotText" if snapshot.decoded_text() is None else "Available"
    if decoded_status != actual_status:
        raise invalid("$.decoded_status", "decoded status contradicts selected encoding")
    return snapshot


def _source_snapshot_v2_from_value(value: PortableValue, limits: SourceLimits) -> SourceSnapshot:
    fields = schema_fields(
        value,
        "core.source-snapshot@2",
        ["schema", "raw_bytes", "digest", "encoding", "decoded_status"],
        "$",
    )
    raw_value = fields[1]
    if raw_value.kind is not Kind.BYTES:
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, "$.raw_bytes", "expected Bytes")
    claimed_digest = _parse_digest(fields[2], "$.digest")
    claimed_facts = _parse_encoding_facts_value(fields[3], "$.encoding")
    decoded_status = string_of(fields[4], "$.decoded_status")
    if decoded_status not in ("Available", "NotText"):
        raise invalid("$.decoded_status", "expected Available or NotText")
    request = EncodingRequest.new(claimed_facts.profile_default).with_bom_policy(
        claimed_facts.bom_policy
    )
    if claimed_facts.declaration is not None:
        request = request.with_declaration(claimed_facts.declaration)
    if claimed_facts.caller_override is not None:
        request = request.with_caller_override(claimed_facts.caller_override)
    try:
        snapshot = SourceSnapshot.from_raw(raw_value.as_bytes(), request, limits)
    except SourceError as error:
        raise invalid("$.raw_bytes", str(error)) from None
    if snapshot.digest() != claimed_digest:
        raise invalid("$.digest", "digest does not match raw_bytes")
    if snapshot.encoding != claimed_facts:
        raise invalid("$.encoding", "encoding facts do not match raw_bytes resolution")
    actual_status = "NotText" if snapshot.decoded_text() is None else "Available"
    if decoded_status != actual_status:
        raise invalid("$.decoded_status", "decoded status contradicts selected encoding")
    return snapshot


# ---------------------------------------------------------------------------
# core.source-patch@1 / core.source-patch@2 (source.rs)
# ---------------------------------------------------------------------------


def _replacement_value(replacement: SourceReplacement) -> PortableValue:
    return PortableValue.object(
        [
            ("old_start", PortableValue.integer(replacement.old_start)),
            ("old_end", PortableValue.integer(replacement.old_end)),
            ("original", PortableValue.bytes_value(replacement.original)),
            ("replacement", PortableValue.bytes_value(replacement.replacement)),
            ("redact_original", PortableValue.boolean(replacement.redact_original)),
            ("redact_replacement", PortableValue.boolean(replacement.redact_replacement)),
        ]
    )


def _parse_replacements(
    value: PortableValue, path: str, limits: SourcePatchLimits
) -> list[SourceReplacement]:
    """Strictly decodes the ordered replacement facts under one patch budget
    (records_source.go; cli.rs parse_plan_entry)."""
    replacement_values = sequence_of(value, path)
    if len(replacement_values) > limits.max_replacements:
        raise resource(path, "replacement count exceeds configured limit")
    patch_bytes = 0
    replacements = []
    for index, replacement_value in enumerate(replacement_values):
        replacement_path = f"{path}[{index}]"
        fields = exact_fields(
            replacement_value,
            ["old_start", "old_end", "original", "replacement",
             "redact_original", "redact_replacement"],
            replacement_path,
        )
        old_start = unsigned64(fields[0], replacement_path + ".old_start")
        old_end = unsigned64(fields[1], replacement_path + ".old_end")
        if fields[2].kind is not Kind.BYTES:
            raise protocol_error(
                ProtocolErrorKind.WRONG_TYPE, replacement_path + ".original", "expected Bytes"
            )
        if fields[3].kind is not Kind.BYTES:
            raise protocol_error(
                ProtocolErrorKind.WRONG_TYPE, replacement_path + ".replacement", "expected Bytes"
            )
        original = fields[2].as_bytes()
        replacement = fields[3].as_bytes()
        patch_bytes += len(original) + len(replacement)
        if patch_bytes > limits.max_patch_bytes:
            raise resource(path, "patch bytes exceed the configured limit")
        replacements.append(
            SourceReplacement(
                old_start,
                old_end,
                original,
                replacement,
                boolean_of(fields[4], replacement_path + ".redact_original"),
                boolean_of(fields[5], replacement_path + ".redact_replacement"),
            )
        )
    return replacements


def _source_patch_v1_from_value(value: PortableValue, limits: SourcePatchLimits) -> SourcePatch:
    fields = schema_fields(
        value,
        "core.source-patch@1",
        ["schema", "base_digest", "target_digest", "encoding", "replacements", "metadata"],
        "$",
    )
    base_digest = _parse_digest(fields[1], "$.base_digest")
    target_digest = _parse_digest(fields[2], "$.target_digest")
    claimed_encoding = _encoding_from_value_v1(fields[3], "$.encoding")
    replacements = _parse_replacements(fields[4], "$.replacements", limits)
    metadata = string_map_from_object(fields[5], "$.metadata")
    return SourcePatch.new(
        base_digest,
        target_digest,
        claimed_encoding,
        replacements,
        metadata,
        limits,
    )


def _source_patch_v2_from_value(value: PortableValue, limits: SourcePatchLimits) -> SourcePatch:
    fields = schema_fields(
        value,
        "core.source-patch@2",
        ["schema", "base_digest", "target_digest", "encoding", "replacements", "metadata"],
        "$",
    )
    base_digest = _parse_digest(fields[1], "$.base_digest")
    target_digest = _parse_digest(fields[2], "$.target_digest")
    facts = _parse_encoding_facts_value(fields[3], "$.encoding")
    replacements = _parse_replacements(fields[4], "$.replacements", limits)
    metadata = string_map_from_object(fields[5], "$.metadata")
    return SourcePatch.new(
        base_digest,
        target_digest,
        facts,
        replacements,
        metadata,
        limits,
    )


# ---------------------------------------------------------------------------
# core.materialization-request@1 / @2, core.materialization-report@1,
# core.materialization-provenance-map@1, core.materialization-result@1 / @2
# (materialization.rs)
# ---------------------------------------------------------------------------


def _profile_reference_value(profile: ProfileReference) -> PortableValue:
    return PortableValue.object(
        [
            ("id", PortableValue.string(profile.id)),
            ("version", PortableValue.integer(profile.version)),
        ]
    )


def _parse_profile_reference(value: PortableValue, path: str) -> ProfileReference:
    fields = exact_fields(value, ["id", "version"], path)
    identifier = string_of(fields[0], path + ".id")
    version = unsigned32(fields[1], path + ".version")
    return ProfileReference(identifier, version)


def _materialization_limits_value(limits: dict) -> PortableValue:
    return PortableValue.object(
        [
            ("max_input_nodes", PortableValue.integer(limits["max_input_nodes"])),
            ("max_output_bytes", PortableValue.integer(limits["max_output_bytes"])),
            ("max_depth", PortableValue.integer(limits["max_depth"])),
            ("max_report_entries", PortableValue.integer(limits["max_report_entries"])),
            ("max_provenance_entries", PortableValue.integer(limits["max_provenance_entries"])),
        ]
    )


_DEFAULT_MATERIALIZATION_LIMITS = {
    "max_input_nodes": 1_000_000,
    "max_output_bytes": 64 << 20,
    "max_depth": 256,
    "max_report_entries": 100_000,
    "max_provenance_entries": 2_000_000,
}


def _parse_materialization_limits(value: PortableValue, path: str) -> dict:
    fields = exact_fields(
        value,
        ["max_input_nodes", "max_output_bytes", "max_depth",
         "max_report_entries", "max_provenance_entries"],
        path,
    )
    return {
        "max_input_nodes": unsigned64(fields[0], path + ".max_input_nodes"),
        "max_output_bytes": unsigned64(fields[1], path + ".max_output_bytes"),
        "max_depth": unsigned64(fields[2], path + ".max_depth"),
        "max_report_entries": unsigned64(fields[3], path + ".max_report_entries"),
        "max_provenance_entries": unsigned64(fields[4], path + ".max_provenance_entries"),
    }


def _materialization_request_from_value(
    value: PortableValue, schema: str, encoding_parser
) -> dict:
    """Strictly decodes one materialization-request record under one explicit
    encoding decoder (materialization.rs materialization_request_from_value)."""
    fields = schema_fields(
        value,
        schema,
        ["schema", "target_profile", "style", "encoding", "newline",
         "mapping_policy", "representability", "limits"],
        "$",
    )
    target_profile = _parse_profile_reference(fields[1], "$.target_profile")
    style_fields = exact_fields(fields[2], ["id", "version"], "$.style")
    style_id = string_of(style_fields[0], "$.style.id")
    style_version = unsigned32(style_fields[1], "$.style.version")
    encoding = encoding_parser(fields[3], "$.encoding")
    newline = string_of(fields[4], "$.newline")
    if newline not in ("None", "Lf", "CrLf"):
        raise invalid("$.newline", "unknown newline policy")
    mapping_policy = string_of(fields[5], "$.mapping_policy")
    if mapping_policy not in ("RequireObject", "UniqueStringEntriesToObject"):
        raise invalid("$.mapping_policy", "unknown mapping policy")
    representability = string_of(fields[6], "$.representability")
    if representability != "ExactOnly":
        raise invalid("$.representability", "requires ExactOnly")
    limits = _parse_materialization_limits(fields[7], "$.limits")
    return {
        "target_profile": target_profile,
        "style_id": style_id,
        "style_version": style_version,
        "encoding": encoding,
        "newline": newline,
        "mapping_policy": mapping_policy,
        "representability": representability,
        "limits": limits,
    }


def _parse_materialization_encoding_v1(value: PortableValue, path: str) -> SourceEncoding:
    """The v1 encoding spelling: a lowercase string, so a disguised v2
    payload fails with wrong-type at $.encoding (materialization.rs:59-66)."""
    text = string_of(value, path)
    if text not in ("binary", "utf-8", "utf-16le", "utf-16be", "latin-1"):
        raise invalid(path, "unknown source encoding")
    return {
        "binary": SourceEncoding.binary,
        "utf-8": SourceEncoding.utf8,
        "utf-16le": SourceEncoding.utf16le,
        "utf-16be": SourceEncoding.utf16be,
        "latin-1": SourceEncoding.latin1,
    }[text]


def _materialization_request_v1_from_value(value: PortableValue) -> None:
    _materialization_request_from_value(
        value, "core.materialization-request@1", _parse_materialization_encoding_v1
    )


def _materialization_request_v2_from_value(value: PortableValue) -> None:
    _materialization_request_from_value(
        value, "core.materialization-request@2", parse_source_encoding_value
    )


def _materialization_report_from_value(value: PortableValue, registry: ErrorCodeRegistry) -> None:
    """Strictly decodes ordered v3 diagnostics (materialization.rs:263-278)."""
    fields = schema_fields(value, "core.materialization-report@1", ["schema", "events"], "$")
    for index, event in enumerate(sequence_of(fields[1], "$.events")):
        Diagnostic.from_value(event, registry)


def _materialization_provenance_from_value(value: PortableValue) -> None:
    """Strictly decodes the ordered provenance map
    (materialization.rs:507-540)."""
    fields = schema_fields(
        value, "core.materialization-provenance-map@1", ["schema", "entries"], "$"
    )
    source_id = None
    locator_ranges: dict[str, tuple[int, int]] = {}
    for index, entry_value in enumerate(sequence_of(fields[1], "$.entries")):
        path = f"$.entries[{index}]"
        entry_fields = exact_fields(entry_value, ["input", "outputs"], path)
        exact_fields(entry_fields[0], ["kind", "value"], path + ".input")
        outputs = sequence_of(entry_fields[1], path + ".outputs")
        if not outputs:
            raise invalid(path + ".outputs", "provenance entry requires at least one output")
        for output_index, output_value in enumerate(outputs):
            output_path = f"{path}.outputs[{output_index}]"
            output_fields = exact_fields(
                output_value,
                ["target_source_id", "target_node_locator", "start_byte", "end_byte", "relation"],
                output_path,
            )
            target_source_id = string_of(output_fields[0], output_path + ".target_source_id")
            target_node_locator = string_of(output_fields[1], output_path + ".target_node_locator")
            start_byte = unsigned64(output_fields[2], output_path + ".start_byte")
            end_byte = unsigned64(output_fields[3], output_path + ".end_byte")
            relation = string_of(output_fields[4], output_path + ".relation")
            if relation not in ("Direct", "Reencoded", "Generated"):
                raise invalid(output_path + ".relation", "unknown materialization relation")
            if (
                not target_source_id
                or len(target_source_id) > 1024
                or not target_node_locator
                or len(target_node_locator) > 4096
                or start_byte > end_byte
            ):
                raise invalid(output_path, "invalid target origin")
            if source_id is not None and source_id != target_source_id:
                raise invalid(output_path, "one provenance map must bind one target source")
            source_id = target_source_id
            range_key = (start_byte, end_byte)
            if (
                target_node_locator in locator_ranges
                and locator_ranges[target_node_locator] != range_key
            ):
                raise invalid(output_path, "one target node locator cannot identify contradictory ranges")
            locator_ranges[target_node_locator] = range_key


def _parse_value_kind(text: str, path: str) -> None:
    if text not in (
        "Null", "Boolean", "Integer", "Decimal", "BinaryFloat32", "BinaryFloat64",
        "String", "Bytes", "Date", "Time", "LocalDateTime", "OffsetDateTime",
        "Sequence", "Object", "EntryMapping",
    ):
        raise invalid(path, "unknown portable value kind")


def _parse_failure(value: PortableValue, path: str) -> None:
    """Strictly decodes one materialization failure with its registered code
    (materialization.rs parse_failure)."""
    if value.kind is not Kind.OBJECT:
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected Object")
    entries = value.as_object()
    kind_entry = next((entry for entry in entries if entry[0] == "kind"), None)
    if kind_entry is None:
        raise invalid(path, "missing kind")
    kind = string_of(kind_entry[1], path + ".kind")
    code_entry = next((entry for entry in entries if entry[0] == "code"), None)
    if code_entry is None:
        raise invalid(path, "missing code")
    code = string_of(code_entry[1], path + ".code")
    if kind == "InvalidRequest":
        fields = exact_fields(value, ["kind", "code", "detail"], path)
        detail = string_of(fields[2], path + ".detail")
        if detail == "" or len(detail) > 4096:
            raise invalid(path, "invalid failure detail")
        expected = "core.materialization.invalid-request@1"
    elif kind == "UnsupportedProfile":
        exact_fields(value, ["kind", "code"], path)
        expected = "core.materialization.unsupported-profile@1"
    elif kind == "UnsupportedStyle":
        exact_fields(value, ["kind", "code"], path)
        expected = "core.materialization.unsupported-style@1"
    elif kind == "UnsupportedEncoding":
        exact_fields(value, ["kind", "code"], path)
        expected = "core.materialization.unsupported-encoding@1"
    elif kind == "UnsupportedNewline":
        exact_fields(value, ["kind", "code"], path)
        expected = "core.materialization.unsupported-newline@1"
    elif kind == "Unrepresentable":
        fields = exact_fields(value, ["kind", "code", "path", "value_kind"], path)
        _parse_value_kind(string_of(fields[3], path + ".value_kind"), path)
        expected = "core.materialization.unrepresentable@1"
    elif kind == "ResourceLimit":
        fields = exact_fields(value, ["kind", "code", "limit"], path)
        limit = string_of(fields[2], path + ".limit")
        if (
            limit == ""
            or len(limit) > 256
            or not all(
                ("a" <= character <= "z")
                or ("0" <= character <= "9")
                or character == "-"
                for character in limit
            )
        ):
            raise invalid(path, "invalid resource limit ID")
        expected = "core.materialization.resource-limit@1"
    elif kind == "FormationFailed":
        exact_fields(value, ["kind", "code"], path)
        expected = "core.materialization.formation-failed@1"
    else:
        raise invalid(path, "unknown materialization failure")
    ErrorCodeRegistry(3).validate(code, path + ".code")
    if code != expected:
        raise invalid(path + ".code", "failure kind contradicts its registered code")


def _materialization_result_v1_from_value(value: PortableValue, registry: ErrorCodeRegistry) -> None:
    fields = schema_fields(
        value, "core.materialization-result@1", ["schema", "target_profile", "outcome"], "$"
    )
    _parse_profile_reference(fields[1], "$.target_profile")
    outcome = fields[2]
    if outcome.kind is not Kind.OBJECT:
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, "$.outcome", "expected Object")
    kind_entry = next((entry for entry in outcome.as_object() if entry[0] == "kind"), None)
    if kind_entry is None:
        raise invalid("$.outcome", "missing kind")
    kind = string_of(kind_entry[1], "$.outcome.kind")
    if kind == "Complete":
        complete = exact_fields(
            outcome,
            ["kind", "target_source_id", "snapshot", "fidelity", "report", "provenance"],
            "$.outcome",
        )
        string_of(complete[1], "$.outcome.target_source_id")
        _source_snapshot_v1_from_value(complete[2], SourceLimits())
        fidelity = string_of(complete[3], "$.outcome.fidelity")
        if fidelity not in ("Exact", "Transformed"):
            raise invalid("$.outcome.fidelity", "unknown materialization fidelity")
        _materialization_report_from_value(complete[4], registry)
        _materialization_provenance_from_value(complete[5])
    elif kind == "Failed":
        failed = exact_fields(
            outcome,
            ["kind", "failure", "report", "analyzed_input_paths"],
            "$.outcome",
        )
        _parse_failure(failed[1], "$.outcome.failure")
        _materialization_report_from_value(failed[2], registry)
        _parse_value_paths(failed[3], "$.outcome.analyzed_input_paths")
    else:
        raise invalid("$.outcome.kind", "unknown materialization outcome")


def _parse_value_paths(value: PortableValue, path: str) -> None:
    for index, item in enumerate(sequence_of(value, path)):
        ValuePath.from_value(item)


def _materialization_result_v2_from_value(value: PortableValue, registry: ErrorCodeRegistry) -> None:
    fields = schema_fields(
        value, "core.materialization-result@2", ["schema", "target_profile", "outcome"], "$"
    )
    _parse_profile_reference(fields[1], "$.target_profile")
    outcome = fields[2]
    if outcome.kind is not Kind.OBJECT:
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, "$.outcome", "expected Object")
    kind_entry = next((entry for entry in outcome.as_object() if entry[0] == "kind"), None)
    if kind_entry is None:
        raise invalid("$.outcome", "missing kind")
    kind = string_of(kind_entry[1], "$.outcome.kind")
    if kind == "Complete":
        complete = exact_fields(
            outcome,
            ["kind", "target_source_id", "snapshot", "fidelity", "report", "provenance"],
            "$.outcome",
        )
        target_source_id = string_of(complete[1], "$.outcome.target_source_id")
        if not target_source_id or len(target_source_id) > 4096:
            raise invalid("$.outcome.target_source_id", "invalid target source ID")
        _source_snapshot_v2_from_value(complete[2], SourceLimits())
        fidelity = string_of(complete[3], "$.outcome.fidelity")
        if fidelity not in ("Exact", "Transformed", "Lossy"):
            raise invalid("$.outcome.fidelity", "unknown materialization fidelity")
        _materialization_report_from_value(complete[4], registry)
        _materialization_provenance_from_value(complete[5])
    elif kind == "Failed":
        raise invalid("$.outcome.kind", "failed outcomes land with the source milestone")
    else:
        raise invalid("$.outcome.kind", "unknown materialization outcome")


# ---------------------------------------------------------------------------
# core.java-utf16-string@1 (java_utf16.rs)
# ---------------------------------------------------------------------------


def _parse_java_unit(text: str) -> int | None:
    if len(text) != 4:
        return None
    for character in text:
        if not ("0" <= character <= "9") and not ("A" <= character <= "F"):
            return None
    return int(text, 16)


def _java_utf16_from_value(value: PortableValue, limits: ProtocolLimits) -> None:
    """Strictly decodes and canonically re-verifies one exact Java string
    (java_utf16.rs:84-146)."""
    fields = schema_fields(
        value,
        "core.java-utf16-string@1",
        ["schema", "encoding", "code_units", "bytes", "unicode_status"],
        "$",
    )
    encoding = string_of(fields[1], "$.encoding")
    if encoding != "UTF16BE/1":
        raise invalid("$.encoding", "expected exact encoding UTF16BE/1")
    unit_values = sequence_of(fields[2], "$.code_units")
    if len(unit_values) > limits.max_container_entries:
        raise resource("$.code_units", "code-unit count exceeds the configured container limit")
    bytes_value = fields[3]
    if bytes_value.kind is not Kind.BYTES:
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, "$.bytes", "expected Bytes")
    raw_bytes = bytes_value.as_bytes()
    if len(raw_bytes) > limits.max_blob_bytes:
        raise resource("$.bytes", "UTF-16 bytes exceed the configured blob limit")
    if len(raw_bytes) % 2 != 0:
        raise invalid("$.bytes", "UTF-16 byte length must be even")
    if len(raw_bytes) != len(unit_values) * 2:
        raise invalid("$.bytes", "byte count does not equal two bytes per code unit")
    code_units = []
    for index, encoded in enumerate(unit_values):
        path = f"$.code_units[{index}]"
        text = string_of(encoded, path)
        unit = _parse_java_unit(text)
        if unit is None:
            raise invalid(path, "code unit must be exactly four uppercase hexadecimal digits")
        offset = index * 2
        if (unit >> 8) != raw_bytes[offset] or (unit & 0xFF) != raw_bytes[offset + 1]:
            raise invalid(path, "code unit and byte representation differ")
        code_units.append(unit)
    status_text = string_of(fields[4], "$.unicode_status")
    if status_text not in ("WellFormedUnicode", "UnpairedSurrogate"):
        raise invalid("$.unicode_status", "unknown Java Unicode status")
    status = JavaString.from_code_units(code_units).status()
    if status.value != status_text:
        raise invalid("$.unicode_status", "unicode status contradicts the code units")


# ---------------------------------------------------------------------------
# core.ini-query-result@1, core.java-properties-query-result@1 (line_query.rs)
# ---------------------------------------------------------------------------

_INI_ROLES = {
    MatchRole.INI_DOCUMENT,
    MatchRole.INI_SECTION,
    MatchRole.INI_DEFAULT_SECTION,
    MatchRole.INI_ENTRY,
    MatchRole.INI_PHYSICAL_LINE,
    MatchRole.INI_LOGICAL_LINE,
    MatchRole.INI_ERROR_LINE,
    MatchRole.INI_SYNTAX_PIECE,
}

_PROPERTIES_ROLES = {
    MatchRole.PROPERTIES_DOCUMENT,
    MatchRole.PROPERTIES_NATURAL_LINE,
    MatchRole.PROPERTIES_LOGICAL_LINE,
    MatchRole.PROPERTIES_PROPERTY,
    MatchRole.PROPERTIES_COMMENT,
    MatchRole.PROPERTIES_ESCAPE,
    MatchRole.PROPERTIES_ERROR_LINE,
    MatchRole.PROPERTIES_SYNTAX_PIECE,
}


def _line_query_result_from_value(
    value: PortableValue, schema: str, accept_role, roles: set, registry: ErrorCodeRegistry
) -> None:
    """Strictly decodes one line-format query result (line_query.rs)."""
    fields = schema_fields(
        value,
        schema,
        ["schema", "domain_id", "domain_version", "role", "matches", "completion", "diagnostics"],
        "$",
    )
    domain = QueryDomain(string_of(fields[1], "$.domain_id"), unsigned32(fields[2], "$.domain_version"))
    role = _parse_match_role(string_of(fields[3], "$.role"))
    if role is None or role not in roles:
        raise invalid("$.role", "unknown match role")
    if not accept_role(domain, role):
        raise invalid("$", "line query domain and result role are inconsistent")
    locators = sequence_of(fields[4], "$.matches")
    completion = Completion.from_value_with_registry(fields[5], registry)
    if completion.produced != len(locators):
        raise invalid("$", "completion count, role, or match ordinals are inconsistent")
    previous = 0
    for index, locator in enumerate(locators):
        locator_fields = exact_fields(
            locator, ["source_id", "node_locator", "role", "ordinal"], f"$.matches[{index}]"
        )
        locator_role = _parse_match_role(string_of(locator_fields[2], f"$.matches[{index}].role"))
        ordinal = unsigned64(locator_fields[3], f"$.matches[{index}].ordinal")
        if locator_role is not role:
            raise invalid("$", "completion count, role, or match ordinals are inconsistent")
        if index > 0 and ordinal <= previous:
            raise invalid("$", "completion count, role, or match ordinals are inconsistent")
        previous = ordinal
    sequence_of(fields[6], "$.diagnostics")


def _ini_domain_accepts_role(domain: QueryDomain, role: MatchRole) -> bool:
    if domain.id == "ini.native-semantic-query" and domain.version == 1:
        return role in _INI_ROLES and role is not MatchRole.INI_SYNTAX_PIECE
    if domain.id == "ini.lossless-syntax-query" and domain.version == 1:
        return role is MatchRole.INI_SYNTAX_PIECE
    return False


def _properties_domain_accepts_role(domain: QueryDomain, role: MatchRole) -> bool:
    if domain.id == "java-properties.native-semantic-query" and domain.version == 1:
        return role in _PROPERTIES_ROLES and role is not MatchRole.PROPERTIES_SYNTAX_PIECE
    if domain.id == "java-properties.lossless-syntax-query" and domain.version == 1:
        return role is MatchRole.PROPERTIES_SYNTAX_PIECE
    return False


def _ini_query_result_from_value(value: PortableValue, registry: ErrorCodeRegistry) -> None:
    _line_query_result_from_value(
        value, "core.ini-query-result@1", _ini_domain_accepts_role, _INI_ROLES, registry
    )


def _java_properties_query_result_from_value(
    value: PortableValue, registry: ErrorCodeRegistry
) -> None:
    _line_query_result_from_value(
        value,
        "core.java-properties-query-result@1",
        _properties_domain_accepts_role,
        _PROPERTIES_ROLES,
        registry,
    )


# ---------------------------------------------------------------------------
# core.portable-graph@1 (portable_graph.rs)
# ---------------------------------------------------------------------------


def _portable_graph_value(graph, pgce: bytes) -> PortableValue:
    """The exact readable graph plus PGCE/1 wire record (portable_graph.rs:44-126)."""
    order, canonical_ids = graph._canonical_layout()
    roots = [PortableValue.integer(canonical_ids[root.index]) for root in graph.roots()]
    nodes = []
    for wire_id, original in enumerate(order):
        node = graph._nodes[original]
        record = [
            ("id", PortableValue.integer(wire_id)),
            ("kind", PortableValue.string(node.kind.value)),
            ("tag", PortableValue.string(node.tag)),
        ]
        if node.content[0] == "scalar":
            record.append(("canonical_content", PortableValue.string(node.content[1])))
        elif node.content[0] == "sequence":
            items = [PortableValue.integer(canonical_ids[item.index]) for item in node.content[1]]
            record.append(("items", PortableValue.sequence(tuple(items))))
        else:
            entries = [
                PortableValue.object(
                    [
                        ("key", PortableValue.integer(canonical_ids[entry.key.index])),
                        ("value", PortableValue.integer(canonical_ids[entry.value.index])),
                    ]
                )
                for entry in node.content[1]
            ]
            record.append(("entries", PortableValue.sequence(tuple(entries))))
        nodes.append(PortableValue.object(record))
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.portable-graph@1")),
            ("encoding", PortableValue.string("PGCE/1")),
            ("roots", PortableValue.sequence(tuple(roots))),
            ("nodes", PortableValue.sequence(tuple(nodes))),
            ("pgce", PortableValue.bytes_value(pgce)),
        ]
    )


def _portable_graph_from_value(value: PortableValue, limits: PgceLimits):
    """Strictly decodes and cross-validates the readable graph and PGCE forms
    (portable_graph.rs:127-203)."""
    fields = schema_fields(
        value,
        "core.portable-graph@1",
        ["schema", "encoding", "roots", "nodes", "pgce"],
        "$",
    )
    encoding = string_of(fields[1], "$.encoding")
    if encoding != "PGCE/1":
        raise invalid("$.encoding", "expected PGCE/1")
    root_values = sequence_of(fields[2], "$.roots")
    node_values = sequence_of(fields[3], "$.nodes")
    _check_graph_count("$.roots", len(root_values), limits.max_roots)
    _check_graph_count("$.nodes", len(node_values), limits.max_nodes)
    pgce_value = fields[4]
    if pgce_value.kind is not Kind.BYTES:
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, "$.pgce", "expected Bytes")
    pgce_bytes = pgce_value.as_bytes()
    _check_graph_count("$.pgce", len(pgce_bytes), limits.max_stream_bytes)
    builder = GraphBuilder(limits.graph_limits())
    ids = [builder.reserve_node() for _ in node_values]
    for index, record_value in enumerate(node_values):
        path = f"$.nodes[{index}]"
        record = _node_record(record_value, index, path)
        kind = record[1]
        if kind == "Scalar":
            fields_s = exact_fields(
                record_value, ["id", "kind", "tag", "canonical_content"], path
            )
            try:
                builder.define_scalar(ids[index], string_of(fields_s[2], path + ".tag"), string_of(fields_s[3], path + ".canonical_content"))
            except GraphBuildError as error:
                raise _map_graph_build_error(error) from None
        elif kind == "Sequence":
            fields_s = exact_fields(record_value, ["id", "kind", "tag", "items"], path)
            items = []
            for item_index, item_value in enumerate(sequence_of(fields_s[3], path + ".items")):
                items.append(_resolve_graph_id(ids, item_value, path + f".items[{item_index}]"))
            try:
                builder.define_sequence(ids[index], string_of(fields_s[2], path + ".tag"), items)
            except GraphBuildError as error:
                raise _map_graph_build_error(error) from None
        else:
            fields_s = exact_fields(record_value, ["id", "kind", "tag", "entries"], path)
            entries = []
            for entry_index, entry_value in enumerate(sequence_of(fields_s[3], path + ".entries")):
                entry_path = path + f".entries[{entry_index}]"
                entry_fields = exact_fields(entry_value, ["key", "value"], entry_path)
                entries.append(
                    GraphMappingEntry(
                        _resolve_graph_id(ids, entry_fields[0], entry_path + ".key"),
                        _resolve_graph_id(ids, entry_fields[1], entry_path + ".value"),
                    )
                )
            try:
                builder.define_mapping(ids[index], string_of(fields_s[2], path + ".tag"), entries)
            except GraphBuildError as error:
                raise _map_graph_build_error(error) from None
    for index, root_value in enumerate(root_values):
        root_id = _resolve_graph_id(ids, root_value, f"$.roots[{index}]")
        try:
            builder.push_root(root_id)
        except GraphBuildError as error:
            raise _map_graph_build_error(error) from None
    try:
        built = builder.build()
    except GraphBuildError as error:
        raise _map_graph_build_error(error) from None
    order, _ = built._canonical_layout()
    if order != list(range(len(node_values))):
        raise invalid("$.nodes", "node records are not in canonical first-discovery order")
    try:
        decoded = decode_pgce(pgce_bytes, limits)
    except PgceDecodeError as error:
        raise invalid("$.pgce", str(error)) from None
    if built != decoded:
        raise invalid("$", "readable graph and PGCE graph are not strictly equal")
    try:
        canonical = encode_pgce_bounded(built, limits)
    except Exception as error:  # noqa: BLE001 — mapped to the pgce path
        raise invalid("$.pgce", str(error)) from None
    if canonical != pgce_bytes:
        raise invalid("$.pgce", "PGCE bytes disagree with readable graph")
    return built


def _node_record(value: PortableValue, index: int, path: str) -> tuple:
    if value.kind is not Kind.OBJECT or not value.as_object():
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected node Object")
    entries = value.as_object()
    if entries[0][0] != "id":
        raise invalid(path, "id must be the first field")
    canonical_id = unsigned64(entries[0][1], path + ".id")
    if canonical_id != index:
        raise invalid(path + ".id", "node records must carry canonical wire IDs")
    for key, item in entries[1:]:
        if key == "kind":
            return (canonical_id, string_of(item, path + ".kind"))
    raise invalid(path, "kind field is absent")


def _resolve_graph_id(ids, value: PortableValue, path: str):
    canonical = unsigned64(value, path)
    if canonical >= len(ids):
        raise invalid(path, "canonical node ID out of range")
    return ids[canonical]


def _check_graph_count(name: str, observed: int, limit: int) -> None:
    if observed > limit:
        raise resource(name, "count exceeds configured limit")


def _map_graph_build_error(error: GraphBuildError) -> ProtocolError:
    return invalid("$.nodes", str(error))


# ---------------------------------------------------------------------------
# core.graph-query-result@1 (graph_query.rs)
# ---------------------------------------------------------------------------

_GRAPH_ROLES = {
    MatchRole.GRAPH_NODE,
    MatchRole.GRAPH_SEQUENCE_ELEMENT,
    MatchRole.GRAPH_MAPPING_ENTRY,
}


def _graph_match_role(kind: str) -> MatchRole:
    if kind == "Node":
        return MatchRole.GRAPH_NODE
    if kind == "SequenceElement":
        return MatchRole.GRAPH_SEQUENCE_ELEMENT
    if kind == "MappingEntry":
        return MatchRole.GRAPH_MAPPING_ENTRY
    raise invalid("$", "unknown graph query match kind")


def _graph_match_from_value(value: PortableValue, path: str) -> dict:
    if value.kind is not Kind.OBJECT:
        raise invalid(path, "match must be Object")
    entries = value.as_object()
    if entries[0][0] != "kind":
        raise invalid(path, "match kind must be first")
    kind = string_of(entries[0][1], path + ".kind")
    if kind == "Node":
        fields = exact_fields(value, ["kind", "node"], path)
        return {"kind": "Node", "node": unsigned64(fields[1], path + ".node")}
    if kind == "SequenceElement":
        fields = exact_fields(value, ["kind", "parent", "ordinal", "node"], path)
        return {
            "kind": "SequenceElement",
            "parent": unsigned64(fields[1], path + ".parent"),
            "ordinal": unsigned64(fields[2], path + ".ordinal"),
            "node": unsigned64(fields[3], path + ".node"),
        }
    if kind == "MappingEntry":
        fields = exact_fields(value, ["kind", "parent", "ordinal", "key", "value"], path)
        return {
            "kind": "MappingEntry",
            "parent": unsigned64(fields[1], path + ".parent"),
            "ordinal": unsigned64(fields[2], path + ".ordinal"),
            "key": unsigned64(fields[3], path + ".key"),
            "value": unsigned64(fields[4], path + ".value"),
        }
    raise invalid(path, f"unknown graph match kind {kind!r}")


def _validate_graph_matches(graph, matches: list[dict]) -> None:
    """Resolves every match against the exact graph (graph_query.rs)."""
    order, _ = graph._canonical_layout()

    def resolve(canonical: int, path: str) -> int:
        if canonical >= len(order):
            raise invalid(path, "canonical node ID out of range")
        return order[canonical]

    for index, match in enumerate(matches):
        path = f"$.matches[{index}]"
        kind = match["kind"]
        if kind == "Node":
            resolve(match["node"], path + ".node")
        elif kind == "SequenceElement":
            parent = resolve(match["parent"], path + ".parent")
            child = resolve(match["node"], path + ".node")
            node = graph._nodes[parent]
            if node.content[0] != "sequence":
                raise invalid(path, "sequence element parent is not a sequence")
            items = node.content[1]
            if match["ordinal"] >= len(items):
                raise invalid(path, "sequence element ordinal out of range")
            if items[match["ordinal"]].index != child:
                raise invalid(path, "sequence element does not reference the child node")
        else:
            parent = resolve(match["parent"], path + ".parent")
            key = resolve(match["key"], path + ".key")
            value = resolve(match["value"], path + ".value")
            node = graph._nodes[parent]
            if node.content[0] != "mapping":
                raise invalid(path, "mapping entry parent is not a mapping")
            entries = node.content[1]
            if match["ordinal"] >= len(entries):
                raise invalid(path, "mapping entry ordinal out of range")
            entry = entries[match["ordinal"]]
            if entry.key.index != key or entry.value.index != value:
                raise invalid(path, "mapping entry does not reference the key/value nodes")


def _graph_query_result_from_value(value: PortableValue, registry: ErrorCodeRegistry) -> None:
    """Strictly decodes ``core.graph-query-result@1`` (graph_query.rs:64-100)."""
    fields = schema_fields(
        value,
        "core.graph-query-result@1",
        ["schema", "domain_id", "domain_version", "role", "graph", "matches", "completion", "diagnostics"],
        "$",
    )
    domain = QueryDomain(string_of(fields[1], "$.domain_id"), unsigned32(fields[2], "$.domain_version"))
    if domain.id != "core.portable-graph-query" or domain.version != 1:
        raise invalid("$", "graph result requires core.portable-graph-query@1")
    role = _parse_match_role(string_of(fields[3], "$.role"))
    if role is None or role not in _GRAPH_ROLES:
        raise invalid("$.role", "unknown graph match role")
    graph = _portable_graph_from_value(fields[4], PgceLimits())
    matches = [
        _graph_match_from_value(item, f"$.matches[{index}]")
        for index, item in enumerate(sequence_of(fields[5], "$.matches"))
    ]
    completion = Completion.from_value_with_registry(fields[6], registry)
    if completion.produced != len(matches):
        raise invalid("$", "completion count or graph match role is inconsistent")
    for match in matches:
        if _graph_match_role(match["kind"]) is not role:
            raise invalid("$", "completion count or graph match role is inconsistent")
    _validate_graph_matches(graph, matches)
    sequence_of(fields[7], "$.diagnostics")


# ---------------------------------------------------------------------------
# core.graph-provenance-map@1, core.graph-projection-result@1
# (graph_projection.rs)
# ---------------------------------------------------------------------------

_GRAPH_LOCATION_RANK = {"Root": 0, "Node": 1, "SequenceElement": 2, "MappingKey": 3, "MappingValue": 4}


def _graph_location_less(left: dict, right: dict) -> bool:
    if left["kind"] != right["kind"]:
        return _GRAPH_LOCATION_RANK[left["kind"]] < _GRAPH_LOCATION_RANK[right["kind"]]
    kind = left["kind"]
    if kind == "Root":
        return left["ordinal"] < right["ordinal"]
    if kind == "Node":
        return left["node"] < right["node"]
    if left["parent"] != right["parent"]:
        return left["parent"] < right["parent"]
    return left["ordinal"] < right["ordinal"]


def _parse_graph_location(value: PortableValue, path: str) -> dict:
    if value.kind is not Kind.OBJECT or not value.as_object():
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected location Object")
    entries = value.as_object()
    if entries[0][0] != "kind":
        raise invalid(path, "kind must be the first String field")
    kind = string_of(entries[0][1], path + ".kind")
    if kind == "Root":
        fields = exact_fields(value, ["kind", "ordinal"], path)
        return {"kind": "Root", "ordinal": unsigned64(fields[1], path + ".ordinal")}
    if kind == "Node":
        fields = exact_fields(value, ["kind", "node"], path)
        return {"kind": "Node", "node": unsigned64(fields[1], path + ".node")}
    if kind in ("SequenceElement", "MappingKey", "MappingValue"):
        fields = exact_fields(value, ["kind", "parent", "ordinal"], path)
        return {
            "kind": kind,
            "parent": unsigned64(fields[1], path + ".parent"),
            "ordinal": unsigned64(fields[2], path + ".ordinal"),
        }
    raise invalid(path, "unknown projected graph location")


def _parse_graph_origin(value: PortableValue, path: str) -> PortableValue:
    fields = exact_fields(value, ["source_id", "node_locator", "start_byte", "end_byte", "relation"], path)
    source_id = string_of(fields[0], path + ".source_id")
    node_locator = optional_string(fields[1], path + ".node_locator")
    start_byte = unsigned64(fields[2], path + ".start_byte")
    end_byte = unsigned64(fields[3], path + ".end_byte")
    relation = string_of(fields[4], path + ".relation")
    if relation not in ("Direct", "Reference"):
        raise invalid(path + ".relation", "unknown graph provenance relation")
    if (
        not source_id
        or len(source_id) > 1024
        or start_byte > end_byte
        or (node_locator is not None and (node_locator == "" or len(node_locator) > 4096))
    ):
        raise invalid("$.origin", "invalid source identity, locator, or half-open range")
    return PortableValue.object(
        [
            ("source_id", PortableValue.string(source_id)),
            ("node_locator", nullable_string(node_locator)),
            ("start_byte", PortableValue.integer(start_byte)),
            ("end_byte", PortableValue.integer(end_byte)),
            ("relation", PortableValue.string(relation)),
        ]
    )


def _graph_provenance_entries(value: PortableValue) -> list[dict]:
    """Strictly decodes and sorts the ordered provenance entries
    (graph_projection.rs:121-141)."""
    fields = schema_fields(value, "core.graph-provenance-map@1", ["schema", "entries"], "$")
    entries = []
    for index, entry_value in enumerate(sequence_of(fields[1], "$.entries")):
        entry_path = f"$.entries[{index}]"
        entry_fields = exact_fields(entry_value, ["projected", "origins"], entry_path)
        projected = _parse_graph_location(entry_fields[0], entry_path + ".projected")
        origins = [
            _parse_graph_origin(item, entry_path + f".origins[{origin_index}]")
            for origin_index, item in enumerate(sequence_of(entry_fields[1], entry_path + ".origins"))
        ]
        entries.append({"projected": projected, "origins": origins})
    for entry in entries:
        if not entry["origins"]:
            raise invalid("$.entries", "graph provenance locations must be sorted, unique, and have origins")
    for index in range(1, len(entries)):
        if not _graph_location_less(entries[index - 1]["projected"], entries[index]["projected"]):
            raise invalid("$.entries", "graph provenance locations must be sorted, unique, and have origins")
    return entries


def _validate_graph_locations(graph, entries: list[dict]) -> None:
    """Validates every projected location against the exact graph
    (graph_projection.rs:143-158)."""
    order, _ = graph._canonical_layout()

    def resolve(canonical: int, name: str, path: str) -> int:
        if canonical >= len(order):
            raise invalid(path + "." + name, "canonical node ID out of range")
        return order[canonical]

    for index, entry in enumerate(entries):
        path = f"$.entries[{index}].projected"
        location = entry["projected"]
        kind = location["kind"]
        if kind == "Root":
            if location["ordinal"] >= len(graph.roots()):
                raise invalid(path, "root ordinal out of range")
        elif kind == "Node":
            resolve(location["node"], "node", path)
        elif kind == "SequenceElement":
            parent = resolve(location["parent"], "parent", path)
            node = graph._nodes[parent]
            if node.content[0] != "sequence":
                raise invalid(path, "sequence element parent is not a sequence")
            if location["ordinal"] >= len(node.content[1]):
                raise invalid(path, "sequence element ordinal out of range")
        elif kind in ("MappingKey", "MappingValue"):
            parent = resolve(location["parent"], "parent", path)
            node = graph._nodes[parent]
            if node.content[0] != "mapping":
                raise invalid(path, "mapping location parent is not a mapping")
            if location["ordinal"] >= len(node.content[1]):
                raise invalid(path, "mapping location ordinal out of range")
        else:
            raise invalid(path, "unknown projected graph location")


def _graph_provenance_map_from_value(value: PortableValue) -> None:
    _graph_provenance_entries(value)


def _graph_projection_result_from_value(
    value: PortableValue, registry: ErrorCodeRegistry
) -> None:
    """Strictly decodes ``core.graph-projection-result@1``
    (graph_projection.rs:245-...)."""
    fields = schema_fields(
        value,
        "core.graph-projection-result@1",
        ["schema", "completion", "graph", "provenance", "diagnostics"],
        "$",
    )
    completion = Completion.from_value_with_registry(fields[1], registry)
    success = completion.status is CompletionStatus.SUCCESS
    graph_value = fields[2]
    has_graph = graph_value.kind is not Kind.NULL
    if success != has_graph:
        raise invalid("$", "only a successful graph projection carries a graph")
    provenance = _graph_provenance_entries(fields[3])
    if success:
        if graph_value.kind is not Kind.OBJECT:
            raise protocol_error(ProtocolErrorKind.WRONG_TYPE, "$.graph", "expected Object")
        graph_member = exact_fields(graph_value, ["portable_graph"], "$.graph")
        graph = _portable_graph_from_value(graph_member[0], PgceLimits())
        _validate_graph_locations(graph, provenance)
    else:
        if provenance:
            raise invalid("$.provenance", "failed projection cannot claim complete provenance")
    sequence_of(fields[4], "$.diagnostics")


# ---------------------------------------------------------------------------
# core.yaml-query-result@1 (yaml_query.rs)
# ---------------------------------------------------------------------------

_YAML_ROLES = {
    MatchRole.YAML_STREAM,
    MatchRole.YAML_DOCUMENT,
    MatchRole.YAML_NODE,
    MatchRole.YAML_MAPPING_ENTRY,
    MatchRole.YAML_SEQUENCE_ELEMENT,
    MatchRole.YAML_ANCHOR_DEFINITION,
    MatchRole.YAML_ALIAS_OCCURRENCE,
    MatchRole.YAML_SYNTAX_PIECE,
}


def _yaml_domain_accepts_role(domain: QueryDomain, role: MatchRole) -> bool:
    if domain.id == "yaml.native-semantic-query" and domain.version == 1:
        return role in _YAML_ROLES and role is not MatchRole.YAML_SYNTAX_PIECE
    if domain.id == "yaml.lossless-syntax-query" and domain.version == 1:
        return role is MatchRole.YAML_SYNTAX_PIECE
    return False


def _yaml_query_result_from_value(value: PortableValue, registry: ErrorCodeRegistry) -> None:
    """Strictly decodes ``core.yaml-query-result@1`` (yaml_query.rs:85-120)."""
    fields = schema_fields(
        value,
        "core.yaml-query-result@1",
        ["schema", "domain_id", "domain_version", "role", "matches", "completion", "diagnostics"],
        "$",
    )
    domain = QueryDomain(string_of(fields[1], "$.domain_id"), unsigned32(fields[2], "$.domain_version"))
    role = _parse_match_role(string_of(fields[3], "$.role"))
    if role is None or role not in _YAML_ROLES:
        raise invalid("$.role", "unknown YAML match role")
    if not _yaml_domain_accepts_role(domain, role):
        raise invalid("$", "YAML query domain and result role are inconsistent")
    locators = sequence_of(fields[4], "$.matches")
    completion = Completion.from_value_with_registry(fields[5], registry)
    if completion.produced != len(locators):
        raise invalid("$", "completion count, role, or YAML match ordinals are inconsistent")
    previous = 0
    for index, locator in enumerate(locators):
        locator_fields = exact_fields(
            locator, ["source_id", "node_locator", "role", "ordinal"], f"$.matches[{index}]"
        )
        locator_role = _parse_match_role(string_of(locator_fields[2], f"$.matches[{index}].role"))
        ordinal = unsigned64(locator_fields[3], f"$.matches[{index}].ordinal")
        if locator_role is not role:
            raise invalid("$", "completion count, role, or YAML match ordinals are inconsistent")
        if index > 0 and ordinal <= previous:
            raise invalid("$", "completion count, role, or YAML match ordinals are inconsistent")
        previous = ordinal
    sequence_of(fields[6], "$.diagnostics")


# ---------------------------------------------------------------------------
# core.conversion-report@1 (conversion.rs)
# ---------------------------------------------------------------------------


def _parse_fidelity(value: PortableValue, path: str) -> str:
    text = string_of(value, path)
    if text not in ("Exact", "Transformed", "Lossy"):
        raise invalid(path, "unknown conversion fidelity")
    return text


def _parse_materialization_fidelity(value: PortableValue, path: str) -> str:
    text = string_of(value, path)
    if text not in ("Exact", "Transformed"):
        raise invalid(path, "unknown materialization fidelity")
    return text


def _conversion_report_from_value(value: PortableValue, registry: ErrorCodeRegistry) -> None:
    """Strictly decodes both stage reports under one semantic-model registry
    (conversion.rs:175-209)."""
    fields = schema_fields(
        value,
        "core.conversion-report@1",
        ["schema", "source_profile", "target_profile", "projection_fidelity",
         "projection_report", "materialization_fidelity", "materialization_report",
         "overall_fidelity"],
        "$",
    )
    _parse_profile_reference(fields[1], "$.source_profile")
    _parse_profile_reference(fields[2], "$.target_profile")
    projection_fidelity = _parse_fidelity(fields[3], "$.projection_fidelity")
    projection_report = ProjectionReportMessage.from_value_with_registry(fields[4], registry)
    materialization_fidelity = _parse_materialization_fidelity(fields[5], "$.materialization_fidelity")
    materialization_report = _materialization_report_fields(fields[6], registry)
    overall_fidelity = _parse_fidelity(fields[7], "$.overall_fidelity")
    has_reversible = any(
        event.loss_classification is LossClassification.REVERSIBLE
        for event in projection_report.events
    )
    has_loss = any(
        event.loss_classification is LossClassification.LOSSY
        for event in projection_report.events
    )
    projection_valid = (
        (projection_fidelity == "Exact" and not has_reversible and not has_loss)
        or (projection_fidelity == "Transformed" and has_reversible and not has_loss)
        or (projection_fidelity == "Lossy" and has_loss)
    )
    if not projection_valid:
        raise invalid("$.projection_report", "projection fidelity contradicts its complete event report")
    has_materialization_transform = any(
        event.code == "core.materialization.mapping-transformed@1"
        for event in materialization_report
    )
    if (materialization_fidelity == "Transformed") != has_materialization_transform:
        raise invalid(
            "$.materialization_report", "materialization fidelity contradicts its complete event report"
        )
    materialization_overall = (
        "Exact" if materialization_fidelity == "Exact" else "Transformed"
    )
    worst = (
        projection_fidelity
        if _fidelity_rank(projection_fidelity) >= _fidelity_rank(materialization_overall)
        else materialization_overall
    )
    if overall_fidelity != worst:
        raise invalid("$.overall_fidelity", "overall fidelity is not the worst complete stage fidelity")


def _fidelity_rank(fidelity: str) -> int:
    return {"Exact": 0, "Transformed": 1, "Lossy": 2}[fidelity]


def _materialization_report_fields(value: PortableValue, registry: ErrorCodeRegistry) -> list:
    fields = schema_fields(value, "core.materialization-report@1", ["schema", "events"], "$")
    events = []
    for index, event in enumerate(sequence_of(fields[1], "$.events")):
        events.append(Diagnostic.from_value(event, registry))
    return events


# ---------------------------------------------------------------------------
# core.edit-plan@1, core.format-operation-registry@1 (operation.rs)
# ---------------------------------------------------------------------------


def _edit_plan_from_value(value: PortableValue, registry: ErrorCodeRegistry) -> None:
    """Strictly decodes and revalidates a dry-run plan (operation.rs:334-381)."""
    fields = schema_fields(
        value,
        "core.edit-plan@1",
        ["schema", "source_id", "base_digest", "profile", "operations",
         "replacements", "target_digest", "report"],
        "$",
    )
    source_id = string_of(fields[1], "$.source_id")
    if not source_id or len(source_id) > 1024:
        raise invalid("$.source_id", "invalid source ID")
    _parse_digest(fields[2], "$.base_digest")
    _parse_profile_reference(fields[3], "$.profile")
    for index, operation in enumerate(sequence_of(fields[4], "$.operations")):
        path = f"$.operations[{index}]"
        operation_fields = exact_fields(operation, ["operation", "summary"], path)
        _parse_reference(operation_fields[0], path + ".operation")
        string_map_from_object(operation_fields[1], path + ".summary")
    _parse_replacements(fields[5], "$.replacements", SourcePatchLimits())
    _parse_digest(fields[6], "$.target_digest")
    for index, event in enumerate(sequence_of(fields[7], "$.report")):
        Diagnostic.from_value(event, registry)


_ARGUMENT_KINDS = ("NodeRef", "String", "PortableValue", "Placement", "ExactBytes", "RepresentationPolicy")
_SUPPORT_KINDS = ("Supported", "ExistingTypedCapability", "Unsupported")


def _format_operation_registry_from_value(value: PortableValue) -> None:
    """Strictly decodes and revalidates IDs, schemas, order, and uniqueness
    (operation.rs:83-99)."""
    fields = schema_fields(
        value,
        "core.format-operation-registry@1",
        ["schema", "profile", "operations"],
        "$",
    )
    _parse_profile_reference(fields[1], "$.profile")
    seen_ids = set()
    for index, operation in enumerate(sequence_of(fields[2], "$.operations")):
        path = f"$.operations[{index}]"
        operation_fields = exact_fields(
            operation, ["operation", "target_role", "arguments", "support"], path
        )
        operation_id = _parse_reference(operation_fields[0], path + ".operation")
        _parse_reference(operation_fields[1], path + ".target_role")
        for argument_index, argument in enumerate(sequence_of(operation_fields[2], path + ".arguments")):
            argument_path = f"{path}.arguments[{argument_index}]"
            argument_fields = exact_fields(argument, ["name", "kind", "required"], argument_path)
            name = string_of(argument_fields[0], argument_path + ".name")
            if not name:
                raise invalid(argument_path + ".name", "argument name cannot be empty")
            kind = string_of(argument_fields[1], argument_path + ".kind")
            if kind not in _ARGUMENT_KINDS:
                raise invalid(argument_path + ".kind", "unknown operation argument kind")
            boolean_of(argument_fields[2], argument_path + ".required")
        support = string_of(operation_fields[3], path + ".support")
        if support not in _SUPPORT_KINDS:
            raise invalid(path + ".support", "unknown operation support")
        if operation_id in seen_ids:
            raise invalid(path + ".operation", "operation IDs must be unique")
        seen_ids.add(operation_id)


# ---------------------------------------------------------------------------
# dispatch (payload.rs)
# ---------------------------------------------------------------------------


def validate_registered_payload(
    contract: ContractId, payload: PortableValue, registry: ContractRegistry
) -> None:
    """Validates a registered contract's payload with its full record decoder
    (payload.rs)."""
    error_registry = ErrorCodeRegistry(registry.version)
    key = f"{contract.id}@{contract.version}"
    if key == "core.batch-plan@1":
        BatchPlanMessage.from_value(payload, error_registry)
    elif key == "core.batch-result@1":
        BatchResultMessage.from_value(payload)
    elif key == "core.cancellation-request@1":
        CancellationRequest.from_value(payload)
    elif key == "core.capability-declaration@1":
        CapabilityDeclaration.from_value(payload)
    elif key == "core.change-set@1":
        ChangeSetMessage.from_value_with_registry(payload, error_registry)
    elif key == "core.cli-output@1":
        CliOutputMessage.from_value(payload, error_registry)
    elif key == "core.completion@1":
        Completion.from_value_with_registry(payload, error_registry)
    elif key == "core.conversion-report@1":
        _conversion_report_from_value(payload, error_registry)
    elif key == "core.diagnostic@1":
        Diagnostic.from_value(payload, error_registry)
    elif key == "core.edit-plan@1":
        _edit_plan_from_value(payload, error_registry)
    elif key == "core.error-code-registry@1":
        validate_error_code_manifest_value(payload)
    elif key == "core.execution-policy@1":
        ExecutionPolicy.from_value(payload)
    elif key == "core.format-operation-registry@1":
        _format_operation_registry_from_value(payload)
    elif key == "core.graph-projection-result@1":
        _graph_projection_result_from_value(payload, error_registry)
    elif key == "core.graph-provenance-map@1":
        _graph_provenance_map_from_value(payload)
    elif key == "core.graph-query-result@1":
        _graph_query_result_from_value(payload, error_registry)
    elif key == "core.ini-query-result@1":
        _ini_query_result_from_value(payload, error_registry)
    elif key == "core.java-properties-query-result@1":
        _java_properties_query_result_from_value(payload, error_registry)
    elif key == "core.java-utf16-string@1":
        _java_utf16_from_value(payload, ProtocolLimits())
    elif key == "core.materialization-provenance-map@1":
        _materialization_provenance_from_value(payload)
    elif key == "core.materialization-report@1":
        _materialization_report_from_value(payload, error_registry)
    elif key == "core.materialization-request@1":
        _materialization_request_v1_from_value(payload)
    elif key == "core.materialization-request@2":
        _materialization_request_v2_from_value(payload)
    elif key == "core.materialization-result@1":
        _materialization_result_v1_from_value(payload, error_registry)
    elif key == "core.materialization-result@2":
        _materialization_result_v2_from_value(payload, error_registry)
    elif key == "core.profile-descriptor@1":
        ProfileDescriptor.from_value(payload)
    elif key == "core.portable-graph@1":
        _portable_graph_from_value(payload, PgceLimits())
    elif key == "core.projection-report@1":
        ProjectionReportMessage.from_value_with_registry(payload, error_registry)
    elif key == "core.projection-request@1":
        ProjectionRequestMessage.from_value(payload)
    elif key == "core.projection-result@1":
        ProjectionResultMessage.from_value_with_registry(payload, error_registry)
    elif key == "core.provenance-map@1":
        ProvenanceMapMessage.from_value(payload)
    elif key == "core.query-definition@1":
        try:
            QueryDefinitionCodec.from_value(payload)
        except ProtocolError as error:
            raise protocol_error(
                error.kind, "$.payload", f"invalid query definition: {error.detail}"
            ) from None
    elif key == "core.query-result@1":
        QueryResultMessage.from_value_with_registry(payload, error_registry)
    elif key == "core.registry-manifest@1":
        RegistryManifest.from_value(payload)
    elif key == "core.source-encoding@1":
        parse_source_encoding_value(payload, "$")
    elif key == "core.source-patch@1":
        _source_patch_v1_from_value(payload, SourcePatchLimits())
    elif key == "core.source-patch@2":
        _source_patch_v2_from_value(payload, SourcePatchLimits())
    elif key == "core.source-snapshot@1":
        _source_snapshot_v1_from_value(payload, SourceLimits())
    elif key == "core.source-snapshot@2":
        _source_snapshot_v2_from_value(payload, SourceLimits())
    elif key == "core.yaml-query-result@1":
        _yaml_query_result_from_value(payload, error_registry)
