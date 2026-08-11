"""Suite ``consema.plist.conformance@1`` (plist-v1.json, 45 cases): plist XML
and binary formation with recovery, the three query domains, value-tree and
require-object projection, both canonical materializations, cross-
representation conversion, and the six structural edits. Dispatch is by the
``capability`` field, mirroring go/conformance/plist_v1.go.
"""

from __future__ import annotations

import json
import struct

from consema.conformance import compare
from consema.conformance import loader
from consema.conformance import runner
from consema.core.value import Kind, PortableValue
from consema.document.ids import MaterializationStyleId, ProfileId
from consema.document.materialization import (
    CompleteMaterialization,
    FailedMaterializationAttempt,
    MaterializationFailureKind,
    MaterializationRequest,
)
from consema.document.source_patch import SourcePatchLimits
from consema.document.structural import FormationStatus
from consema.plist import conversion as plist_conversion
from consema.plist import edit as plist_edit
from consema.plist import materialization as plist_materialization
from consema.plist import projection as plist_projection
from consema.plist import query as plist_query
from consema.plist.document import PlistDocument, parse
from consema.plist.errors import PlistConversionFailure, PlistEditFailure, PlistFormationFailure
from consema.plist.kinds import (
    PlistEncodingSelection,
    PlistParseLimits,
    PlistProfile,
)
from consema.plist.native import PlistValueKind
from consema.protocol import query as protocol_query
from consema.protocol.registry_descriptor import CapabilityId, CapabilitySet


def run(conformance_runner: runner.Runner, data: runner.SuiteData) -> runner.SuiteReport:
    report = runner.SuiteReport(suite=data.suite, expected_cases=len(data.cases))
    for vector in data.cases:
        capability = vector.capability
        if capability == "plist.xml-formation@1":
            message = _xml_formation(vector)
        elif capability == "plist.binary-formation@1":
            message = _binary_formation(vector)
        elif capability == "plist.query@1":
            message = _query(vector)
        elif capability == "plist.projection@1":
            message = _projection(vector)
        elif capability == "plist.materialization@1":
            message = _materialization(conformance_runner, vector)
        elif capability == "plist.conversion@1":
            message = _conversion(vector)
        elif capability == "plist.edit@1":
            message = _edit(vector)
        else:
            message = "runner does not recognize published plist capability " + capability
        if message:
            report.failed.append(runner.CaseFailure(id=vector.id, message=message))
        else:
            report.passed.append(vector.id)
    return report


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _profile_of(value) -> PlistProfile | None:
    profile = compare.string_field(value, "profile")
    if profile == "plist.xml@1":
        return PlistProfile.XML_V1
    if profile == "plist.binary@1":
        return PlistProfile.BINARY_V1
    return None


def _source_bytes(value, profile: PlistProfile):
    """Raw source bytes of one vector value or sample."""
    if profile is PlistProfile.BINARY_V1:
        text = compare.string_field(value, "hex")
        if text is None:
            return None, "missing input.hex"
        try:
            return bytes.fromhex(text), ""
        except ValueError:
            return None, "invalid hex"
    source = compare.string_field(value, "source")
    if source is None:
        return None, "missing input.source"
    if compare.string_field(value, "encoding") == "utf16le-bom":
        return b"\xff\xfe" + source.encode("utf-16-le"), ""
    return source.encode("utf-8"), ""


def _form_bytes(raw: bytes, profile: PlistProfile):
    try:
        document = parse(
            raw, profile, PlistEncodingSelection.profile_default(), PlistParseLimits()
        )
    except PlistFormationFailure as failure:
        return None, "plist formation failed: " + failure.code
    return document, ""


def _form_value(value):
    profile = _profile_of(value)
    if profile is None:
        return None, "missing profile"
    raw, message = _source_bytes(value, profile)
    if message:
        return None, message
    return _form_bytes(raw, profile)


def _sample_profile(vector: runner.Case, sample) -> PlistProfile:
    profile = compare.string_field(sample, "profile")
    if profile == "plist.xml@1":
        return PlistProfile.XML_V1
    if profile == "plist.binary@1":
        return PlistProfile.BINARY_V1
    return _profile_of(vector.input) or PlistProfile.XML_V1


def _form_sample(vector: runner.Case, sample):
    profile = _sample_profile(vector, sample)
    raw, message = _source_bytes(sample, profile)
    if message:
        return None, message
    return _form_bytes(raw, profile)


def _status_name(document) -> str:
    return document.formation_status().value


def _assert_expected_status(document, expected) -> str | None:
    status = compare.string_field(expected, "status")
    if status is not None and _status_name(document) != status:
        return f"status {_status_name(document)} != {status}"
    diagnostic = compare.string_field(expected, "diagnostic")
    if diagnostic is not None:
        codes = [item.code for item in document.diagnostic_records()]
        if diagnostic not in codes:
            return f"diagnostic {diagnostic} not found in {codes!r}"
    return None


def _bits_equal(left: float, right: float) -> bool:
    return struct.pack(">d", left).hex() == struct.pack(">d", right).hex()


def _expected_f64(value) -> float | None:
    kind = value.kind
    if kind is Kind.BINARY_FLOAT64:
        return struct.unpack(">d", struct.pack(">Q", value.as_binary_float64()))[0]
    if kind is Kind.BINARY_FLOAT32:
        return float(struct.unpack(">f", struct.pack(">I", value.as_binary_float32()))[0])
    if kind is Kind.DECIMAL:
        decimal = value.as_decimal()
        return float(decimal.coefficient) * (10.0 ** decimal.exponent)
    if kind is Kind.INTEGER:
        return float(value.as_integer())
    return None


def _root_value(document):
    native = document.document()
    if native is None:
        return None, "no native document"
    return native.root_value(), ""


def _dict_entries(value):
    dict_value = value.as_dict()
    if dict_value is None:
        return None, "expected dict"
    return dict_value, ""


def _dict_keys_of(document, value):
    dict_value, message = _dict_entries(value)
    if message:
        return None, message
    native = document.document()
    keys = []
    for entry in dict_value.entries:
        try:
            keys.append(entry.key.to_unicode())
        except Exception:
            return None, "key not unicode"
    return keys, ""


def _entry_by_key(document, value, name):
    native = document.document()
    if native is None:
        return None, "no native document"
    dict_value, message = _dict_entries(value)
    if message:
        return None, message
    for entry in dict_value.entries:
        if entry.key.to_unicode() == name:
            resolved = native.get(entry.value)
            if resolved is None:
                return None, "entry value missing"
            return resolved, ""
    return None, f"dict entry {name} not found"


def _duplicate_groups_of(entries) -> int:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.key.to_unicode()] = counts.get(entry.key.to_unicode(), 0) + 1
    return sum(1 for count in counts.values() if count > 1)


def _value_kind_name(value) -> str:
    return value.kind.value


def _value_text(value) -> str | None:
    string = value.as_string()
    if string is None:
        return None
    try:
        return string.to_unicode()
    except Exception:
        return None


def _value_integer(value) -> int | None:
    integer = value.as_integer()
    return integer.value if integer is not None else None


def _value_real(value) -> float | None:
    real = value.as_real()
    return real.as_f64() if real is not None else None


def _value_boolean(value) -> bool | None:
    boolean = value.as_boolean()
    return boolean.value if boolean is not None else None


def _value_data_hex(value) -> str | None:
    data = value.as_data()
    return data.bytes.hex() if data is not None else None


def _value_seconds(value) -> float | None:
    date = value.as_date()
    return date.seconds if date is not None else None


def _compare_scalar_value(value, expected) -> str | None:
    kind = expected.kind
    if kind is Kind.STRING:
        actual = _value_text(value)
        if actual != expected.as_string():
            return "value mismatch"
    elif kind is Kind.INTEGER:
        actual = _value_integer(value)
        if actual != expected.as_integer():
            return "integer value mismatch"
    elif kind is Kind.BOOLEAN:
        actual = _value_boolean(value)
        if actual != expected.as_boolean():
            return "boolean value mismatch"
    else:
        return "unsupported expected scalar"
    return None


def _assert_strings(actual: list, expected, what: str) -> str | None:
    if len(actual) != len(expected):
        return what + " count differs"
    for index, item in enumerate(expected):
        if item.kind is not Kind.STRING:
            return what + " must be a string"
        if actual[index] != item.as_string():
            return what + " differs from expected"
    return None


def _assert_u64_field(expected, name: str, actual: int) -> str | None:
    expected_value = compare.integer_field(expected, name)
    if expected_value is None:
        return None
    if actual != expected_value:
        return name + " differs from expected"
    return None


def _scalar_objects(document) -> int:
    facts = document.binary_facts()
    if facts is None:
        return 0
    count = 0
    for obj in facts.objects:
        marker = obj.marker
        if 0xA0 <= marker <= 0xAF:
            continue
        if 0xD0 <= marker <= 0xDF:
            continue
        count += 1
    return count


def _native_value_of(document, reference):
    native = document.document()
    if native is None:
        return None, "no native document"
    value = native.get(reference)
    if value is None:
        return None, "arena reference missing"
    return value, ""


# ---------------------------------------------------------------------------
# XML formation
# ---------------------------------------------------------------------------


