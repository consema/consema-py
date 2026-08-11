"""Runner-side protocol record codecs.

The Python protocol package implements the contract/error registries, the
canonical transports, the envelope, diagnostics, registry descriptors, and
the CLI records, but not the v1 record codecs that the protocol vectors
exercise (completion, cancellation-request, execution-policy,
projection-request/report/result, provenance-map, query-result,
change-set). This module transcribes those codecs from the Rust protocol
crate (crates/consema-protocol/src/execution.rs, projection.rs, query.rs,
change.rs — the registry/byte authority), with go/protocol (records_*.go)
as cross-reference only.

Every record follows the fixed-field schema discipline of the semantic
model: exact field sets, ``schema`` discriminator, canonical tagged JSON
and PVCE transports, and typed rejections via :class:`ProtocolError`.
"""

from __future__ import annotations

import enum

from consema.core.value import Kind, PortableValue
from consema.protocol.contract import ContractId, ContractRegistry
from consema.protocol.error_registry import ErrorCodeRegistry
from consema.protocol.errors import ProtocolError, ProtocolErrorKind, invalid, protocol_error
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
from consema.protocol.query import MatchRole, QueryDomain, QueryDefinition, QueryDefinitionCodec


def process_local_error(path: str) -> ProtocolError:
    """The frozen process-local-handle rejection."""
    return protocol_error(
        ProtocolErrorKind.PROCESS_LOCAL_HANDLE,
        path,
        "process-local handle must be externalized to a stable caller identity",
    )


def _reference_value(contract: ContractId) -> PortableValue:
    return PortableValue.object(
        [("id", PortableValue.string(contract.id)), ("version", PortableValue.integer(contract.version))]
    )


def _parse_reference(value: PortableValue, path: str) -> ContractId:
    fields = exact_fields(value, ["id", "version"], path)
    identifier = string_of(fields[0], path + ".id")
    version = unsigned32(fields[1], path + ".version")
    return ContractId(identifier, version)


# ---------------------------------------------------------------------------
# core.completion@1, core.cancellation-request@1, core.execution-policy@1
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
    def new(cls, status, processed, produced, limit_name=None, failure_code=None):
        """Validates the state-specific completion invariants against the
        semantic-model v1 error registry (execution.rs:51-67)."""
        from consema.protocol.error_registry import ErrorCodeRegistry

        if failure_code is not None:
            ErrorCodeRegistry(1).validate(failure_code, "$.failure_code")
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

    def to_value(self) -> PortableValue:
        return PortableValue.object(
            [
                ("schema", PortableValue.string("core.completion@1")),
                ("status", PortableValue.string(self.status.value)),
                ("processed", PortableValue.integer(self.processed)),
                ("produced", PortableValue.integer(self.produced)),
                ("limit_name", _nullable_string(self.limit_name)),
                ("failure_code", _nullable_string(self.failure_code)),
            ]
        )

    @classmethod
    def from_value(cls, value: PortableValue) -> Completion:
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
        return cls.new(status, processed, produced, limit_name, failure_code)


def _nullable_string(value: str | None) -> PortableValue:
    if value is None:
        return PortableValue.null()
    return PortableValue.string(value)


def _parse_completion_status(value: PortableValue, path: str):
    text = string_of(value, path)
    try:
        return CompletionStatus(text)
    except ValueError:
        raise invalid(path, "unknown completion status") from None


class ExecutionPolicy:
    """The transferable ``core.execution-policy@1`` record
    (execution.rs:189-195)."""

    __slots__ = ("limits", "cancellation_request_id")

    def __init__(self, limits, cancellation_request_id=None):
        self.limits = limits
        self.cancellation_request_id = cancellation_request_id

    @classmethod
    def new(cls, limits, cancellation_request_id=None):
        for name in limits:
            if not _valid_limit_name(name):
                raise invalid("$.limits", "limit names must be stable lowercase identifiers")
        if cancellation_request_id is not None and (
            cancellation_request_id == "" or len(cancellation_request_id) > 1024
        ):
            raise invalid("$.cancellation_request_id", "invalid cancellation request ID")
        return cls(dict(limits), cancellation_request_id)

    def to_value(self) -> PortableValue:
        names = sorted(self.limits)
        return PortableValue.object(
            [
                ("schema", PortableValue.string("core.execution-policy@1")),
                (
                    "limits",
                    PortableValue.object(
                        tuple(
                            (name, PortableValue.integer(self.limits[name])) for name in names
                        )
                    ),
                ),
                ("cancellation_request_id", _nullable_string(self.cancellation_request_id)),
            ]
        )

    @classmethod
    def from_value(cls, value: PortableValue) -> ExecutionPolicy:
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
        cancellation_id = optional_string(fields[2], "$.cancellation_request_id")
        return cls.new(limits, cancellation_id)


