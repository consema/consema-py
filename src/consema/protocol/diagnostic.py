"""The transferable `core.diagnostic@1` record.

Authority: crates/consema-protocol/src/diagnostic.rs (the record shape and
the code/category registry binding, diagnostic.rs:336-351); RFC 0016 §6:
"unknown code or category contradiction is a protocol error". Go
(go/protocol/diagnostic.go) is a cross-reference only.

The record fields in canonical order: schema, code, category, severity,
primary, related, arguments, notes, fixes, occurrence. Fix replacements are
Bytes leaves carried with full byte fidelity (never Null).
"""

from __future__ import annotations

import enum

from consema.core.value import Kind, PortableValue
from consema.protocol.errors import ProtocolError, ProtocolErrorKind, protocol_error
from consema.protocol.error_registry import (
    DiagnosticCategory,
    ErrorCodeRegistry,
    parse_category,
)
from consema.protocol.schema import (
    boolean_of,
    exact_fields,
    integer_value,
    schema_fields,
    sequence_of,
    string_map_from_object,
    string_map_object,
    string_of,
    unsigned64,
)


class Severity(enum.Enum):
    """The three frozen presentation severities."""

    INFO = "Info"
    WARNING = "Warning"
    ERROR = "Error"


def parse_severity(name: str) -> Severity:
    try:
        return Severity(name)
    except ValueError:
        raise protocol_error(
            ProtocolErrorKind.INVALID_VALUE, "$.severity", "unknown diagnostic severity"
        ) from None


class FixApplicability(enum.Enum):
    """Whether a fix can be applied without additional judgment."""

    MACHINE_APPLICABLE = "MachineApplicable"
    MAYBE_APPLICABLE = "MaybeApplicable"
    MANUAL = "Manual"


def parse_fix_applicability(name: str) -> FixApplicability:
    try:
        return FixApplicability(name)
    except ValueError:
        raise protocol_error(
            ProtocolErrorKind.INVALID_VALUE,
            "$.fixes[].applicability",
            "unknown fix applicability",
        ) from None


class SourceLocation:
    """A transferable source location bound to a caller-assigned source ID."""

    __slots__ = ("source_id", "start_byte", "end_byte")

    def __init__(self, source_id: str, start_byte: int, end_byte: int):
        if (
            not source_id
            or len(source_id.encode("utf-8")) > 1024
            or start_byte > end_byte
        ):
            raise protocol_error(
                ProtocolErrorKind.INVALID_VALUE,
                "$.location",
                "source ID or half-open byte range is invalid",
            )
        self.source_id = source_id
        self.start_byte = start_byte
        self.end_byte = end_byte

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SourceLocation):
            return NotImplemented
        return (
            self.source_id == other.source_id
            and self.start_byte == other.start_byte
            and self.end_byte == other.end_byte
        )

    def __hash__(self) -> int:
        return hash((self.source_id, self.start_byte, self.end_byte))

    def __repr__(self) -> str:
        return (
            f"SourceLocation({self.source_id!r}, {self.start_byte}, {self.end_byte})"
        )


class RelatedSourceLocation:
    """A related source location with its stable relationship role."""

    __slots__ = ("role", "location")

    def __init__(self, role: str, location: SourceLocation):
        self.role = role
        self.location = location


class FixProposal:
    """An explicit source replacement proposal; never an implicit write."""

    __slots__ = ("id", "applicability", "location", "replacement")

    def __init__(
        self,
        id: str,
        applicability: FixApplicability,
        location: SourceLocation | None,
        replacement: bytes,
    ):
        self.id = id
        self.applicability = applicability
        self.location = location
        self.replacement = bytes(replacement)