def _xml_formation(vector: runner.Case) -> str | None:
    samples = compare.sequence_field(vector.input, "samples")
    if samples is not None:
        return _xml_formation_samples(vector, samples)
    document, message = _form_value(vector.input)
    if message:
        return message
    message = _assert_expected_status(document, vector.expected)
    if message:
        return message
    if _status_name(document) == "Complete":
        render = compare.string_field(vector.expected, "render")
        if render is not None and document.render() != render.encode("utf-8"):
            return "render mismatch"
        render_hex = compare.string_field(vector.expected, "render_hex")
        if render_hex is not None and document.render().hex() != render_hex:
            return "render_hex mismatch"
        message = _xml_native_facts(document, vector.expected)
        if message:
            return message
    return None


def _xml_native_facts(document, expected) -> str | None:
    root, message = _root_value(document)
    if message:
        return message
    root_value = compare.string_field(expected, "root_value")
    if root_value is not None:
        actual = _value_text(root)
        if actual != root_value:
            return "root value mismatch"
    keys = compare.sequence_field(expected, "keys")
    if keys is not None:
        actual, message = _dict_keys_of(document, root)
        if message:
            return message
        message = _assert_strings(actual, keys, "key")
        if message:
            return message
    associations = compare.integer_field(expected, "associations")
    if associations is not None:
        dict_value, message = _dict_entries(root)
        if message:
            return message
        if len(dict_value.entries) != associations:
            return "associations mismatch"
    groups = compare.integer_field(expected, "duplicate_groups")
    if groups is not None:
        dict_value, message = _dict_entries(root)
        if message:
            return message
        if _duplicate_groups_of(dict_value.entries) != groups:
            return "duplicate_groups mismatch"
    values = compare.sequence_field(expected, "values")
    if values is not None:
        dict_value, message = _dict_entries(root)
        if message:
            return message
        if len(dict_value.entries) != len(values):
            return "value count mismatch"
        for index, entry in enumerate(dict_value.entries):
            value, message = _native_value_of(document, entry.value)
            if message:
                return message
            message = _compare_scalar_value(value, values[index])
            if message:
                return message
    integer_value = compare.integer_field(expected, "integer_value")
    if integer_value is not None:
        value, message = _entry_by_key(document, root, "count")
        if message:
            return message
        if _value_integer(value) != integer_value:
            return "integer_value mismatch"
    negative = compare.integer_field(expected, "negative_integer")
    if negative is not None:
        value, message = _entry_by_key(document, root, "negative")
        if message:
            return message
        if _value_integer(value) != negative:
            return "negative_integer mismatch"
    real_value = compare.object_field(expected, "real_value")
    if real_value is not None:
        value, message = _entry_by_key(document, root, "ratio")
        if message:
            return message
        expected_f64 = _expected_f64(real_value)
        actual = _value_real(value)
        if actual is None or expected_f64 is None or not _bits_equal(actual, expected_f64):
            return "real_value mismatch"
    data_hex = compare.string_field(expected, "data_hex")
    if data_hex is not None:
        value, message = _entry_by_key(document, root, "payload")
        if message:
            return message
        if _value_data_hex(value) != data_hex:
            return "data_hex mismatch"
    date_seconds = compare.object_field(expected, "date_seconds")
    if date_seconds is not None:
        value, message = _entry_by_key(document, root, "born")
        if message:
            return message
        expected_f64 = _expected_f64(date_seconds)
        actual = _value_seconds(value)
        if actual is None or expected_f64 is None or not _bits_equal(actual, expected_f64):
            return "date_seconds mismatch"
    booleans = compare.sequence_field(expected, "bool_values")
    if booleans is not None:
        dict_value, message = _dict_entries(root)
        if message:
            return message
        expected_values = [
            item.as_boolean() for item in booleans if item.kind is Kind.BOOLEAN
        ]
        actual_values = []
        for entry in dict_value.entries:
            value, message = _native_value_of(document, entry.value)
            if message:
                return message
            boolean = _value_boolean(value)
            if boolean is not None:
                actual_values.append(boolean)
        if actual_values != expected_values:
            return "bool_values mismatch"
    nested = compare.sequence_field(expected, "nested_array")
    if nested is not None:
        array_value, message = _entry_by_key(document, root, "tags")
        if message:
            return message
        array = array_value.as_array()
        if array is None:
            return "tags must be an array"
        if len(array.elements) != len(nested):
            return "nested array count mismatch"
        for index, element in enumerate(array.elements):
            value, message = _native_value_of(document, element)
            if message:
                return message
            item = nested[index]
            if item.kind is Kind.STRING:
                if _value_text(value) != item.as_string():
                    return "nested element text mismatch"
            elif item.kind is Kind.OBJECT:
                dict_value = value.as_dict()
                if dict_value is None or dict_value.entries:
                    return "nested element must be an empty dict"
            else:
                return "unsupported nested expectation"
    string_values = compare.object_field(expected, "string_values")
    if string_values is not None:
        for key, item in string_values.as_object():
            value, message = _entry_by_key(document, root, key)
            if message:
                return message
            if item.kind is not Kind.STRING:
                return "expected string value"
            if _value_text(value) != item.as_string():
                return "string value mismatch"
    normalized = compare.boolean_field(expected, "line_end_normalized")
    if normalized is not None:
        value, message = _entry_by_key(document, root, "lines")
        if message:
            return message
        text = _value_text(value)
        if text is None:
            return "lines value missing"
        has_cr = "\r" in text
        if has_cr == normalized:
            return "line-end normalization mismatch"
    needs_reals = (
        compare.integer_field(expected, "real_count") is not None
        or compare.boolean_field(expected, "nan_admitted") is not None
        or compare.boolean_field(expected, "infinities_admitted") is not None
        or compare.object_field(expected, "exponent_value") is not None
    )
    if needs_reals:
        array = root.as_array()
        if array is None:
            return "root must be an array"
        reals = []
        for element in array.elements:
            value, message = _native_value_of(document, element)
            if message:
                return message
            if value.as_real() is not None:
                reals.append(value)
        real_count = compare.integer_field(expected, "real_count")
        if real_count is not None and len(reals) != real_count:
            return "real_count mismatch"
        nan_admitted = compare.boolean_field(expected, "nan_admitted")
        if nan_admitted is not None:
            actual = any(_value_real(value) != _value_real(value) for value in reals)
            if actual != nan_admitted:
                return "nan_admitted mismatch"
        infinities = compare.boolean_field(expected, "infinities_admitted")
        if infinities is not None:
            actual = any(
                abs(_value_real(value)) == float("inf") for value in reals
            )
            if actual != infinities:
                return "infinities_admitted mismatch"
        exponent = compare.object_field(expected, "exponent_value")
        if exponent is not None:
            expected_f64 = _expected_f64(exponent)
            actual = any(
                expected_f64 is not None
                and _value_real(value) is not None
                and _bits_equal(_value_real(value), expected_f64)
                for value in reals
            )
            if not actual:
                return "exponent_value mismatch"
    return None


def _xml_formation_samples(vector: runner.Case, samples) -> str | None:
    expected = vector.expected
    statuses = compare.sequence_field(expected, "statuses")
    diagnostics = compare.sequence_field(expected, "diagnostics")
    if statuses is None or diagnostics is None:
        return "missing expected.statuses/diagnostics"
    if len(samples) != len(statuses) or len(samples) != len(diagnostics):
        return "status/diagnostic count mismatch"
    integers = compare.sequence_field(expected, "integers")
    seconds = compare.sequence_field(expected, "seconds")
    data_hexes = compare.sequence_field(expected, "data_hexes")
    values = compare.sequence_field(expected, "values")
    for index, sample in enumerate(samples):
        document, message = _form_sample(vector, sample)
        if message:
            return message
        status_value = statuses[index]
        if status_value.kind is not Kind.STRING:
            return "status must be a string"
        status = status_value.as_string()
        if _status_name(document) != status:
            return "sample status mismatch"
        code = diagnostics[index]
        if code.kind is Kind.STRING:
            codes = [item.code for item in document.diagnostic_records()]
            if code.as_string() not in codes:
                return f"sample diagnostic {code.as_string()} not found"
        if status != "Complete":
            continue
        root, message = _root_value(document)
        if message:
            return message
        if integers is not None:
            expected_integer = 0
            ok = False
            if integers[index].kind is Kind.INTEGER:
                expected_integer = integers[index].as_integer()
                ok = True
            actual = _value_integer(root)
            if (actual is not None) != ok or (actual is not None and actual != expected_integer):
                return "sample integer mismatch"
        if seconds is not None:
            expected_seconds = _expected_f64(seconds[index])
            actual = _value_seconds(root)
            if (actual is None) != (expected_seconds is None) or (
                actual is not None and expected_seconds is not None and not _bits_equal(actual, expected_seconds)
            ):
                return "sample seconds mismatch"
        if data_hexes is not None:
            expected_text = ""
            ok = False
            if data_hexes[index].kind is Kind.STRING:
                expected_text = data_hexes[index].as_string()
                ok = True
            actual = _value_data_hex(root)
            if (actual is not None) != ok or (actual is not None and actual != expected_text):
                return "sample data hex mismatch"
        if values is not None:
            expected_value = values[index]
            if expected_value.kind is Kind.STRING and expected_value.as_string() == "":
                text = _value_text(root)
                data = root.as_data()
                empty = (text is not None and text == "") or (data is not None and not data.bytes)
                if not empty:
                    return "sample value is not empty"
            elif expected_value.kind is Kind.STRING:
                if _value_text(root) != expected_value.as_string():
                    return "sample value mismatch"
    return None


