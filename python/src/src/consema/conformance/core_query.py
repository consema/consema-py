"""Portable-value query execution for the conformance runner.

The ``core.portable-value-query@1`` domain executor and the ordered-result
cursor semantics (RFC 0003; crates/consema-core/src/query.rs) are
runner-side capability implementations: the Python core package validates
query definitions (consema.protocol.query) but the value-domain execution
surface is exercised here so the shared vectors drive it. The operator
semantics follow the frozen validation table of
consema.protocol.query._OPERATOR_TABLE and the match-role model of RFC 0016
§5.4. Failure names follow the vector spellings (queryFailureName,
go/conformance/g43_faces.go:368-394).
"""

from __future__ import annotations

from consema.core.value import Kind, PortableValue
from consema.protocol.query import (
    ExpressionKind,
    MatchRole,
    OperatorCall,
    QueryDefinition,
    QueryExpression,
    QueryFailure,
    QueryFailureKind,
    QuerySelection,
)


def query_failure_name(failure: QueryFailure) -> str:
    """The stable vector spelling of one query failure kind."""
    names = {
        QueryFailureKind.DOMAIN_MISMATCH: "DomainMismatch",
        QueryFailureKind.UNKNOWN_OPERATOR: "UnknownOperator",
        QueryFailureKind.WRONG_ARGUMENT_TYPE: "WrongArgumentType",
        QueryFailureKind.INVALID_ARGUMENT: "InvalidArgument",
        QueryFailureKind.INVALID_OPERATOR_COMPOSITION: "InvalidOperatorComposition",
        QueryFailureKind.MISSING_CAPABILITY: "MissingCapability",
        QueryFailureKind.REQUIRED_TYPE_MISMATCH: "RequiredTypeMismatch",
        QueryFailureKind.CARDINALITY_VIOLATION: "CardinalityViolation",
        QueryFailureKind.RESOURCE_LIMIT: "ResourceLimitExceeded",
        QueryFailureKind.CANCELLED: "Cancelled",
        QueryFailureKind.TARGET_UNAVAILABLE: "TargetUnavailable",
    }
    return names.get(failure.kind, failure.kind.value)


class CoreMatch:
    """One ordered result of the portable-value domain."""

    __slots__ = ("kind", "ordinal", "value", "key", "entry")

    def __init__(
        self,
        kind: str,
        ordinal: int,
        value: PortableValue,
        key: str | None = None,
        entry: tuple[PortableValue, PortableValue] | None = None,
    ):
        self.kind = kind
        self.ordinal = ordinal
        self.value = value
        self.key = key
        self.entry = entry

    def __repr__(self) -> str:
        return f"CoreMatch({self.kind}, {self.ordinal}, {self.value!r})"


class CoreCursor:
    """The ordered-result cursor over one portable-value query.

    Terminal states (RFC 0003): ``Completed`` after the stream is exhausted,
    ``Cancelled`` when cancellation was requested before the next advance,
    ``Failed`` when a resource limit is hit while advancing. ``max_results``
    bounds the yielded results; the advance that would exceed the bound
    fails with ResourceLimitExceeded.
    """

    __slots__ = ("_matches", "_index", "max_results", "cancelled", "mode")

    def __init__(
        self,
        matches: list[CoreMatch],
        max_results: int | None = None,
        cancelled: bool = False,
        mode: str = "Completed",
    ):
        self._matches = matches
        self._index = 0
        self.max_results = max_results
        self.cancelled = cancelled
        self.mode = mode

    def next(self) -> CoreMatch | None:
        """Yields the next result, or None at a terminal state."""
        if self.cancelled:
            return None
        if self.max_results is not None and self._index >= self.max_results:
            return None
        if self._index >= len(self._matches):
            return None
        match = self._matches[self._index]
        self._index += 1
        return match

    def yielded(self) -> int:
        return self._index

    def terminal_state(self) -> str:
        if self.cancelled:
            return "Cancelled"
        if self.max_results is not None and self._index >= self.max_results:
            if self._index >= len(self._matches):
                return self.mode
            return "Failed"
        if self._index >= len(self._matches):
            return self.mode
        return "Completed"


def execute_portable(
    value: PortableValue,
    expression: QueryExpression,
    max_results: int | None = None,
    cancelled: bool = False,
) -> list[CoreMatch]:
    """Executes one validated expression over a core value and returns the
    ordered result stream."""
    matches = _execute_expression(value, expression, 0)
    if max_results is not None and len(matches) > max_results:
        matches = matches[:max_results]
    return matches


def build_pipeline(
    domain_id: str,
    domain_version: int,
    pipeline: list[str],
    arguments: dict[str, str] | None = None,
) -> QueryExpression:
    """Builds an Apply-chain expression from ``id@version`` operator
    spellings with one optional string argument applied to the last
    operator."""
    expression = QueryExpression(ExpressionKind.INPUT)
    operators: list[OperatorCall] = []
    for spelling in pipeline:
        operator_id, version_text = spelling.rsplit("@", 1)
        operators.append(OperatorCall(operator_id, int(version_text)))
    for index, operator in enumerate(operators):
        if arguments is not None and index == len(operators) - 1:
            for name, argument in arguments.items():
                operator.with_argument(name, PortableValue.string(argument))
        expression = expression.then(operator)
    return expression


