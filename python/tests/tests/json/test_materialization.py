"""Materialization and conversion golden transcriptions.

Cases covered:

- json-family-v2.json: json5.materialize.canonical-specials (138-142),
  json5.materialize.reject-finite-binary (144-148),
  json5.materialize.reject-profile-style-mismatch (150-154),
  json5.convert.finite-to-strict (156-160), json5.convert.nonfinite-to-
  strict-fails (162-166), json5.convert.strict-to-json5 (168-172).
- Pretty layout and request failures follow the Rust arbitration tests
  (crates/consema-json/src/materialization.rs:855-927).

Conversion is the audited Projection-to-Materialization composition with
explicit source/target profile ids (RFC 0004 §9); the report always
exposes both stages (RFC 0005 §9).
"""

from __future__ import annotations

import pytest

from consema.core.value import PortableValue, decimal
from consema.document.ids import MaterializationStyleId, ProfileId
from consema.document.limits import ParseLimits
from consema.document.materialization import (
    MaterializationFailure,
    MaterializationFailureKind,
    MaterializationLimits,
    MaterializationRequest,
    NewlinePolicy,
)
from consema.json import (
    JsonProfile,
    JsonStyle,
    materialization_failure_name,
    materialize,
    parse,
    requested_profile,
    requested_style,
)
from consema.json.parser import (
    BITS_NAN,
    BITS_NEGATIVE_INFINITY,
    BITS_NEGATIVE_NAN,
    BITS_POSITIVE_INFINITY,
)

DEFAULT_LIMITS = ParseLimits()


def strict_request(style: str = "json.canonical-compact", newline=NewlinePolicy.NONE):
    return MaterializationRequest.new(
        ProfileId.new("json.strict", 1), MaterializationStyleId.new(style, 1)
    ).with_newline(newline)


def json5_request(style: str = "json5.canonical-compact", newline=NewlinePolicy.NONE):
    return MaterializationRequest.new(
        ProfileId.new("json5.standard", 1), MaterializationStyleId.new(style, 1)
    ).with_newline(newline)


def test_json5_materialize_canonical_specials():
    # Case json5.materialize.canonical-specials (json-family-v2.json:138-142).
    values = PortableValue.sequence(
        [
            PortableValue.binary_float64(BITS_POSITIVE_INFINITY),
            PortableValue.binary_float64(BITS_NEGATIVE_INFINITY),
            PortableValue.binary_float64(BITS_NAN),
            PortableValue.binary_float64(BITS_NEGATIVE_NAN),
            PortableValue.string("a\u2028b"),
        ]
    )
    result = materialize(values, json5_request())
    assert result.document.render() == b'[Infinity,-Infinity,NaN,-NaN,"a\\u2028b"]'


def test_json5_materialize_reject_finite_binary():
    # Case json5.materialize.reject-finite-binary (json-family-v2.json:144-148).
    result = materialize(
        PortableValue.binary_float64(0x0000000000000000), json5_request()
    )
    assert not hasattr(result, "document")
    assert materialization_failure_name(result.failure) == "Unrepresentable"


def test_json5_materialize_reject_profile_style_mismatch():
    # Case json5.materialize.reject-profile-style-mismatch
    # (json-family-v2.json:150-154).
    # strict style under the json5 profile is a mismatch:
    result = materialize(
        PortableValue.null(),
        MaterializationRequest.new(
            ProfileId.new("json5.standard", 1),
            MaterializationStyleId.new("json.canonical-compact", 1),
        ).with_newline(NewlinePolicy.NONE),
    )
    assert not hasattr(result, "document")
    assert materialization_failure_name(result.failure) == "UnsupportedStyle"