# ---------------------------------------------------------------------------
# Binary formation
# ---------------------------------------------------------------------------


def _binary_formation(vector: runner.Case) -> str | None:
    samples = compare.sequence_field(vector.input, "samples")
    if samples is not None:
        return _binary_formation_samples(vector, samples)
    document, message = _form_value(vector.input)
    if message:
        return message
    message = _assert_expected_status(document, vector.expected)
    if message:
        return message
    facts = document.binary_facts()
    if facts is not None:
        trailer = facts.trailer
        for name, actual in (
            ("num_objects", trailer.num_objects),
            ("top_object", trailer.top_object),
            ("offset_int_size", trailer.offset_int_size),
            ("object_ref_size", trailer.object_ref_size),
            ("sort_version", trailer.sort_version),
            ("offset_table_offset", trailer.offset_table_offset),
        ):
            message = _assert_u64_field(vector.expected, name, actual)
            if message:
                return message
        refs_of_top = compare.sequence_field(vector.expected, "refs_of_top")
        if refs_of_top is not None:
            top = trailer.top_object
            pairs = [
                (ref.position, ref.target)
                for ref in facts.refs
                if ref.owner == top
            ]
            pairs.sort(key=lambda pair: pair[0])
            actual = [target for _, target in pairs]
            expected_refs = [
                item.as_integer() for item in refs_of_top if item.kind is Kind.INTEGER
            ]
            if actual != expected_refs:
                return "refs_of_top mismatch"
        shared = compare.integer_field(vector.expected, "shared_ref_count")
        if shared is not None:
            counts: dict[int, int] = {}
            for ref in facts.refs:
                counts[ref.target] = counts.get(ref.target, 0) + 1
            shared_count = sum(1 for count in counts.values() if count > 1)
            if shared_count != shared:
                return "shared_ref_count mismatch"
    if _status_name(document) == "Complete":
        message = _binary_native_facts(document, vector.expected)
        if message:
            return message
    return None


def _binary_native_facts(document, expected) -> str | None:
    root, message = _root_value(document)
    if message:
        return message
    value = compare.string_field(expected, "value")
    if value is not None and _value_text(root) != value:
        return "value mismatch"
    top_kind = compare.string_field(expected, "top_kind")
    if top_kind is not None and _value_kind_name(root) != top_kind:
        return "top_kind mismatch"
    keys = compare.sequence_field(expected, "keys")
    if keys is not None:
        actual, message = _dict_keys_of(document, root)
        if message:
            return message
        message = _assert_strings(actual, keys, "key")
        if message:
            return message
    values = compare.sequence_field(expected, "values")
    if values is not None:
        dict_value, message = _dict_entries(root)
        if message:
            return message
        if len(dict_value.entries) != len(values):
            return "value count mismatch"
        for index, entry in enumerate(dict_value.entries):
            value, message = _native_value_of(document, entry.value)
            if message:
                return message
            message = _compare_scalar_value(value, values[index])
            if message:
                return message
    for name, key, check in (
        ("int_value", "int", _value_integer),
        ("data_hex", "data", _value_data_hex),
        ("str_value", "str", _value_text),
    ):
        field = compare.object_field(expected, name)
        if field is not None:
            entry, message = _entry_by_key(document, root, key)
            if message:
                return message
            actual = check(entry)
            if name == "data_hex":
                if actual != field.as_string():
                    return name + " mismatch"
            elif name == "int_value":
                if actual != field.as_integer():
                    return name + " mismatch"
            else:
                if actual != field.as_string():
                    return name + " mismatch"
    for name, key in (("real_value", "real"), ("f32_value", "f32"), ("date_seconds", "date"), ("fractional_seconds", "fractional")):
        field = compare.object_field(expected, name)
        if field is not None:
            entry, message = _entry_by_key(document, root, key)
            if message:
                return message
            expected_f64 = _expected_f64(field)
            actual = _value_seconds(entry) if name in ("date_seconds", "fractional_seconds") else _value_real(entry)
            if actual is None or expected_f64 is None or not _bits_equal(actual, expected_f64):
                return name + " mismatch"
    booleans = compare.sequence_field(expected, "bool_values")
    if booleans is not None:
        entry, message = _entry_by_key(document, root, "bool")
        if message:
            return message
        expected_values = [
            item.as_boolean() for item in booleans if item.kind is Kind.BOOLEAN
        ]
        actual_values = []
        array = entry.as_array()
        if array is not None:
            for element in array.elements:
                value, message = _native_value_of(document, element)
                if message:
                    return message
                boolean = _value_boolean(value)
                if boolean is not None:
                    actual_values.append(boolean)
        else:
            boolean = _value_boolean(entry)
            if boolean is not None:
                actual_values.append(boolean)
        if actual_values != expected_values:
            return "bool_values mismatch"
    array_elements = compare.sequence_field(expected, "array_elements")
    if array_elements is not None:
        entry, message = _entry_by_key(document, root, "array")
        if message:
            return message
        array = entry.as_array()
        if array is None:
            return "array must be an array"
        if len(array.elements) != len(array_elements):
            return "array count mismatch"
        for index, element in enumerate(array.elements):
            value, message = _native_value_of(document, element)
            if message:
                return message
            expected_integer = array_elements[index]
            if expected_integer.kind is not Kind.INTEGER:
                return "expected element must be an integer"
            if _value_integer(value) != expected_integer.as_integer():
                return "array element mismatch"
    return None


def _width_non_minimal(document, root) -> tuple[bool, bool]:
    facts = document.binary_facts()
    if facts is None or not facts.objects:
        return False, False
    marker = facts.objects[0].marker
    integer = _value_integer(root)
    if integer is not None:
        width = 1 << (marker & 0x0F)
        minimal = 8
        if integer <= 0xFF:
            minimal = 1
        elif integer <= 0xFFFF:
            minimal = 2
        elif integer <= 0xFFFFFFFF:
            minimal = 4
        return width > minimal, True
    uid = root.as_uid()
    if uid is not None:
        width = (marker & 0x0F) + 1
        value = uid.value
        minimal = 4
        if value <= 0xFF:
            minimal = 1
        elif value <= 0xFFFF:
            minimal = 2
        elif value <= 0xFF_FFFF:
            minimal = 3
        return width > minimal, True
    return False, False


def _binary_formation_samples(vector: runner.Case, samples) -> str | None:
    expected = vector.expected
    statuses = compare.sequence_field(expected, "statuses")
    diagnostics = compare.sequence_field(expected, "diagnostics")
    if statuses is None or diagnostics is None:
        return "missing expected.statuses/diagnostics"
    if len(samples) != len(statuses) or len(samples) != len(diagnostics):
        return "status/diagnostic count mismatch"
    integers = compare.sequence_field(expected, "integers")
    strings = compare.sequence_field(expected, "strings")
    uids = compare.sequence_field(expected, "uids")
    documents = []
    for index, sample in enumerate(samples):
        document, message = _form_sample(vector, sample)
        if message:
            return message
        status_value = statuses[index]
        if status_value.kind is not Kind.STRING:
            return "status must be a string"
        status = status_value.as_string()
        if _status_name(document) != status:
            return "sample status mismatch"
        code = diagnostics[index]
        if code.kind is Kind.STRING:
            codes = [item.code for item in document.diagnostic_records()]
            if code.as_string() not in codes:
                return f"sample diagnostic {code.as_string()} not found"
        if status == "Complete":
            root, message = _root_value(document)
            if message:
                return message
            if integers is not None:
                expected_integer = 0
                ok = False
                if integers[index].kind is Kind.INTEGER:
                    expected_integer = integers[index].as_integer()
                    ok = True
                actual = _value_integer(root)
                if (actual is not None) != ok or (actual is not None and actual != expected_integer):
                    return "sample integer mismatch"
            if strings is not None:
                expected_text = ""
                ok = False
                if strings[index].kind is Kind.STRING:
                    expected_text = strings[index].as_string()
                    ok = True
                actual = _value_text(root)
                if (actual is not None) != ok or (actual is not None and actual != expected_text):
                    return "sample string mismatch"
            if uids is not None:
                expected_uid = 0
                ok = False
                if uids[index].kind is Kind.INTEGER:
                    expected_uid = uids[index].as_integer()
                    ok = True
                uid = root.as_uid()
                actual = uid.value if uid is not None else None
                if (actual is not None) != ok or (actual is not None and actual != expected_uid):
                    return "sample uid mismatch"
        documents.append(document)
    non_minimal = compare.boolean_field(expected, "non_minimal_width_observed")
    if non_minimal is not None:
        actual = False
        for document in documents:
            root, message = _root_value(document)
            if not message:
                width_non_minimal, has = _width_non_minimal(document, root)
                if has and width_non_minimal:
                    actual = True
                    break
        if actual != non_minimal:
            return "non_minimal_width_observed mismatch"
    unpaired_hex = compare.string_field(expected, "unpaired_utf16be_hex")
    if unpaired_hex is not None:
        unpaired = None
        for document in documents:
            root, message = _root_value(document)
            if message:
                continue
            string = root.as_string()
            if string is not None and string.status().value == "UnpairedSurrogate":
                unpaired = document
                break
        if unpaired is None:
            return "no unpaired-surrogate sample"
        root, _ = _root_value(unpaired)
        string = root.as_string()
        if unpaired_hex is not None and string.utf16be_bytes().hex() != unpaired_hex:
            return "unpaired_utf16be_hex mismatch"
        unpaired_status = compare.string_field(expected, "unpaired_status")
        if unpaired_status is not None and string.status().value != unpaired_status:
            return "unpaired_status mismatch"
    sort_version_one = compare.boolean_field(expected, "sort_version_one_accepted")
    if sort_version_one is not None:
        actual = False
        for document in documents:
            if _status_name(document) == "Complete":
                facts = document.binary_facts()
                if facts is not None and facts.trailer.sort_version == 1:
                    actual = True
                    break
        if actual != sort_version_one:
            return "sort_version_one_accepted mismatch"
    has_extended_length = compare.integer_field(expected, "extended_array_length") is not None
    has_extended_object = compare.boolean_field(expected, "extended_count_is_object") is not None
    if has_extended_length or has_extended_object:
        complete = None
        for document in documents:
            if _status_name(document) == "Complete":
                complete = document
                break
        if complete is None:
            return "no complete sample"
        root, _ = _root_value(complete)
        extended_length = compare.integer_field(expected, "extended_array_length")
        if extended_length is not None:
            array = root.as_array()
            if array is None or len(array.elements) != extended_length:
                return "extended_array_length mismatch"
        count_is_object = compare.boolean_field(expected, "extended_count_is_object")
        if count_is_object is not None:
            facts = complete.binary_facts()
            if facts is None or not facts.objects:
                return "missing binary facts"
            extended = facts.objects[0].marker & 0x0F == 0x0F
            if extended != count_is_object:
                return "extended_count_is_object mismatch"
    return None


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