def _valid_limit_name(name: str) -> bool:
    if not name or len(name) > 255:
        return False
    return all(
        ("a" <= character <= "z") or ("0" <= character <= "9") or character in "_"
        for character in name
    )


class CancellationRequest:
    """The idempotent outer-transport ``core.cancellation-request@1`` record
    (execution.rs:279-290)."""

    __slots__ = ("request_id", "reason")

    def __init__(self, request_id: str, reason=None):
        self.request_id = request_id
        self.reason = reason

    @classmethod
    def new(cls, request_id: str, reason=None):
        if not request_id or len(request_id) > 1024:
            raise invalid("$.request_id", "invalid request ID")
        return cls(request_id, reason)

    def to_value(self) -> PortableValue:
        return PortableValue.object(
            [
                ("schema", PortableValue.string("core.cancellation-request@1")),
                ("request_id", PortableValue.string(self.request_id)),
                ("reason", _nullable_string(self.reason)),
            ]
        )

    @classmethod
    def from_value(cls, value: PortableValue) -> CancellationRequest:
        fields = schema_fields(
            value,
            "core.cancellation-request@1",
            ["schema", "request_id", "reason"],
            "$",
        )
        request_id = string_of(fields[1], "$.request_id")
        reason = optional_string(fields[2], "$.reason")
        return cls.new(request_id, reason)


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
    def root(cls) -> ValuePath:
        return cls()

    def to_value(self) -> PortableValue:
        items = []
        for kind, key in self.segments:
            key_value = PortableValue.integer(key) if isinstance(key, int) else PortableValue.string(key)
            items.append(PortableValue.object([("kind", PortableValue.string(kind)), ("key", key_value)]))
        return PortableValue.object(
            [
                ("schema", PortableValue.string("core.value-path@1")),
                ("segments", PortableValue.sequence(tuple(items))),
            ]
        )

    @classmethod
    def from_value(cls, value: PortableValue) -> ValuePath:
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

    def equal(self, other: ValuePath) -> bool:
        return self.segments == other.segments

    def less(self, other: ValuePath) -> bool:
        return _path_less(self.segments, other.segments)


def _path_less(left, right) -> bool:
    for left_segment, right_segment in zip(left, right):
        if left_segment[0] != right_segment[0]:
            return left_segment[0] < right_segment[0]
        if left_segment[1] != right_segment[1]:
            left_key = left_segment[1]
            right_key = right_segment[1]
            if isinstance(left_key, int) and isinstance(right_key, int):
                return left_key < right_key
            return str(left_key) < str(right_key)
    return len(left) < len(right)


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

    def to_value(self) -> PortableValue:
        return PortableValue.object(
            [
                ("schema", PortableValue.string("core.association-location@1")),
                ("path", self.path.to_value()),
                ("ordinal", PortableValue.integer(self.ordinal)),
                ("role", PortableValue.string(self.role.value)),
            ]
        )

    @classmethod
    def from_value(cls, value: PortableValue) -> AssociationLocation:
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

    def equal(self, other: AssociationLocation) -> bool:
        return (
            self.path.equal(other.path)
            and self.ordinal == other.ordinal
            and self.role is other.role
        )

    def less(self, other: AssociationLocation) -> bool:
        if not self.path.equal(other.path):
            return self.path.less(other.path)
        if self.ordinal != other.ordinal:
            return self.ordinal < other.ordinal
        return self.role.value < other.role.value


# ---------------------------------------------------------------------------
# core.projection-request@1
# ---------------------------------------------------------------------------