def test_json5_convert_finite_to_strict():
    # Case json5.convert.finite-to-strict (json-family-v2.json:156-160).
    source = "{service:{port:8080,},}"
    document = parse(source.encode("utf-8"), JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    from consema.json.projection import (
        ProjectionRequestBuilder,
        ProjectionTarget,
        project,
    )

    projection = project(
        document,
        ProjectionRequestBuilder(ProjectionTarget.JSON5_BEST_EXACT_CORE_V1).build(),
    )
    result = materialize(projection.value, strict_request())
    assert result.document.render() == b'{"service":{"port":8080}}'
    assert result.fidelity.value == "Exact"


def test_json5_convert_nonfinite_to_strict_fails():
    # Case json5.convert.nonfinite-to-strict-fails (json-family-v2.json:162-166).
    document = parse(b"Infinity", JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    from consema.json.projection import (
        ProjectionRequestBuilder,
        ProjectionTarget,
        project,
    )

    projection = project(
        document,
        ProjectionRequestBuilder(ProjectionTarget.JSON5_BEST_EXACT_CORE_V1).build(),
    )
    result = materialize(projection.value, strict_request())
    assert not hasattr(result, "document")
    assert materialization_failure_name(result.failure) == "Unrepresentable"


def test_json5_convert_strict_to_json5():
    # Case json5.convert.strict-to-json5 (json-family-v2.json:168-172).
    source = '{"a":1}'
    document = parse(source.encode("utf-8"), JsonProfile.STRICT_V1, DEFAULT_LIMITS)
    from consema.json.projection import (
        ProjectionRequestBuilder,
        ProjectionTarget,
        project,
    )

    projection = project(
        document,
        ProjectionRequestBuilder(ProjectionTarget.BEST_EXACT_CORE_V1).build(),
    )
    result = materialize(projection.value, json5_request())
    assert result.document.render() == b'{"a":1}'
    assert result.fidelity.value == "Exact"


def test_pretty_layout_and_request_failures():
    # Pretty layout and explicit request failures (materialization.rs:855-927).
    values = PortableValue.sequence([PortableValue.boolean(True)])
    pretty = materialize(
        values, strict_request("json.canonical-pretty", NewlinePolicy.CRLF)
    )
    assert pretty.document.render() == b"[\r\n  true\r\n]\r\n"

    result = materialize(
        values, strict_request("json.canonical-pretty", NewlinePolicy.NONE)
    )
    assert materialization_failure_name(result.failure) == "UnsupportedNewline"

    result = materialize(
        PortableValue.binary_float64(0), strict_request()
    )
    assert materialization_failure_name(result.failure) == "Unrepresentable"

    result = materialize(
        PortableValue.string("too large"),
        strict_request().with_limits(
            MaterializationLimits(max_output_bytes=3)
        ),
    )
    assert materialization_failure_name(result.failure) == "ResourceLimit"
    assert result.failure.name == "output-bytes"


def test_profile_and_style_resolution():
    # requested_profile / requested_style resolution
    # (materialization.rs:113-142).
    request = strict_request()
    assert requested_profile(request) is JsonProfile.STRICT_V1
    assert requested_style(request, JsonProfile.STRICT_V1) is JsonStyle.COMPACT
    request5 = json5_request()
    assert requested_profile(request5) is JsonProfile.JSON5_STANDARD_V1
    assert requested_style(request5, JsonProfile.JSON5_STANDARD_V1) is JsonStyle.JSON5_COMPACT
    with pytest.raises(MaterializationFailure) as caught:
        requested_profile(
            MaterializationRequest.new(
                ProfileId.new("yaml.1.1", 1),
                MaterializationStyleId.new("json.canonical-compact", 1),
            )
        )
    assert caught.value.kind is MaterializationFailureKind.UNSUPPORTED_PROFILE


def test_materialization_closure_reproject():
    # Closure: output reparses under the exact requested profile and
    # reprojects to the identical PortableValue (RFC 0005 §9).
    value = PortableValue.object(
        [
            ("service", PortableValue.object([("port", PortableValue.integer(8080))])),
            ("ratio", PortableValue.decimal(decimal(25, -1))),
            ("enabled", PortableValue.boolean(True)),
        ]
    )
    result = materialize(value, strict_request())
    assert result.document.render() == b'{"service":{"port":8080},"ratio":25e-1,"enabled":true}'
    from consema.json.projection import (
        ProjectionRequestBuilder,
        ProjectionTarget,
        project,
    )

    reparsed = parse(result.document.render(), JsonProfile.STRICT_V1, DEFAULT_LIMITS)
    reprojected = project(
        reparsed,
        ProjectionRequestBuilder(ProjectionTarget.BEST_EXACT_CORE_V1).build(),
    )
    assert reprojected.value == value