_QUERY_FAILURE_CODES = {
    "domain-mismatch": "plist.query.domain-mismatch@1",
    "unknown-operator": "plist.query.unknown-operator@1",
    "wrong-argument-type": "plist.query.wrong-argument-type@1",
    "invalid-argument": "plist.query.invalid-argument@1",
    "invalid-composition": "plist.query.invalid-composition@1",
    "missing-capability": "plist.query.missing-capability@1",
    "required-type-mismatch": "plist.query.type-mismatch@1",
    "cardinality-violation": "plist.query.cardinality-violation@1",
    "resource-limit": "plist.query.resource-limit@1",
    "cancelled": "plist.query.cancelled@1",
    "target-unavailable": "plist.query.target-unavailable@1",
}


def _query_failure_code(failure) -> str:
    return _QUERY_FAILURE_CODES.get(failure.kind.value, "plist.query.invalid-argument@1")


def _ordered_results() -> CapabilitySet:
    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    return capabilities


def _build_filters(filters):
    calls = []
    for filter_value in filters:
        operator = compare.string_field(filter_value, "operator")
        if operator is None:
            return None, "missing filter.operator"
        operator_id, version_text = operator.rsplit("@", 1)
        call = protocol_query.OperatorCall(operator_id, int(version_text))
        argument = compare.string_field(filter_value, "argument")
        if argument is not None:
            if operator_id == "plist.dict-key-equals":
                call = call.with_argument("key", PortableValue.string(argument))
            elif operator_id == "plist.value-type-is":
                call = call.with_argument("kind", PortableValue.string(argument))
            else:
                call = call.with_argument("argument", PortableValue.string(argument))
        calls.append(call)
    return calls, None


def _execute_native(document, calls):
    expression = protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT)
    for call in calls:
        expression = expression.then(call)
    definition = (
        protocol_query.QueryDefinition(protocol_query.domain_plist_native_v1())
        .with_expression(expression)
        .validate()
        .bind(_ordered_results())
    )
    return plist_query.execute_plist_native_query(
        definition, document, plist_query.PlistQueryLimits(), plist_query.PlistCancellationToken()
    )


def _dict_entry_keys(matches) -> list[str]:
    keys = []
    for item in matches:
        if item.kind is plist_query.PlistMatchKind.DICT_ENTRY and item.key is not None:
            keys.append(item.key.to_unicode())
    return keys


def _duplicate_key_groups(matches) -> int:
    counts: dict[str, int] = {}
    for key in _dict_entry_keys(matches):
        counts[key] = counts.get(key, 0) + 1
    return sum(1 for count in counts.values() if count > 1)


def _match_payload(match):
    if match.kind in (
        plist_query.PlistMatchKind.VALUE,
        plist_query.PlistMatchKind.DICT_ENTRY,
        plist_query.PlistMatchKind.ARRAY_ELEMENT,
    ):
        if match.value is not None and match.value_kind is not None:
            return match.value, match.value_kind
    return None, None


def _assert_typed_matches(document, matches, expected_matches) -> str | None:
    if len(matches) != len(expected_matches):
        return "match count differs from expected"
    for index, match in enumerate(matches):
        expected = expected_matches[index]
        expected_kind = compare.string_field(expected, "kind")
        if expected_kind is None:
            return "missing expected match kind"
        reference, kind = _match_payload(match)
        if reference is None:
            return "match without value payload"
        if kind.value != expected_kind:
            return "typed match kind mismatch"
        value, message = _native_value_of(document, reference)
        if message:
            return message
        expected_value = compare.object_field(expected, "value")
        if expected_value is not None:
            if expected_value.kind is Kind.INTEGER:
                if _value_integer(value) != expected_value.as_integer():
                    return "typed match integer mismatch"
        expected_seconds = compare.object_field(expected, "seconds")
        if expected_seconds is not None:
            seconds = _expected_f64(expected_seconds)
            actual = _value_seconds(value)
            if actual is None or seconds is None or not _bits_equal(actual, seconds):
                return "typed match date seconds mismatch"
    return None


def _query(vector: runner.Case) -> str | None:
    domain = compare.string_field(vector.input, "domain")
    if domain is None:
        return "missing input.domain"
    if domain == "plist.native-semantic-query@1":
        return _native_query(vector)
    if domain == "plist.binary-structure-query@1":
        return _binary_structure_query(vector)
    return "unknown query domain " + domain


def _native_query(vector: runner.Case) -> str | None:
    samples = compare.sequence_field(vector.input, "samples")
    if samples is not None:
        return _native_query_samples(vector, samples)
    document, message = _form_value(vector.input)
    if message:
        return message
    if _status_name(document) != "Complete":
        return "native-query input must form completely"
    filters = compare.sequence_field(vector.input, "filters")
    if filters is None:
        return "missing input.filters"
    calls, message = _build_filters(filters)
    if message:
        return message
    try:
        execution = _execute_native(document, calls)
    except protocol_query.QueryFailure as failure:
        return "execute: " + failure.code
    matches = list(execution.matches)
    terminal = compare.string_field(vector.expected, "terminal")
    if terminal is None:
        return "missing expected.terminal"
    if terminal != "Completed":
        return f"terminal {terminal} != Completed"
    keys = compare.sequence_field(vector.expected, "keys")
    if keys is not None:
        message = _assert_strings(_dict_entry_keys(matches), keys, "key")
        if message:
            return message
    value_types = compare.sequence_field(vector.expected, "value_types")
    if value_types is not None:
        actual = [
            item.value_kind.value
            for item in matches
            if item.kind is plist_query.PlistMatchKind.DICT_ENTRY and item.value_kind is not None
        ]
        expected_types = [item.as_string() for item in value_types if item.kind is Kind.STRING]
        if actual != expected_types:
            return "value_types mismatch"
    groups = compare.integer_field(vector.expected, "duplicate_groups")
    if groups is not None and _duplicate_key_groups(matches) != groups:
        return "duplicate_groups mismatch"
    return None