class ProjectionPolicy:
    __slots__ = ("contract", "arguments")

    def __init__(self, contract: ContractId, arguments):
        self.contract = contract
        self.arguments = dict(arguments)

    def equal(self, other: ProjectionPolicy) -> bool:
        if self.contract != other.contract or len(self.arguments) != len(other.arguments):
            return False
        from consema.core.equal import equal as core_equal

        for name, value in self.arguments.items():
            if name not in other.arguments or not core_equal(value, other.arguments[name]):
                return False
        return True

    def to_value(self) -> PortableValue:
        names = sorted(self.arguments)
        return PortableValue.object(
            [
                ("id", PortableValue.string(self.contract.id)),
                ("version", PortableValue.integer(self.contract.version)),
                (
                    "arguments",
                    PortableValue.object(
                        tuple((name, self.arguments[name]) for name in names)
                    ),
                ),
            ]
        )

    @classmethod
    def from_value(cls, value: PortableValue, path: str) -> ProjectionPolicy:
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

    def to_value(self) -> PortableValue:
        if self.kind == "Global":
            return PortableValue.object([("kind", PortableValue.string("Global"))])
        if self.kind == "ExactNativePath":
            return PortableValue.object(
                [
                    ("kind", PortableValue.string("ExactNativePath")),
                    ("source_id", PortableValue.string(self.source_id)),
                    ("path", PortableValue.string(self.path)),
                ]
            )
        if self.kind == "ResolvedQuery":
            query = QueryDefinitionCodec.to_value(self.query)
            return PortableValue.object(
                [
                    ("kind", PortableValue.string("ResolvedQuery")),
                    ("query", query),
                ]
            )
        raise invalid("$.scope", "unknown projection scope")

    @classmethod
    def from_value(cls, value: PortableValue, path: str) -> ProjectionScope:
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
            return cls("ExactNativePath", string_of(fields[1], path + ".source_id"), string_of(fields[2], path + ".path"))
        if kind == "ResolvedQuery":
            fields = exact_fields(value, ["kind", "query"], path)
            return cls("ResolvedQuery", query=QueryDefinitionCodec.from_value(fields[1]))
        raise invalid(path, "unknown projection scope")


class ProjectionRule:
    __slots__ = ("rule_id", "scope", "priority", "policy")

    def __init__(self, rule_id: str, scope: ProjectionScope, priority: int, policy: ProjectionPolicy):
        self.rule_id = rule_id
        self.scope = scope
        self.priority = priority
        self.policy = policy

    def to_value(self) -> PortableValue:
        return PortableValue.object(
            [
                ("rule_id", PortableValue.string(self.rule_id)),
                ("scope", self.scope.to_value()),
                ("priority", PortableValue.integer(self.priority)),
                ("policy", self.policy.to_value()),
            ]
        )

    @classmethod
    def from_value(cls, value: PortableValue, path: str) -> ProjectionRule:
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
    def new(cls, target: ContractId, default_policy: ProjectionPolicy, rules, limits):
        rule_ids = set()
        for rule in rules:
            if not rule.rule_id or len(rule.rule_id) > 255 or rule.rule_id in rule_ids:
                raise invalid("$.rules", "rule IDs must be non-empty and unique")
            rule_ids.add(rule.rule_id)
            _validate_scope(rule.scope)
        for index, left in enumerate(rules):
            for right in rules[index + 1:]:
                if (
                    left.priority == right.priority
                    and _scope_equal(left.scope, right.scope)
                    and not left.policy.equal(right.policy)
                ):
                    raise invalid("$.rules", "same-scope same-priority policies conflict")
        for name in limits:
            if not _valid_limit_name(name):
                raise invalid("$.limits", "limit names must be stable lowercase identifiers")
        return cls(target, default_policy, rules, limits)

    def to_value(self) -> PortableValue:
        names = sorted(self.limits)
        return PortableValue.object(
            [
                ("schema", PortableValue.string("core.projection-request@1")),
                ("target", _reference_value(self.target)),
                ("default_policy", self.default_policy.to_value()),
                ("rules", PortableValue.sequence(tuple(rule.to_value() for rule in self.rules))),
                (
                    "limits",
                    PortableValue.object(
                        tuple((name, PortableValue.integer(self.limits[name])) for name in names)
                    ),
                ),
            ]
        )

    @classmethod
    def from_value(cls, value: PortableValue) -> ProjectionRequestMessage:
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
        return cls.new(target, default_policy, rules, limits)


def _validate_scope(scope: ProjectionScope) -> None:
    if scope.kind == "Global":
        return
    if scope.kind == "ExactNativePath":
        if (
            not scope.source_id
            or len(scope.source_id) > 1024
            or not scope.path
            or len(scope.path) > 4096
        ):
            raise invalid("$.scope", "invalid exact native path scope")
        return
    if scope.kind == "ResolvedQuery":
        if scope.query is None:
            raise invalid("$.scope.query", "invalid query scope")
        scope.query.validate()
        return
    raise invalid("$.scope", "unknown projection scope")