class Diagnostic:
    """The full `core.diagnostic@1` record independent from control flow.

    Construction validates the code against the frozen error registry and
    the category against the registry record (diagnostic.rs:336-351).
    """

    __slots__ = (
        "code",
        "category",
        "severity",
        "primary",
        "related",
        "arguments",
        "notes",
        "fixes",
        "occurrence",
    )

    def __init__(
        self,
        code: str,
        category: DiagnosticCategory,
        severity: Severity,
        primary: SourceLocation | None,
        related: list[RelatedSourceLocation],
        arguments: dict[str, str],
        notes: list[str],
        fixes: list[FixProposal],
        occurrence: int,
        registry: ErrorCodeRegistry,
    ):
        validate_diagnostic_code(code, category, registry)
        self.code = code
        self.category = category
        self.severity = severity
        self.primary = primary
        self.related = related
        self.arguments = dict(arguments)
        self.notes = notes
        self.fixes = fixes
        self.occurrence = occurrence

    # -- value-level codec -------------------------------------------------

    def to_value(self) -> PortableValue:
        related = [
            PortableValue.object(
                [
                    ("role", PortableValue.string(item.role)),
                    ("location", _location_value(item.location)),
                ]
            )
            for item in self.related
        ]
        fixes = []
        for fix in self.fixes:
            location = PortableValue.null()
            if fix.location is not None:
                location = _location_value(fix.location)
            fixes.append(
                PortableValue.object(
                    [
                        ("id", PortableValue.string(fix.id)),
                        ("applicability", PortableValue.string(fix.applicability.value)),
                        ("location", location),
                        ("replacement", PortableValue.bytes_value(fix.replacement)),
                    ]
                )
            )
        primary = PortableValue.null()
        if self.primary is not None:
            primary = _location_value(self.primary)
        return PortableValue.object(
            [
                ("schema", PortableValue.string("core.diagnostic@1")),
                ("code", PortableValue.string(self.code)),
                ("category", PortableValue.string(self.category.value)),
                ("severity", PortableValue.string(self.severity.value)),
                ("primary", primary),
                ("related", PortableValue.sequence(related)),
                ("arguments", string_map_object(self.arguments)),
                ("notes", PortableValue.sequence([PortableValue.string(note) for note in self.notes])),
                ("fixes", PortableValue.sequence(fixes)),
                ("occurrence", integer_value(self.occurrence)),
            ]
        )

    @staticmethod
    def from_value(value: PortableValue, registry: ErrorCodeRegistry) -> "Diagnostic":
        fields = schema_fields(
            value,
            "core.diagnostic@1",
            ["schema", "code", "category", "severity", "primary", "related",
             "arguments", "notes", "fixes", "occurrence"],
            "$",
        )
        code = string_of(fields[1], "$.code")
        category = _parse_category_value(fields[2], "$.category")
        severity = parse_severity(string_of(fields[3], "$.severity"))
        primary = None
        if fields[4].kind is not Kind.NULL:
            primary = _parse_location(fields[4], "$.primary")
        related = []
        for index, item in enumerate(sequence_of(fields[5], "$.related")):
            path = f"$.related[{index}]"
            entry = exact_fields(item, ["role", "location"], path)
            role = string_of(entry[0], f"{path}.role")
            location = _parse_location(entry[1], f"{path}.location")
            related.append(RelatedSourceLocation(role, location))
        arguments = string_map_from_object(fields[6], "$.arguments")
        notes = []
        for index, note in enumerate(sequence_of(fields[7], "$.notes")):
            notes.append(string_of(note, f"$.notes[{index}]"))
        fixes = []
        for index, item in enumerate(sequence_of(fields[8], "$.fixes")):
            fixes.append(_decode_fix(item, f"$.fixes[{index}]"))
        occurrence = unsigned64(fields[9], "$.occurrence")
        return Diagnostic(
            code, category, severity, primary, related, arguments,
            notes, fixes, occurrence, registry,
        )