def _native_query_samples(vector: runner.Case, samples) -> str | None:
    document, message = _form_value(vector.input)
    if message:
        return message
    if _status_name(document) != "Complete":
        return "native-query input must form completely"
    terminals = compare.sequence_field(vector.expected, "terminals")
    if terminals is None:
        return "missing expected.terminals"
    if len(samples) != len(terminals):
        return "terminal count mismatch"
    mismatch_code = compare.string_field(vector.expected, "mismatch_code")
    integer_matches = compare.sequence_field(vector.expected, "integer_matches")
    date_matches = compare.sequence_field(vector.expected, "date_matches")
    for index, sample in enumerate(samples):
        filters = compare.sequence_field(sample, "filters")
        if filters is None:
            return "missing sample filters"
        last_operator = ""
        if len(filters) > 0:
            last_operator = compare.string_field(filters[-1], "operator") or ""
        calls, message = _build_filters(filters)
        if message:
            return message
        terminal_value = terminals[index]
        if terminal_value.kind is not Kind.STRING:
            return "terminal must be a string"
        terminal = terminal_value.as_string()
        if terminal == "Completed":
            try:
                execution = _execute_native(document, calls)
            except protocol_query.QueryFailure as failure:
                return "execute: " + failure.code
            matches = list(execution.matches)
            if last_operator == "plist.value-as-integer@1" and integer_matches is not None:
                message = _assert_typed_matches(document, matches, integer_matches)
                if message:
                    return message
            elif last_operator == "plist.value-as-date@1" and date_matches is not None:
                message = _assert_typed_matches(document, matches, date_matches)
                if message:
                    return message
        elif terminal == "Failed":
            try:
                _execute_native(document, calls)
            except protocol_query.QueryFailure as failure:
                if _query_failure_code(failure) != mismatch_code:
                    return "query failure code mismatch"
            else:
                return "execution must fail"
        else:
            return "unknown terminal " + terminal
    return None


def _execute_binary_structure(calls, document):
    expression = protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT)
    for call in calls:
        expression = expression.then(call)
    definition = (
        protocol_query.QueryDefinition(protocol_query.domain_plist_binary_structure_v1())
        .with_expression(expression)
        .validate()
        .bind(_ordered_results())
    )
    return plist_query.execute_plist_binary_query(
        definition, document, plist_query.PlistQueryLimits(), plist_query.PlistCancellationToken()
    )


def _binary_structure_query(vector: runner.Case) -> str | None:
    document, message = _form_value(vector.input)
    if message:
        return message
    if _status_name(document) != "Complete":
        return "binary-structure-query input must form completely"
    filters = compare.sequence_field(vector.input, "filters")
    if filters is None:
        return "missing input.filters"
    calls, message = _build_filters(filters)
    if message:
        return message
    terminal = compare.string_field(vector.expected, "terminal")
    if terminal is None:
        return "missing expected.terminal"
    try:
        _execute_binary_structure(calls, document)
    except protocol_query.QueryFailure as failure:
        return "execute: " + failure.code
    if terminal != "Completed":
        return f"terminal {terminal} != Completed"
    trailer = None
    objects = []
    offsets = []
    top_marker = None
    top_refs = []
    for call in calls:
        try:
            execution = _execute_binary_structure([call], document)
        except protocol_query.QueryFailure as failure:
            return "execute: " + failure.code
        for item in execution.matches:
            if item.kind is plist_query.PlistBinaryMatchKind.TRAILER:
                trailer = item
            elif item.kind is plist_query.PlistBinaryMatchKind.OBJECT:
                objects.append((item.index, item.marker))
            elif item.kind is plist_query.PlistBinaryMatchKind.OFFSET:
                offsets.append((item.index, item.offset))
            elif item.kind is plist_query.PlistBinaryMatchKind.TOP_OBJECT:
                top_marker = item.marker
                top_refs = [ref[1] for ref in item.refs]
    if trailer is None:
        return "missing trailer facts match"
    for name, actual in (
        ("num_objects", trailer.num_objects),
        ("top_object", trailer.top_object),
        ("offset_int_size", trailer.offset_int_size),
        ("object_ref_size", trailer.object_ref_size),
        ("sort_version", trailer.sort_version),
        ("offset_table_offset", trailer.offset_table_offset),
    ):
        message = _assert_u64_field(vector.expected, name, actual)
        if message:
            return message
    objects.sort(key=lambda pair: pair[0])
    offsets.sort(key=lambda pair: pair[0])
    object_offsets = compare.sequence_field(vector.expected, "object_offsets")
    if object_offsets is not None:
        expected = [
            item.as_integer() for item in object_offsets if item.kind is Kind.INTEGER
        ]
        actual = [offset for _, offset in offsets]
        if actual != expected:
            return "object_offsets mismatch"
    markers = compare.sequence_field(vector.expected, "markers")
    if markers is not None:
        expected = [item.as_string() for item in markers if item.kind is Kind.STRING]
        actual = [f"{marker:02x}" for _, marker in objects]
        if actual != expected:
            return "markers mismatch"
    top_marker_expected = compare.string_field(vector.expected, "top_marker")
    if top_marker_expected is not None:
        if top_marker is None or f"{top_marker:02x}" != top_marker_expected:
            return "top_marker mismatch"
    top_refs_expected = compare.sequence_field(vector.expected, "top_refs")
    if top_refs_expected is not None:
        expected = [item.as_integer() for item in top_refs_expected if item.kind is Kind.INTEGER]
        if top_refs != expected:
            return "top_refs mismatch"
    return None


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def _portable_kind_name(value) -> str | None:
    kind = value.kind
    if kind is Kind.OBJECT:
        fields = dict(value.as_object())
        if "seconds" in fields:
            return "date"
        if "uid" in fields:
            return "uid"
        return "dict"
    if kind is Kind.ENTRY_MAPPING:
        return "dict"
    if kind is Kind.SEQUENCE:
        return "array"
    if kind is Kind.STRING:
        return "string"
    if kind is Kind.INTEGER:
        return "integer"
    if kind in (Kind.BINARY_FLOAT64, Kind.BINARY_FLOAT32):
        return "real"
    if kind is Kind.BOOLEAN:
        return "boolean"
    if kind is Kind.BYTES:
        return "data"
    return None


def _mapping_entries(value) -> list:
    """Ordered (key, value) pairs of one projected dict value (the plist
    value-tree projector emits EntryMapping values)."""
    if value.kind is Kind.ENTRY_MAPPING:
        return [(key.as_string(), item) for key, item in value.as_entry_mapping()]
    return list(value.as_object())


def _assert_leaf(actual, expected) -> str | None:
    kind = compare.string_field(expected, "kind")
    if kind is None:
        return "missing leaf kind"
    actual_kind = _portable_kind_name(actual)
    if actual_kind != kind:
        return "leaf kind mismatch"
    if kind == "string":
        text = compare.string_field(expected, "text")
        if text is None:
            return "missing leaf text"
        if actual.kind is not Kind.STRING or actual.as_string() != text:
            return "leaf text mismatch"
    elif kind == "integer":
        expected_value = compare.object_field(expected, "value")
        if expected_value is None or expected_value.kind is not Kind.INTEGER:
            return "missing leaf integer"
        if actual.kind is not Kind.INTEGER or actual.as_integer() != expected_value.as_integer():
            return "leaf integer mismatch"
    elif kind == "real":
        expected_value = compare.object_field(expected, "value")
        if expected_value is None:
            return "missing leaf real"
        expected_f64 = _expected_f64(expected_value)
        actual_f64 = _expected_f64(actual)
        if actual_f64 is None or expected_f64 is None or not _bits_equal(actual_f64, expected_f64):
            return "leaf real mismatch"
    elif kind == "boolean":
        expected_value = compare.object_field(expected, "value")
        if expected_value is None or expected_value.kind is not Kind.BOOLEAN:
            return "missing leaf boolean"
        if actual.kind is not Kind.BOOLEAN or actual.as_boolean() != expected_value.as_boolean():
            return "leaf boolean mismatch"
    elif kind == "data":
        expected_hex = compare.string_field(expected, "hex")
        if expected_hex is None:
            return "missing leaf hex"
        if actual.kind is not Kind.BYTES or actual.as_bytes().hex() != expected_hex:
            return "leaf data hex mismatch"
    elif kind == "date":
        expected_seconds = compare.object_field(expected, "seconds")
        if expected_seconds is None:
            return "missing leaf seconds"
        expected_f64 = _expected_f64(expected_seconds)
        seconds = compare.object_field(actual, "seconds")
        if seconds is None:
            return "actual leaf date missing"
        actual_f64 = _expected_f64(seconds)
        if actual_f64 is None or expected_f64 is None or not _bits_equal(actual_f64, expected_f64):
            return "leaf date seconds mismatch"
    else:
        return "unknown leaf kind " + kind
    return None


def _projection_request(value) -> plist_projection.ProjectionRequest:
    collision = compare.string_field(value, "collision_policy")
    if collision == "Reject":
        return plist_projection.ProjectionRequest.require_object(plist_projection.CollisionPolicy.REJECT)
    if collision == "First":
        return plist_projection.ProjectionRequest.require_object(plist_projection.CollisionPolicy.FIRST)
    if collision == "Last":
        return plist_projection.ProjectionRequest.require_object(plist_projection.CollisionPolicy.LAST)
    return plist_projection.ProjectionRequest.value_tree()


def _find_mapping_entry(value, key: str):
    for entry_key, item in _mapping_entries(value):
        if entry_key == key:
            return item
    return None


