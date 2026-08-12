"""Root conversion composition tests (crates/consema/src/conversion.rs;
RFC 0004; the operations-v1.json convert cases as the machine-readable
authority).

Covers the two-stage projection-to-materialization composition for the
JSON and TOML directions, the overall fidelity algebra, the
unauthorized-loss gate, and the record-consumption gate of the record
families. Runs under pytest or directly.
"""

from __future__ import annotations

import sys

from consema.convert import (
    ConversionFailure,
    ConversionFailureKind,
    ConversionFidelity,
    convert_hcl,
    convert_json,
    convert_toml,
)
from consema.document.ids import MaterializationStyleId, ProfileId
from consema.document.limits import ParseLimits
from consema.document.materialization import MappingPolicy, MaterializationRequest, NewlinePolicy
from consema.hcl import document as hcl_document
from consema.hcl import kinds as hcl_kinds
from consema.hcl import limits as hcl_limits
from consema.hcl import projection as hcl_projection
from consema.json import kinds as json_kinds
from consema.json import parser as json_parser
from consema.json import projection as json_projection
from consema.toml import document as toml_document
from consema.toml import parser as toml_parser
from consema.toml import projection as toml_projection


def _json_parse(source: str):
    return json_parser.parse(source.encode("utf-8"), json_kinds.JsonProfile.STRICT_V1, ParseLimits())


def _toml_parse(source: str):
    return toml_parser.parse(source.encode("utf-8"), toml_document.TomlProfile.TOML10_V1, ParseLimits())


def _json_best_exact_request():
    return json_projection.ProjectionRequestBuilder(
        json_projection.ProjectionTarget.BEST_EXACT_CORE_V1
    ).build()


def _toml_request():
    return toml_projection.ProjectionRequest.new(toml_projection.ProjectionTarget.BEST_EXACT_CORE_V1)


def _json_compact_request():
    return (
        MaterializationRequest.new(
            ProfileId.new("json.strict", 1), MaterializationStyleId.new("json.canonical-compact", 1)
        )
        .with_newline(NewlinePolicy.NONE)
    )


def _toml_document_request():
    return MaterializationRequest.new(
        ProfileId.new("toml.1.0", 1), MaterializationStyleId.new("toml.canonical-document", 1)
    )


def test_convert_json_to_toml_exact():
    result = convert_json(_json_parse('{"service":{"port":8080,"enabled":true}}'),
                          _json_best_exact_request(), _toml_document_request())
    assert not isinstance(result, ConversionFailure)
    assert result.document.render() == b'"service" = { "port" = 8080, "enabled" = true }\n'
    assert result.report.overall_fidelity is ConversionFidelity.EXACT
    assert result.report.source_profile.id == "json.strict"
    assert result.report.target_profile.id == "toml.1.0"


def test_convert_toml_to_json_exact():
    result = convert_toml(_toml_parse('name = "api"\nports = [80, 443]\n'),
                          _toml_request(), _json_compact_request())
    assert not isinstance(result, ConversionFailure)
    assert result.document.render() == b'{"name":"api","ports":[80,443]}'
    assert result.report.overall_fidelity is ConversionFidelity.EXACT


def test_convert_duplicate_json_to_toml_fails_atomically():
    result = convert_json(_json_parse('{"a":1,"a":2}'), _json_best_exact_request(),
                          _toml_document_request())
    assert isinstance(result, ConversionFailure)
    assert result.kind is ConversionFailureKind.MATERIALIZATION_FAILED
    assert result.code() == "core.conversion.materialization-failed@1"


def test_convert_transformed_report_and_event_codes():
    entry_mapping = json_projection.ProjectionRequestBuilder(
        json_projection.ProjectionTarget.PROJECT_AS_ENTRY_MAPPING_V1
    ).build()
    request = _toml_document_request().with_mapping_policy(
        MappingPolicy.UNIQUE_STRING_ENTRIES_TO_OBJECT
    )
    result = convert_json(_json_parse('{"a":1}'), entry_mapping, request)
    assert not isinstance(result, ConversionFailure)
    assert result.report.overall_fidelity is ConversionFidelity.TRANSFORMED
    assert "json.projection.structure-reencoded@1" in result.report.projection_report.event_codes()
    assert "core.materialization.mapping-transformed@1" in result.report.materialization_report.event_codes()


def test_convert_keeps_both_provenance_directions():
    result = convert_json(_json_parse('{"a":1}'), _json_best_exact_request(), _toml_document_request())
    assert not isinstance(result, ConversionFailure)
    assert result.projected_value.kind.value == "Object"
    assert result.projection_provenance.json is not None
    assert result.materialization_provenance.toml is not None


def test_hcl_record_gate_rejects_non_hcl_target():
    source = hcl_document.parse(b"a = 1\n", hcl_kinds.HclProfile.NATIVE_V1,
                                hcl_document.HclEncodingSelection.PROFILE_DEFAULT,
                                hcl_limits.HclParseLimits())
    request = hcl_projection.ProjectionRequest.body()
    result = convert_hcl(source, request, _json_compact_request())
    assert isinstance(result, ConversionFailure)
    assert result.kind is ConversionFailureKind.MATERIALIZATION_FAILED
    assert "hcl.body@1" in (result.invalid_request_reason or "")


def main() -> None:
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn()
            print(f"PASS {name}")
        except Exception as error:  # noqa: BLE001
            failures += 1
            print(f"FAIL {name}: {error}")
    print(f"passed={sum(1 for n in globals() if n.startswith('test_')) - failures} failed={failures}")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()