def validate_diagnostic_code(
    code: str, category: DiagnosticCategory, registry: ErrorCodeRegistry
) -> None:
    """Requires the code to be registered and its category to match."""
    descriptor = registry.descriptor(code)
    if descriptor is None:
        raise protocol_error(
            ProtocolErrorKind.INVALID_VALUE, "$.code", f"unregistered public code: {code}"
        )
    if descriptor.category is not category:
        raise protocol_error(
            ProtocolErrorKind.INVALID_VALUE,
            "$.category",
            "diagnostic category contradicts the error-code registry",
        )


def _parse_category_value(value: PortableValue, path: str) -> DiagnosticCategory:
    return parse_category(string_of(value, path), path)


def _location_value(location: SourceLocation) -> PortableValue:
    return PortableValue.object(
        [
            ("source_id", PortableValue.string(location.source_id)),
            ("start_byte", integer_value(location.start_byte)),
            ("end_byte", integer_value(location.end_byte)),
        ]
    )


def _parse_location(value: PortableValue, path: str) -> SourceLocation:
    fields = exact_fields(value, ["source_id", "start_byte", "end_byte"], path)
    source_id = string_of(fields[0], f"{path}.source_id")
    start_byte = unsigned64(fields[1], f"{path}.start_byte")
    end_byte = unsigned64(fields[2], f"{path}.end_byte")
    return SourceLocation(source_id, start_byte, end_byte)


def _decode_fix(value: PortableValue, path: str) -> FixProposal:
    fields = exact_fields(value, ["id", "applicability", "location", "replacement"], path)
    id = string_of(fields[0], f"{path}.id")
    applicability = parse_fix_applicability(
        string_of(fields[1], f"{path}.applicability")
    )
    location = None
    if fields[2].kind is not Kind.NULL:
        location = _parse_location(fields[2], f"{path}.location")
    if fields[3].kind is not Kind.BYTES:
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, f"{path}.replacement", "expected Bytes")
    return FixProposal(id, applicability, location, fields[3].as_bytes())


# --------------------------------------------------------------------------
# JSON-tree-level codec (shared with the CLI record tree codec)
# --------------------------------------------------------------------------

def diagnostic_node(diagnostic: "Diagnostic") -> tuple:
    """Builds one core.diagnostic@1 tree record (fixes as Bytes leaves)."""
    from consema.protocol.canonical import (
        tagged_array,
        tagged_bytes,
        tagged_integer,
        tagged_null,
        tagged_object,
        tagged_string,
    )

    related = []
    for item in diagnostic.related:
        related.append(
            tagged_object(
                [
                    ("role", tagged_string(item.role)),
                    ("location", _location_node(item.location)),
                ]
            )
        )
    arguments = [
        (name, tagged_string(value)) for name, value in sorted(diagnostic.arguments.items())
    ]
    notes = [tagged_string(note) for note in diagnostic.notes]
    fixes = []
    for fix in diagnostic.fixes:
        location = tagged_null()
        if fix.location is not None:
            location = _location_node(fix.location)
        fixes.append(
            tagged_object(
                [
                    ("id", tagged_string(fix.id)),
                    ("applicability", tagged_string(fix.applicability.value)),
                    ("location", location),
                    ("replacement", tagged_bytes(fix.replacement)),
                ]
            )
        )
    primary = tagged_null()
    if diagnostic.primary is not None:
        primary = _location_node(diagnostic.primary)
    return tagged_object(
        [
            ("schema", tagged_string("core.diagnostic@1")),
            ("code", tagged_string(diagnostic.code)),
            ("category", tagged_string(diagnostic.category.value)),
            ("severity", tagged_string(diagnostic.severity.value)),
            ("primary", primary),
            ("related", tagged_array(related)),
            ("arguments", tagged_object(arguments)),
            ("notes", tagged_array(notes)),
            ("fixes", tagged_array(fixes)),
            ("occurrence", tagged_integer(diagnostic.occurrence)),
        ]
    )