def _projection(vector: runner.Case) -> str | None:
    samples = compare.sequence_field(vector.input, "samples")
    if samples is not None:
        return _projection_samples(vector, samples)
    document, message = _form_value(vector.input)
    if message:
        return message
    result = plist_projection.project(document, plist_projection.ProjectionRequest.value_tree())
    if result.failed is not None:
        return "projection must complete"
    projection = result.complete
    record = compare.string_field(vector.expected, "record")
    if record is not None:
        actual = compare.string_field(projection.value, "record")
        if actual != record:
            return "record mismatch"
    root_value = compare.object_field(projection.value, "root")
    if root_value is None:
        return "missing root member"
    root_kind = compare.string_field(vector.expected, "root_kind")
    if root_kind is not None:
        actual = _portable_kind_name(root_value)
        if actual != root_kind:
            return "root_kind mismatch"
    keys = compare.sequence_field(vector.expected, "keys")
    if keys is not None:
        actual = [key for key, _ in _mapping_entries(root_value)]
        expected_keys = [item.as_string() for item in keys if item.kind is Kind.STRING]
        if actual != expected_keys:
            return "keys mismatch"
    leaves = compare.object_field(vector.expected, "leaves")
    if leaves is not None:
        for key, leaf in leaves.as_object():
            entry = _find_mapping_entry(root_value, key)
            if entry is None:
                return f"leaf entry {key} missing"
            message = _assert_leaf(entry, leaf)
            if message:
                return message
    array_leaves = compare.object_field(vector.expected, "array_leaves")
    if array_leaves is not None:
        for key, leaf in array_leaves.as_object():
            entry = _find_mapping_entry(root_value, key)
            if entry is None:
                return f"array leaf entry {key} missing"
            if entry.kind is not Kind.SEQUENCE:
                return "array leaf must be a sequence"
            expected_elements = leaf.as_sequence()
            if len(entry.as_sequence()) != len(expected_elements):
                return "array leaf count mismatch"
            for index, element in enumerate(entry.as_sequence()):
                expected_text = expected_elements[index]
                if expected_text.kind is not Kind.STRING:
                    return "array leaf element must be a string"
                if element.kind is not Kind.STRING or element.as_string() != expected_text.as_string():
                    return "array leaf element mismatch"
    preserved = compare.boolean_field(vector.expected, "association_order_preserved")
    if preserved is not None and not preserved:
        return "association order not preserved"
    return None


def _projection_samples(vector: runner.Case, samples) -> str | None:
    expected = vector.expected
    fidelities = compare.sequence_field(expected, "fidelities")
    codes = compare.sequence_field(expected, "codes")
    events_after_first = compare.integer_field(expected, "events_after_first")
    first_completed_checked = False
    for index, sample in enumerate(samples):
        document, message = _form_sample(vector, sample)
        if message:
            return message
        result = plist_projection.project(document, _projection_request(sample))
        if fidelities is not None:
            fidelity_value = fidelities[index]
            if fidelity_value.kind is not Kind.STRING:
                return "fidelity must be a string"
            expected_fidelity = fidelity_value.as_string()
            fidelity_ok = (
                (result.failed is not None and expected_fidelity == "Failed")
                or (
                    result.complete is not None
                    and expected_fidelity in ("Transformed", "Exact")
                )
            )
            if not fidelity_ok:
                return "projection fidelity mismatch"
        if codes is not None:
            code_value = codes[index]
            if code_value.kind is Kind.STRING:
                if result.complete is not None:
                    return "projection must fail"
                if not result.failed.diagnostics or result.failed.diagnostics[0].code != code_value.as_string():
                    return "projection code mismatch"
        if result.complete is not None and not first_completed_checked:
            first_completed_checked = True
            first_sample = compare.object_field(expected, "first_sample")
            if first_sample is not None:
                keys = compare.sequence_field(first_sample, "keys")
                values = compare.sequence_field(first_sample, "values")
                if keys is None or values is None:
                    return "missing first_sample keys/values"
                # The require-object projection value carries the unique-key
                # object; the Python family wraps it in the value-tree record
                # (plist/projection.py:800-807), so the object is read from
                # the record's root member when the wrapper is present.
                object_value = result.complete.value
                if compare.string_field(object_value, "record") is not None:
                    root = compare.object_field(object_value, "root")
                    if root is None or _portable_kind_name(root) != "dict":
                        return "require-object projection must be an object"
                    entries = _mapping_entries(root)
                else:
                    if _portable_kind_name(object_value) != "dict":
                        return "require-object projection must be an object"
                    entries = _mapping_entries(object_value)
                if len(entries) != len(keys):
                    return "first_sample key count mismatch"
                for position, (entry_key, entry_value) in enumerate(entries):
                    expected_key = keys[position]
                    expected_value = values[position]
                    if expected_key.kind is not Kind.STRING or expected_value.kind is not Kind.STRING:
                        return "first_sample expectation must be strings"
                    if entry_key != expected_key.as_string() or entry_value.kind is not Kind.STRING or entry_value.as_string() != expected_value.as_string():
                        return "first_sample mismatch"
            if events_after_first is not None and events_after_first > 0:
                events = 0
                for event in result.complete.report.events:
                    if event.kind is plist_projection.ProjectionEventKind.ASSOCIATION_DISCARDED:
                        events += 1
                if events != events_after_first:
                    return "events_after_first mismatch"
    return None


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def _ordered_value(raw):
    """Converts one order-preserving decoded JSON structure (dicts as lists
    of pairs) into a PortableValue, preserving member order."""
    if raw is None:
        return PortableValue.null()
    if isinstance(raw, bool):
        return PortableValue.boolean(raw)
    if isinstance(raw, str):
        return PortableValue.string(raw)
    if isinstance(raw, int):
        return PortableValue.integer(raw)
    if isinstance(raw, list):
        if raw and isinstance(raw[0], tuple):
            return PortableValue.object(
                tuple((key, _ordered_value(value)) for key, value in raw)
            )
        return PortableValue.sequence(tuple(_ordered_value(item) for item in raw))
    if isinstance(raw, loader._NumberToken):
        import re as _re

        if _re.match(r"^-?\d+$", raw.text):
            return PortableValue.integer(int(raw.text))
        return PortableValue.decimal(loader._decimal_from_text(raw.text))
    raise ValueError(f"unsupported ordered JSON value {raw!r}")


def _ordered_records(vector_bytes: bytes) -> dict[str, PortableValue]:
    """Re-decodes the raw vector file order-preserving and extracts the
    ordered ``input.record`` values keyed by case id (the shared loader sorts
    Object members, which would destroy the materialization record's ordered
    association facts)."""
    decoder = json.JSONDecoder(
        object_pairs_hook=lambda pairs: list(pairs),
        parse_float=loader._parse_float_hook,
        parse_constant=loader._parse_constant_hook,
    )
    text = vector_bytes.decode("utf-8")
    root, _ = decoder.raw_decode(text)
    root_pairs = root if isinstance(root, list) else list(root.items())
    cases = next((value for key, value in root_pairs if key == "cases"), [])
    records: dict[str, PortableValue] = {}
    for case in cases:
        case_pairs = case if isinstance(case, list) else list(case.items())
        case_id = next((value for key, value in case_pairs if key == "id"), None)
        case_input = next((value for key, value in case_pairs if key == "input"), None)
        if isinstance(case_input, list):
            record = next(
                (value for key, value in case_input if key == "record"), None
            )
            if record is not None and case_id is not None:
                records[case_id] = _ordered_value(record)
    return records


def _materialization_request(style: str):
    if style == "plist.xml-canonical@1":
        return MaterializationRequest.new(
            ProfileId.new("plist.xml", 1),
            MaterializationStyleId.new("plist.xml-canonical", 1),
        )
    if style == "plist.binary-canonical@1":
        from consema.document.source import SourceEncoding

        return (
            MaterializationRequest.new(
                ProfileId.new("plist.binary", 1),
                MaterializationStyleId.new("plist.binary-canonical", 1),
            )
            .with_encoding(SourceEncoding.binary())
            .with_newline(_newline_none())
        )
    return None


def _newline_none():
    from consema.document.materialization import NewlinePolicy

    return NewlinePolicy.NONE


def _materialization_failure_code(failure) -> str:
    """The stable vector spelling of one materialization failure (mirrors
    go/plist/materialization.go Code(): the plist family failure codes are
    plist.materialization.*@1; the shared document layer maps them onto the
    core.materialization.*@1 codes, so the runner re-derives the plist
    spellings)."""
    if failure.kind is MaterializationFailureKind.UNREPRESENTABLE:
        if failure.name == "date":
            return "plist.materialization.fractional-date@1"
        return "plist.materialization.unrepresentable@1"
    if failure.kind is MaterializationFailureKind.RESOURCE_LIMIT:
        return "plist.materialization.resource-limit@1"
    return failure.code


def _complete_materialization(record, request):
    result = plist_materialization.materialize(record, request)
    if isinstance(result, FailedMaterializationAttempt):
        return None, "materialization failed: " + _materialization_failure_code(result.failure)
    return result, ""


def _materialization(conformance_runner: runner.Runner, vector: runner.Case) -> str | None:
    samples = compare.sequence_field(vector.input, "samples")
    if samples is not None:
        return _materialization_samples(conformance_runner, vector, samples)
    style = compare.string_field(vector.input, "style")
    if style is None:
        return "missing input.style"
    record = compare.object_field(vector.input, "record")
    if record is None:
        return "missing input.record"
    raw = loader.read_vector_file(conformance_runner.vectors_dir, "plist-v1.json")
    ordered = _ordered_records(raw)
    if vector.id in ordered:
        record = ordered[vector.id]
    request = _materialization_request(style)
    if request is None:
        return "unknown materialization style " + style
    complete, message = _complete_materialization(record, request)
    if message:
        return message
    closure = compare.boolean_field(vector.expected, "closure")
    if closure is not None and closure:
        if _status_name(complete.document) != "Complete":
            return "materialized document must be complete"
    render = compare.string_field(vector.expected, "render")
    if render is not None and complete.document.render() != render.encode("utf-8"):
        return "render mismatch"
    render_hex = compare.string_field(vector.expected, "render_hex")
    if render_hex is not None and complete.document.render().hex() != render_hex:
        return "render_hex mismatch"
    return None