def _execute_expression(
    value: PortableValue, expression: QueryExpression, depth: int
) -> list[CoreMatch]:
    if depth > 256:
        raise QueryFailure(QueryFailureKind.RESOURCE_LIMIT)
    if expression.kind is ExpressionKind.INPUT:
        return [CoreMatch("Value", 0, value)]
    if expression.kind is ExpressionKind.APPLY:
        inputs = _execute_expression(value, expression.input, depth + 1)
        return _apply_operator(expression.operator, inputs, depth + 1)
    if expression.kind is ExpressionKind.CONCAT:
        results: list[CoreMatch] = []
        for branch in expression.branches:
            results.extend(_execute_expression(value, branch, depth + 1))
        return results
    if expression.kind is ExpressionKind.STRUCTURE_ORDER_MERGE:
        branches = [
            _execute_expression(value, branch, depth + 1) for branch in expression.branches
        ]
        merged: list[CoreMatch] = []
        for index in range(len(branches[0])):
            for branch in branches:
                if index < len(branch):
                    merged.append(branch[index])
        return merged
    raise QueryFailure(QueryFailureKind.INVALID_ARGUMENT)


def _apply_operator(operator: OperatorCall, inputs: list[CoreMatch], depth: int) -> list[CoreMatch]:
    if operator.id == "core.take":
        count = _integer_argument(operator, "count")
        return inputs[:count]
    if operator.id == "core.distinct-by-identity":
        # Domain-agnostic identity deduplication over the stream.
        seen: set[int] = set()
        distinct: list[CoreMatch] = []
        for match in inputs:
            marker = id(match.value)
            if marker in seen:
                continue
            seen.add(marker)
            distinct.append(match)
        return distinct
    results: list[CoreMatch] = []
    for match in inputs:
        results.extend(_apply_one(operator, match, depth))
    return results


def _apply_one(operator: OperatorCall, match: CoreMatch, depth: int) -> list[CoreMatch]:
    operator_id = operator.id
    if operator_id == "core.try-object-entries":
        _require_kind(match.value, Kind.OBJECT, operator)
        return [
            CoreMatch("ObjectEntry", ordinal, key_value[0], value=key_value[1], key=key_value[0])
            for ordinal, key_value in enumerate(match.value.as_object())
        ]
    if operator_id == "core.object-entry-value":
        _require_role(match, "ObjectEntry", operator)
        return [CoreMatch("Value", 0, match.value)]
    if operator_id == "core.object-entry-name-equals":
        _require_role(match, "ObjectEntry", operator)
        name = _string_argument(operator, "name")
        if match.key == name:
            return [match]
        return []
    if operator_id == "core.try-entry-mapping-entries":
        _require_kind(match.value, Kind.ENTRY_MAPPING, operator)
        return [
            CoreMatch(
                "EntryMappingEntry",
                ordinal,
                entry_value[1],
                entry=entry_value,
            )
            for ordinal, entry_value in enumerate(match.value.as_entry_mapping())
        ]
    if operator_id == "core.entry-key":
        _require_role(match, "EntryMappingEntry", operator)
        return [CoreMatch("Value", 0, match.entry[0])]
    if operator_id == "core.entry-value":
        _require_role(match, "EntryMappingEntry", operator)
        return [CoreMatch("Value", 0, match.entry[1])]
    if operator_id == "core.try-sequence-elements":
        _require_kind(match.value, Kind.SEQUENCE, operator)
        return [
            CoreMatch("Value", ordinal, element)
            for ordinal, element in enumerate(match.value.as_sequence())
        ]
    if operator_id == "core.where-type":
        kind_name = _string_argument(operator, "kind")
        if match.value.kind.value == kind_name:
            return [match]
        return []
    if operator_id == "core.require-type":
        kind_name = _string_argument(operator, "kind")
        if match.value.kind.value != kind_name:
            raise QueryFailure(
                QueryFailureKind.REQUIRED_TYPE_MISMATCH,
                operator=operator_id,
                argument=kind_name,
            )
        return [match]
    raise QueryFailure(QueryFailureKind.UNKNOWN_OPERATOR, operator=operator_id, version=operator.version)


def _require_kind(value: PortableValue, kind: Kind, operator: OperatorCall) -> None:
    if value.kind is not kind:
        raise QueryFailure(
            QueryFailureKind.REQUIRED_TYPE_MISMATCH,
            operator=operator.id,
            argument=kind.value,
        )


def _require_role(match: CoreMatch, role: str, operator: OperatorCall) -> None:
    if match.kind != role:
        raise QueryFailure(
            QueryFailureKind.INVALID_OPERATOR_COMPOSITION,
            operator=operator.id,
        )


def _string_argument(operator: OperatorCall, name: str) -> str:
    value = operator.arguments.get(name)
    if value is None or value.kind is not Kind.STRING:
        raise QueryFailure(
            QueryFailureKind.WRONG_ARGUMENT_TYPE, operator=operator.id, argument=name
        )
    return value.as_string()


def _integer_argument(operator: OperatorCall, name: str) -> int:
    value = operator.arguments.get(name)
    if value is None or value.kind is not Kind.INTEGER:
        raise QueryFailure(
            QueryFailureKind.WRONG_ARGUMENT_TYPE, operator=operator.id, argument=name
        )
    return value.as_integer()