def parse_diagnostic_node(node: tuple, path: str, registry: ErrorCodeRegistry) -> "Diagnostic":
    """Decodes one core.diagnostic@1 tree record and binds it to the registry."""
    from consema.protocol.canonical import (
        json_record_fields,
        json_tagged_array,
        json_tagged_bytes,
        json_tagged_string,
        json_tagged_uint64,
        json_is_tagged_null,
    )

    fields = json_record_fields(
        node,
        ["schema", "code", "category", "severity", "primary", "related",
         "arguments", "notes", "fixes", "occurrence"],
        path,
    )
    schema = json_tagged_string(fields[0], f"{path}.schema")
    if schema != "core.diagnostic@1":
        raise protocol_error(
            ProtocolErrorKind.SCHEMA_MISMATCH, f"{path}.schema", "expected core.diagnostic@1"
        )
    code = json_tagged_string(fields[1], f"{path}.code")
    category = parse_category(json_tagged_string(fields[2], f"{path}.category"), f"{path}.category")
    severity = parse_severity(json_tagged_string(fields[3], f"{path}.severity"))
    primary = None
    if not json_is_tagged_null(fields[4]):
        primary = _parse_location_node(fields[4], f"{path}.primary")
    related = []
    for index, item in enumerate(json_tagged_array(fields[5], f"{path}.related")):
        item_path = f"{path}.related[{index}]"
        item_fields = json_record_fields(item, ["role", "location"], item_path)
        role = json_tagged_string(item_fields[0], f"{item_path}.role")
        location = _parse_location_node(item_fields[1], f"{item_path}.location")
        related.append(RelatedSourceLocation(role, location))
    arguments = _json_string_map(fields[6], f"{path}.arguments")
    notes = []
    for index, note in enumerate(json_tagged_array(fields[7], f"{path}.notes")):
        notes.append(json_tagged_string(note, f"{path}.notes[{index}]"))
    fixes = []
    for index, item in enumerate(json_tagged_array(fields[8], f"{path}.fixes")):
        item_path = f"{path}.fixes[{index}]"
        item_fields = json_record_fields(
            item, ["id", "applicability", "location", "replacement"], item_path
        )
        id = json_tagged_string(item_fields[0], f"{item_path}.id")
        applicability = parse_fix_applicability(
            json_tagged_string(item_fields[1], f"{item_path}.applicability")
        )
        location = None
        if not json_is_tagged_null(item_fields[2]):
            location = _parse_location_node(item_fields[2], f"{item_path}.location")
        replacement = json_tagged_bytes(item_fields[3], f"{item_path}.replacement")
        fixes.append(FixProposal(id, applicability, location, replacement))
    occurrence = json_tagged_uint64(fields[9], f"{path}.occurrence")
    return Diagnostic(
        code, category, severity, primary, related, arguments,
        notes, fixes, occurrence, registry,
    )


def _location_node(location: SourceLocation) -> tuple:
    from consema.protocol.canonical import tagged_integer, tagged_object, tagged_string

    return tagged_object(
        [
            ("source_id", tagged_string(location.source_id)),
            ("start_byte", tagged_integer(location.start_byte)),
            ("end_byte", tagged_integer(location.end_byte)),
        ]
    )


def _parse_location_node(node: tuple, path: str) -> SourceLocation:
    from consema.protocol.canonical import json_record_fields, json_tagged_string, json_tagged_uint64

    fields = json_record_fields(node, ["source_id", "start_byte", "end_byte"], path)
    source_id = json_tagged_string(fields[0], f"{path}.source_id")
    start_byte = json_tagged_uint64(fields[1], f"{path}.start_byte")
    end_byte = json_tagged_uint64(fields[2], f"{path}.end_byte")
    return SourceLocation(source_id, start_byte, end_byte)


def _json_string_map(node: tuple, path: str) -> dict[str, str]:
    from consema.protocol.canonical import json_string_map

    return json_string_map(node, path)