def _add_truncate_policy(record, policy: PortableValue):
    entries = list(record.as_object()) + [("truncate_policy", policy)]
    return PortableValue.object(entries)


def _materialization_samples(conformance_runner: runner.Runner, vector: runner.Case, samples) -> str | None:
    expected = vector.expected
    raw = loader.read_vector_file(conformance_runner.vectors_dir, "plist-v1.json")
    ordered = _ordered_records(raw)
    canonical_hex = compare.string_field(expected, "canonical_hex")
    conversion_render = compare.string_field(expected, "conversion_render")
    closure = compare.boolean_field(expected, "closure")
    representation_change = compare.boolean_field(expected, "representation_change_reported")
    deduplicated = compare.integer_field(expected, "deduplicated_scalars")
    renders = compare.sequence_field(expected, "renders")
    codes = compare.sequence_field(expected, "codes")
    truncation_events = compare.integer_field(expected, "truncation_events")
    for index, sample in enumerate(samples):
        style = compare.string_field(sample, "style")
        if style is None:
            style = compare.string_field(vector.input, "style")
            if style is None:
                return "missing sample style"
        record = compare.object_field(sample, "record")
        if record is not None:
            record_value = record
            if vector.id in ordered:
                record_value = ordered[vector.id]
            policy = compare.string_field(sample, "truncate_policy")
            if policy is not None:
                record_value = _add_truncate_policy(
                    record, PortableValue.string(policy)
                )
            request = _materialization_request(style)
            if request is None:
                return "unknown materialization style " + style
            result = plist_materialization.materialize(record_value, request)
            if isinstance(result, CompleteMaterialization):
                if renders is not None:
                    expected_render = renders[index]
                    if expected_render.kind is not Kind.STRING:
                        return "expected render must be a string"
                    if result.document.render() != expected_render.as_string().encode("utf-8"):
                        return "render mismatch"
                if truncation_events is not None and truncation_events > 0:
                    events = 0
                    for event in result.report.events:
                        code = event[0] if isinstance(event, tuple) else getattr(event, "code", None)
                        if code == "plist.materialization.fractional-date@1":
                            events += 1
                    if events != truncation_events:
                        return "truncation events mismatch"
                if closure is not None and closure:
                    if _status_name(result.document) != "Complete":
                        return "materialized document must be complete"
            else:
                if codes is not None:
                    code_value = codes[index]
                    if code_value.kind is Kind.NULL:
                        return "materialization must complete"
                    if code_value.kind is not Kind.STRING:
                        return "expected code must be a string"
                    if _materialization_failure_code(result.failure) != code_value.as_string():
                        return "materialization failure code mismatch"
                else:
                    return "materialization must complete"
            continue
        # Source-document samples: normalization materializes the projected
        # record; conversion crosses the representation boundary.
        document, message = _form_value(sample)
        if message:
            return message
        if style == "plist.binary-canonical@1":
            projection = plist_projection.project(document, plist_projection.ProjectionRequest.value_tree())
            if projection.failed is not None:
                return "projection must complete"
            request = _materialization_request(style)
            complete, message = _complete_materialization(projection.complete.value, request)
            if message:
                return message
            if canonical_hex is not None and complete.document.render().hex() != canonical_hex:
                return "canonical_hex mismatch"
            if deduplicated is not None and deduplicated > 0:
                actual = _scalar_objects(document) - _scalar_objects(complete.document)
                if actual != deduplicated:
                    return "deduplicated_scalars mismatch"
            if closure is not None and closure:
                if _status_name(complete.document) != "Complete":
                    return "materialized document must be complete"
        else:
            converted, failure = _convert_document(document, PlistProfile.XML_V1)
            if failure:
                return "conversion failed: " + failure
            if conversion_render is not None and converted.document.render() != conversion_render.encode("utf-8"):
                return "conversion_render mismatch"
            if representation_change is not None and representation_change:
                if not converted.report.representation_changed():
                    return "representation change not reported"
            if closure is not None and closure:
                if _status_name(converted.document) != "Complete":
                    return "converted document must be complete"
    return None


def _convert_document(document: PlistDocument, target: PlistProfile):
    try:
        return plist_conversion.convert(document, target, PlistParseLimits()), None
    except PlistConversionFailure as failure:
        return None, failure.code


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------


def _conversion(vector: runner.Case) -> str | None:
    document, message = _form_value(vector.input)
    if message:
        return message
    if _status_name(document) != "Complete":
        return "conversion input must form completely"
    target_text = compare.string_field(vector.expected, "target")
    if target_text is None:
        return "missing expected.target"
    if target_text == "plist.binary@1":
        target = PlistProfile.BINARY_V1
    elif target_text == "plist.xml@1":
        target = PlistProfile.XML_V1
    else:
        return "unknown target profile " + target_text
    converted, failure = _convert_document(document, target)
    if failure:
        expected_code = compare.string_field(vector.expected, "code")
        if expected_code is None:
            return "conversion must complete"
        if failure != expected_code:
            return "conversion failure code mismatch"
        return None
    if compare.string_field(vector.expected, "code") is not None:
        return "conversion must fail"
    representation_change = compare.boolean_field(vector.expected, "representation_change_reported")
    if representation_change is not None and representation_change:
        if not converted.report.representation_changed():
            return "representation change not reported"
    closure = compare.boolean_field(vector.expected, "closure")
    if closure is not None and closure:
        if _status_name(converted.document) != "Complete":
            return "converted document must be complete"
    round_trip = compare.boolean_field(vector.expected, "round_trip")
    if round_trip is not None and round_trip:
        source_profile = _profile_of(vector.input)
        back, failure = _convert_document(converted.document, source_profile)
        if failure:
            return "round-trip conversion failed: " + failure
        if not _native_equal(document, back.document):
            return "round-trip native model mismatch"
    keys = compare.sequence_field(vector.expected, "dict_keys")
    if keys is not None:
        root, message = _root_value(converted.document)
        if message:
            return message
        actual, message = _dict_keys_of(converted.document, root)
        if message:
            return message
        message = _assert_strings(actual, keys, "key")
        if message:
            return message
    return None


def _native_equal(left: PlistDocument, right: PlistDocument) -> bool:
    left_native = left.document()
    right_native = right.document()
    if left_native is None or right_native is None:
        return False
    return left_native == right_native


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------


def _edit_path(operation) -> plist_edit.EditPath:
    path = compare.sequence_field(operation, "path")
    if path is not None:
        steps = []
        for element in path:
            if element.kind is Kind.STRING:
                steps.append(
                    plist_edit.EditPathStep.dict_key(
                        _plist_key(element.as_string()), 0
                    )
                )
            elif element.kind is Kind.INTEGER:
                steps.append(plist_edit.EditPathStep.array_index(element.as_integer()))
            else:
                return None
        return plist_edit.EditPath.new(tuple(steps))
    name = compare.string_field(operation, "dict")
    if name is None:
        name = compare.string_field(operation, "array")
    if name is not None:
        return plist_edit.EditPath.new(
            (plist_edit.EditPathStep.dict_key(_plist_key(name), 0),)
        )
    return None


def _plist_key(text: str):
    from consema.plist.native import PlistKey

    return PlistKey.from_unicode(text)


def _edit_value(value) -> plist_edit.EditValue | None:
    from consema.plist.native import (
        PlistBoolean,
        PlistData,
        PlistDate,
        PlistInteger,
        PlistReal,
        PlistString,
        PlistUid,
    )

    kind = compare.string_field(value, "kind")
    if kind is None:
        return None
    if kind == "string":
        text = compare.string_field(value, "text")
        if text is None:
            return None
        return plist_edit.EditValue.string(PlistString.from_unicode(text))
    if kind == "integer":
        payload = compare.object_field(value, "value")
        if payload is None or payload.kind is not Kind.INTEGER:
            return None
        return plist_edit.EditValue.integer(PlistInteger(payload.as_integer()))
    if kind == "real":
        payload = compare.object_field(value, "value")
        if payload is None:
            return None
        real = _expected_f64(payload)
        if real is None:
            return None
        return plist_edit.EditValue.real(PlistReal.double(real))
    if kind == "boolean":
        payload = compare.object_field(value, "value")
        if payload is None or payload.kind is not Kind.BOOLEAN:
            return None
        return plist_edit.EditValue.boolean(PlistBoolean(payload.as_boolean()))
    if kind == "date":
        payload = compare.object_field(value, "seconds")
        if payload is None:
            return None
        seconds = _expected_f64(payload)
        if seconds is None:
            return None
        return plist_edit.EditValue.date(PlistDate.from_seconds(seconds))
    if kind == "data":
        text = compare.string_field(value, "hex")
        if text is None:
            return None
        try:
            decoded = bytes.fromhex(text)
        except ValueError:
            return None
        return plist_edit.EditValue.data(PlistData(decoded))
    if kind == "uid":
        payload = compare.object_field(value, "value")
        if payload is None or payload.kind is not Kind.INTEGER:
            return None
        uid = payload.as_integer()
        if not 0 <= uid <= 0xFFFFFFFF:
            return None
        return plist_edit.EditValue.uid(PlistUid(uid))
    return None