def _scope_equal(left: ProjectionScope, right: ProjectionScope) -> bool:
    if left.kind != right.kind:
        return False
    if left.kind == "Global":
        return True
    if left.kind == "ExactNativePath":
        return left.source_id == right.source_id and left.path == right.path
    if left.kind == "ResolvedQuery":
        from consema.core.equal import equal as core_equal

        if left.query is None or right.query is None:
            return left.query is right.query
        return core_equal(
            QueryDefinitionCodec.to_value(left.query),
            QueryDefinitionCodec.to_value(right.query),
        )
    return False


# ---------------------------------------------------------------------------
# core.projection-report@1 and core.provenance-map@1
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
    def new(cls, source_id, node_locator, start_byte, end_byte, relation):
        if (
            not source_id
            or len(source_id) > 1024
            or start_byte > end_byte
            or (node_locator is not None and (node_locator == "" or len(node_locator) > 4096))
        ):
            raise invalid("$.origin", "invalid source identity, locator, or range")
        return cls(source_id, node_locator, start_byte, end_byte, relation)

    def to_value(self) -> PortableValue:
        return PortableValue.object(
            [
                ("source_id", PortableValue.string(self.source_id)),
                ("node_locator", _nullable_string(self.node_locator)),
                ("start_byte", PortableValue.integer(self.start_byte)),
                ("end_byte", PortableValue.integer(self.end_byte)),
                ("relation", PortableValue.string(self.relation.value)),
            ]
        )

    @classmethod
    def from_value(cls, value: PortableValue, path: str) -> SourceOriginMessage:
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
        return cls.new(source_id, node_locator, start_byte, end_byte, relation)