def _build_transaction(document, operations):
    builder = plist_edit.EditTransactionBuilder(document)
    for operation in operations:
        op = compare.string_field(operation, "op")
        if op is None:
            return None, "missing op"
        if op == "plist.edit.set-value@1":
            path = _edit_path(operation)
            value_field = compare.object_field(operation, "value")
            value = _edit_value(value_field)
            if path is None or value is None:
                return None, "missing path/value"
            builder.set_value(path, value)
        elif op == "plist.edit.insert-dict-entry@1":
            path = _edit_path(operation)
            key = compare.string_field(operation, "key")
            value_field = compare.object_field(operation, "value")
            value = _edit_value(value_field)
            if path is None or key is None or value is None:
                return None, "missing path/key/value"
            placement_text = compare.string_field(operation, "placement") or "End"
            if placement_text != "End":
                return None, "unknown placement " + placement_text
            builder.insert_dict_entry(
                path, _plist_key(key), value, plist_edit.DictEntryPlacement.end()
            )
        elif op == "plist.edit.remove-dict-entry@1":
            path = _edit_path(operation)
            key = compare.string_field(operation, "key")
            if path is None or key is None:
                return None, "missing path/key"
            builder.remove_dict_entry(path, _plist_key(key), 0)
        elif op == "plist.edit.rename-dict-key@1":
            path = _edit_path(operation)
            from_key = compare.string_field(operation, "from")
            to_key = compare.string_field(operation, "to")
            if path is None or from_key is None or to_key is None:
                return None, "missing path/from/to"
            builder.rename_dict_key(
                path, _plist_key(from_key), 0, _plist_key(to_key)
            )
        elif op == "plist.edit.insert-array-element@1":
            path = _edit_path(operation)
            index = compare.integer_field(operation, "index")
            value_field = compare.object_field(operation, "value")
            value = _edit_value(value_field)
            if path is None or index is None or value is None:
                return None, "missing path/index/value"
            builder.insert_array_element(path, index, value)
        elif op == "plist.edit.remove-array-element@1":
            path = _edit_path(operation)
            index = compare.integer_field(operation, "index")
            if path is None or index is None:
                return None, "missing path/index"
            builder.remove_array_element(path, index)
        else:
            return None, "unknown edit op " + op
    return builder.build(), None


def _reparse(document: PlistDocument):
    profile = PlistProfile.BINARY_V1 if document.profile_id().id == "plist.binary" else PlistProfile.XML_V1
    return _form_bytes(document.render(), profile)


def _assert_edit_native(expected, committed) -> str | None:
    root, message = _root_value(committed)
    if message:
        return message
    top_kind = compare.string_field(expected, "top_kind")
    if top_kind is not None and _value_kind_name(root) != top_kind:
        return "top_kind mismatch"
    dict_a_keys = compare.sequence_field(expected, "dict_a_keys")
    if dict_a_keys is not None:
        dict_a, message = _entry_by_key(committed, root, "a")
        if message:
            return message
        actual, message = _dict_keys_of(committed, dict_a)
        if message:
            return message
        message = _assert_strings(actual, dict_a_keys, "key")
        if message:
            return message
    dict_a_values = compare.sequence_field(expected, "dict_a_values")
    if dict_a_values is not None:
        dict_a, message = _entry_by_key(committed, root, "a")
        if message:
            return message
        dict_value, message = _dict_entries(dict_a)
        if message:
            return message
        if len(dict_value.entries) != len(dict_a_values):
            return "value count mismatch"
        for index, entry in enumerate(dict_value.entries):
            value, message = _native_value_of(committed, entry.value)
            if message:
                return message
            message = _compare_scalar_value(value, dict_a_values[index])
            if message:
                return message
    arr_elements = compare.sequence_field(expected, "arr_elements")
    if arr_elements is not None:
        array_value, message = _entry_by_key(committed, root, "arr")
        if message:
            return message
        array = array_value.as_array()
        if array is None:
            return "arr must be an array"
        if len(array.elements) != len(arr_elements):
            return "array count mismatch"
        for index, element in enumerate(array.elements):
            value, message = _native_value_of(committed, element)
            if message:
                return message
            message = _compare_scalar_value(value, arr_elements[index])
            if message:
                return message
    elements = compare.sequence_field(expected, "elements")
    if elements is not None:
        array = root.as_array()
        if array is None:
            return "root must be an array"
        if len(array.elements) != len(elements):
            return "array count mismatch"
        for index, element in enumerate(array.elements):
            value, message = _native_value_of(committed, element)
            if message:
                return message
            message = _compare_scalar_value(value, elements[index])
            if message:
                return message
    element_kinds = compare.sequence_field(expected, "element_kinds")
    if element_kinds is not None:
        array = root.as_array()
        if array is None:
            return "root must be an array"
        if len(array.elements) != len(element_kinds):
            return "array count mismatch"
        for index, element in enumerate(array.elements):
            value, message = _native_value_of(committed, element)
            if message:
                return message
            expected_kind = element_kinds[index]
            if expected_kind.kind is not Kind.STRING:
                return "kind must be a string"
            if _value_kind_name(value) != expected_kind.as_string():
                return "element kind mismatch"
    return None


def _edit(vector: runner.Case) -> str | None:
    samples = compare.sequence_field(vector.input, "samples")
    if samples is not None:
        return _edit_conflicts(vector, samples)
    document, message = _form_value(vector.input)
    if message:
        return message
    if _status_name(document) != "Complete":
        return "edit input must form completely"
    operations = compare.sequence_field(vector.input, "operations")
    if operations is None:
        return "missing input.operations"
    transaction, message = _build_transaction(document, operations)
    if message:
        return message
    try:
        commit = plist_edit.commit(document, transaction)
    except PlistEditFailure as failure:
        return "edit failed: " + failure.code
    committed = commit.document
    if _status_name(committed) != "Complete":
        return "committed document must be complete"
    reparse_closure = compare.boolean_field(vector.expected, "reparse_closure")
    if reparse_closure is not None and reparse_closure:
        reparsed, message = _reparse(committed)
        if message:
            return message
        if _status_name(reparsed) != "Complete":
            return "committed document must reparse completely"
    patch_replays = compare.boolean_field(vector.expected, "patch_replays")
    if patch_replays is not None and patch_replays:
        try:
            replay = commit.source_patch.apply(document.source, SourcePatchLimits())
        except Exception:
            return "patch does not replay"
        if replay.bytes() != committed.render():
            return "patch does not replay"
    untouched_byte_proof = compare.boolean_field(vector.expected, "untouched_byte_proof")
    untouched_object_bytes = compare.boolean_field(vector.expected, "untouched_object_bytes")
    if untouched_byte_proof or untouched_object_bytes:
        try:
            commit.untouched_proof.verify(
                document.source,
                committed.source,
                list(commit.source_patch.replacements),
            )
        except Exception as error:
            return "untouched proof: " + str(error)
    if untouched_object_bytes:
        base = document.render()
        target = committed.render()
        for region in commit.untouched_proof.regions:
            if base[region.old_start : region.old_end] != target[region.new_start : region.new_end]:
                return "untouched region content changed"
    message = _assert_edit_native(vector.expected, committed)
    if message:
        return message
    return None


def _edit_conflicts(vector: runner.Case, samples) -> str | None:
    expected = vector.expected
    codes = compare.sequence_field(expected, "codes")
    if codes is None:
        return "missing expected.codes"
    base_unchanged = compare.boolean_field(expected, "base_unchanged")
    if len(samples) != len(codes):
        return "code count mismatch"
    for index, sample in enumerate(samples):
        document, message = _form_sample(vector, sample)
        if message:
            return message
        operations = compare.sequence_field(sample, "operations")
        if operations is None:
            return "missing operations"
        wrong_source = compare.object_field(sample, "wrong_source")
        if wrong_source is not None:
            other, message = _form_value(wrong_source)
            if message:
                return message
            transaction, message = _build_transaction(other, operations)
            if message:
                return message
        else:
            transaction, message = _build_transaction(document, operations)
            if message:
                return message
        try:
            plist_edit.commit(document, transaction)
        except PlistEditFailure as failure:
            expected_code = codes[index]
            if expected_code.kind is not Kind.STRING:
                return "expected code must be a string"
            if failure.code != expected_code.as_string():
                return "edit failure code mismatch"
        else:
            return "edit must fail"
        if base_unchanged is not None and base_unchanged:
            if document.render() != document.source.bytes():
                return "base document changed"
    return None


runner.register_suite("plist-v1.json", "consema.plist.conformance@1", "", 45, run)