class ProjectedLocationMessage:
    __slots__ = ("kind", "path", "association")

    def __init__(self, kind: str, path: ValuePath | None = None, association: AssociationLocation | None = None):
        self.kind = kind
        self.path = path
        self.association = association

    def less(self, other: ProjectedLocationMessage) -> bool:
        if self.kind != other.kind:
            return self.kind < other.kind
        if self.kind == "AssociationLocation":
            return self.association.less(other.association)
        return self.path.less(other.path)

    def equal(self, other: ProjectedLocationMessage) -> bool:
        if self.kind != other.kind:
            return False
        if self.kind == "AssociationLocation":
            return self.association.equal(other.association)
        return self.path.equal(other.path)

    def to_value(self) -> PortableValue:
        if self.kind == "ValuePath":
            return PortableValue.object(
                [
                    ("kind", PortableValue.string("ValuePath")),
                    ("value", self.path.to_value()),
                ]
            )
        return PortableValue.object(
            [
                ("kind", PortableValue.string("AssociationLocation")),
                ("value", self.association.to_value()),
            ]
        )

    @classmethod
    def from_value(cls, value: PortableValue, path: str) -> ProjectedLocationMessage:
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
    """The sorted unique ``core.provenance-map@1`` record
    (projection.rs:321-326)."""

    __slots__ = ("entries",)

    def __init__(self, entries):
        self.entries = entries

    @classmethod
    def new(cls, entries):
        for entry in entries:
            if not entry.origins:
                raise invalid("$.entries", "provenance locations must be sorted, unique, and have origins")
        for index in range(1, len(entries)):
            if not entries[index - 1].projected.less(entries[index].projected):
                raise invalid("$.entries", "provenance locations must be sorted, unique, and have origins")
        return cls(entries)

    def to_value(self) -> PortableValue:
        entry_values = []
        for entry in self.entries:
            entry_values.append(
                PortableValue.object(
                    [
                        ("projected", entry.projected.to_value()),
                        (
                            "origins",
                            PortableValue.sequence(tuple(origin.to_value() for origin in entry.origins)),
                        ),
                    ]
                )
            )
        return PortableValue.object(
            [
                ("schema", PortableValue.string("core.provenance-map@1")),
                ("entries", PortableValue.sequence(tuple(entry_values))),
            ]
        )

    @classmethod
    def from_value(cls, value: PortableValue) -> ProvenanceMapMessage:
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
        return cls.new(entries)


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

    def to_value(self) -> PortableValue:
        from consema.protocol.diagnostic import SourceLocation

        locations = []
        for location in self.source_locations:
            locations.append(
                PortableValue.object(
                    [
                        ("source_id", PortableValue.string(location.source_id)),
                        ("start_byte", PortableValue.integer(location.start_byte)),
                        ("end_byte", PortableValue.integer(location.end_byte)),
                    ]
                )
            )
        projected = PortableValue.null()
        if self.projected_location is not None:
            projected = self.projected_location.to_value()
        names = sorted(self.arguments)
        return PortableValue.object(
            [
                ("code", PortableValue.string(self.code)),
                ("policy_rule_id", _nullable_string(self.policy_rule_id)),
                ("source_locations", PortableValue.sequence(tuple(locations))),
                ("projected_location", projected),
                ("old_category", _nullable_string(self.old_category)),
                ("new_category", _nullable_string(self.new_category)),
                ("reversible", PortableValue.boolean(self.reversible)),
                ("loss_classification", PortableValue.string(self.loss_classification.value)),
                (
                    "arguments",
                    PortableValue.object(
                        tuple((name, PortableValue.string(self.arguments[name])) for name in names)
                    ),
                ),
            ]
        )

    @classmethod
    def from_value(cls, value: PortableValue, path: str) -> ProjectionEventMessage:
        from consema.protocol.diagnostic import SourceLocation

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
                SourceLocation(
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


class ProjectionReportMessage:
    """The ordered ``core.projection-report@1`` record (projection.rs:439-444)."""

    __slots__ = ("events",)

    def __init__(self, events):
        self.events = events

    @classmethod
    def new(cls, events):
        from consema.protocol.error_registry import ErrorCodeRegistry

        registry = ErrorCodeRegistry(1)
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

    def to_value(self) -> PortableValue:
        return PortableValue.object(
            [
                ("schema", PortableValue.string("core.projection-report@1")),
                ("events", PortableValue.sequence(tuple(event.to_value() for event in self.events))),
            ]
        )

    @classmethod
    def from_value(cls, value: PortableValue) -> ProjectionReportMessage:
        fields = schema_fields(value, "core.projection-report@1", ["schema", "events"], "$")
        events = [
            ProjectionEventMessage.from_value(item, f"$.events[{index}]")
            for index, item in enumerate(sequence_of(fields[1], "$.events"))
        ]
        return cls.new(events)


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
    def new(cls, completion, value, has_value, fidelity, report, provenance, diagnostics):
        success = completion.status is CompletionStatus.SUCCESS
        if success != has_value or (success and fidelity is None) or (not success and fidelity is not None):
            raise invalid("$", "only successful projection may carry value and fidelity")
        if fidelity is not None and fidelity == "Lossy":
            found = any(event.loss_classification is LossClassification.LOSSY for event in report.events)
            if not found:
                raise invalid("$.report", "Lossy fidelity requires an explicit lossy event")
        if not success and provenance.entries:
            raise invalid("$.provenance", "failed projection cannot claim completed provenance")
        return cls(completion, value, has_value, fidelity, report, provenance, diagnostics)

    def to_value(self) -> PortableValue:
        value = PortableValue.null()
        if self.has_value:
            value = PortableValue.object([("portable_value", self.value)])
        fidelity = PortableValue.null()
        if self.fidelity is not None:
            fidelity = PortableValue.string(self.fidelity)
        return PortableValue.object(
            [
                ("schema", PortableValue.string("core.projection-result@1")),
                ("completion", self.completion.to_value()),
                ("value", value),
                ("fidelity", fidelity),
                ("report", self.report.to_value()),
                ("provenance", self.provenance.to_value()),
                (
                    "diagnostics",
                    PortableValue.sequence(tuple(diagnostic.to_value() for diagnostic in self.diagnostics)),
                ),
            ]
        )

    @classmethod
    def from_value(cls, value: PortableValue) -> ProjectionResultMessage:
        from consema.protocol.diagnostic import Diagnostic

        fields = schema_fields(
            value,
            "core.projection-result@1",
            ["schema", "completion", "value", "fidelity", "report", "provenance", "diagnostics"],
            "$",
        )
        completion = Completion.from_value(fields[1])
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
        report = ProjectionReportMessage.from_value(fields[4])
        provenance = ProvenanceMapMessage.from_value(fields[5])
        registry = ErrorCodeRegistry(1)
        diagnostics = [
            Diagnostic.from_value(item, registry)
            for item in sequence_of(fields[6], "$.diagnostics")
        ]
        return cls.new(completion, projected, has_value, fidelity, report, provenance, diagnostics)


# ---------------------------------------------------------------------------
# core.query-result@1
# ---------------------------------------------------------------------------


class NativeMatchLocator:
    __slots__ = ("source_id", "node_locator", "role", "ordinal")

    def __init__(self, source_id, node_locator, role, ordinal):
        self.source_id = source_id
        self.node_locator = node_locator
        self.role = role
        self.ordinal = ordinal

    @classmethod
    def new(cls, source_id, node_locator, role, ordinal):
        if (
            not source_id
            or len(source_id) > 1024
            or not node_locator
            or len(node_locator) > 4096
            or not _is_native_role(role)
        ):
            raise invalid("$.native_match", "invalid source, locator, or native role")
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


def parse_match_role(text: str) -> MatchRole | None:
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

    def to_value(self) -> PortableValue:
        if self.kind == "Value":
            return PortableValue.object(
                [
                    ("kind", PortableValue.string("Value")),
                    ("path", self.path.to_value()),
                    ("value", self.value),
                ]
            )
        if self.kind == "ObjectEntry":
            return PortableValue.object(
                [
                    ("kind", PortableValue.string("ObjectEntry")),
                    ("location", self.location.to_value()),
                    ("key", self.key),
                    ("value_path", self.value_path.to_value()),
                    ("value", self.value),
                ]
            )
        if self.kind == "EntryMappingEntry":
            return PortableValue.object(
                [
                    ("kind", PortableValue.string("EntryMappingEntry")),
                    ("location", self.location.to_value()),
                    ("key_path", self.key_path.to_value()),
                    ("key", self.key),
                    ("value_path", self.value_path.to_value()),
                    ("value", self.value),
                ]
            )
        return PortableValue.object(
            [
                ("kind", PortableValue.string("Native")),
                ("role", PortableValue.string(self.native.role.value)),
                ("source_id", PortableValue.string(self.native.source_id)),
                ("node_locator", PortableValue.string(self.native.node_locator)),
                ("ordinal", PortableValue.integer(self.native.ordinal)),
            ]
        )

    @classmethod
    def from_value(cls, value: PortableValue, path: str) -> ProtocolQueryMatch:
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
            role = parse_match_role(string_of(fields[1], path + ".role"))
            if role is None:
                raise invalid(path + ".role", "unknown match role")
            native = NativeMatchLocator.new(
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
    def new(cls, domain, role, matches, completion, diagnostics):
        if not _is_v1_role(role):
            raise invalid("$.role", "role is not published by core.query-result@1")
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
        return cls(domain, role, matches, completion, diagnostics)

    @classmethod
    def from_portable_execution(cls, domain, role, matches):
        count = len(matches)
        completion = Completion.new(CompletionStatus.SUCCESS, count, count)
        return cls.new(domain, role, matches, completion, [])

    def to_value(self) -> PortableValue:
        return PortableValue.object(
            [
                ("schema", PortableValue.string("core.query-result@1")),
                ("domain_id", PortableValue.string(self.domain.id)),
                ("domain_version", PortableValue.integer(self.domain.version)),
                ("role", PortableValue.string(self.role.value)),
                ("matches", PortableValue.sequence(tuple(match.to_value() for match in self.matches))),
                ("completion", self.completion.to_value()),
                (
                    "diagnostics",
                    PortableValue.sequence(tuple(diagnostic.to_value() for diagnostic in self.diagnostics)),
                ),
            ]
        )

    @classmethod
    def from_value(cls, value: PortableValue) -> QueryResultMessage:
        from consema.protocol.diagnostic import Diagnostic

        fields = schema_fields(
            value,
            "core.query-result@1",
            ["schema", "domain_id", "domain_version", "role", "matches", "completion", "diagnostics"],
            "$",
        )
        domain_id = string_of(fields[1], "$.domain_id")
        domain_version = unsigned32(fields[2], "$.domain_version")
        role = parse_match_role(string_of(fields[3], "$.role"))
        if role is None:
            raise invalid("$.role", "unknown match role")
        matches = [
            ProtocolQueryMatch.from_value(item, f"$.matches[{index}]")
            for index, item in enumerate(sequence_of(fields[4], "$.matches"))
        ]
        completion = Completion.from_value(fields[5])
        registry = ErrorCodeRegistry(1)
        diagnostics = [
            Diagnostic.from_value(item, registry)
            for item in sequence_of(fields[6], "$.diagnostics")
        ]
        return cls.new(QueryDomain(domain_id, domain_version), role, matches, completion, diagnostics)


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
