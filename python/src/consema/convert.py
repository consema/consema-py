"""consema.convert — the audited projection-to-materialization composition
(crates/consema/src/conversion.rs; RFC 0016 §3.2: convert lives in the root
package only; RFC 0004).

Every ``convert_*`` function composes one format-owned projection and the
requested target materializer, retaining the intermediate portable value,
both provenance directions, and the two-stage report. The composition never
invents a cross-format convention: the projection target, the
materialization request, the mapping policy, and the representability
policy are explicit caller choices (``MaterializationRequest`` defaults:
UTF-8, LF, Object-only ``MappingPolicy.REQUIRE_OBJECT``,
``RepresentabilityPolicy.EXACT_ONLY``).

Loss discipline: a projection that contains explicitly irreversible loss
fails atomically with :class:`ConversionFailure` (kind
``UNAUTHORIZED_LOSS``) unless every lossy event carries an explicit
authorizing policy rule (conversion.rs convert_json); a failure never
returns a partial target document.

Baseline families (JSON, TOML, YAML, INI, Java Properties) project plain
portable values that convert to every target family under the target's
representability rules. The record families (XML, plist, HCL) project
versioned internal records (``xml.element-tree@1``, ``plist.value-tree@1``,
``hcl.body@1``; RFC 0012 §9, RFC 0013 §9, RFC 0014 §8.2) that only their
owning format family's materializer consumes: the record-consumption gate
fails a conversion atomically with the shared invalid-request vocabulary
whenever the record's owning family is not the target profile's family.
Same-family directions pass the gate and the owning materializer consumes
the record under its own validation and closure.

Authority: crates/consema/src/conversion.rs (composition algebra); RFC 0004
(materialization/convert); go/conversion.go as a cross-reference only.
"""

from __future__ import annotations

import enum

from consema.core.value import Kind, PortableValue
from consema.document.ids import ProfileId
from consema.document.materialization import (
    CompleteMaterialization,
    FailedMaterializationAttempt,
    MaterializationFidelity as DocumentMaterializationFidelity,
    MaterializationFailure,
    MaterializationRequest,
    MaterializationResult,
)
from consema.registry import Document

__all__ = [
    "CompleteConversion",
    "ConversionFailure",
    "ConversionFailureKind",
    "ConversionFidelity",
    "ConversionMaterializationProvenance",
    "ConversionMaterializationReport",
    "ConversionProjectionProvenance",
    "ConversionProjectionReport",
    "ConversionReport",
    "convert_hcl",
    "convert_ini",
    "convert_json",
    "convert_plist",
    "convert_properties",
    "convert_toml",
    "convert_xml",
    "convert_yaml",
    "materialize_target",
]

# ---------------------------------------------------------------------------
# fidelities
# ---------------------------------------------------------------------------


class ConversionFidelity(enum.Enum):
    """Whole-conversion semantic fidelity (conversion.rs:42-51). The
    ordering Exact < Transformed < Lossy is frozen: the overall fidelity is
    the worst fidelity across both stages."""

    EXACT = "Exact"
    TRANSFORMED = "Transformed"
    LOSSY = "Lossy"


class MaterializationFidelity(enum.Enum):
    """Closed materialization fidelity of the composition stage (the family
    materialization fidelities map onto this root-level value)."""

    EXACT = "Exact"
    TRANSFORMED = "Transformed"


# ---------------------------------------------------------------------------
# two-stage reports and provenance
# ---------------------------------------------------------------------------


class ConversionProjectionReport:
    """Retains the complete format-owned projection report without
    flattening its facts (conversion.rs:53-72). Exactly one family report is
    set, matching the source family."""

    __slots__ = ("json", "toml", "yaml", "ini", "properties", "xml", "plist", "hcl")

    def __init__(self, json=None, toml=None, yaml=None, ini=None, properties=None,
                 xml=None, plist=None, hcl=None):
        self.json = json
        self.toml = toml
        self.yaml = yaml
        self.ini = ini
        self.properties = properties
        self.xml = xml
        self.plist = plist
        self.hcl = hcl

    def event_codes(self) -> list[str]:
        """The frozen semantic-model wire codes of the report events in
        source/operation order. JSON structure-reencoded events are
        ``json.projection.structure-reencoded@1``, duplicate-collapse events
        are ``json.object.duplicate-member@1``; TOML and Java Properties
        events carry their own frozen codes. The YAML/INI/XML/plist/HCL
        event kinds have no frozen wire code and are omitted."""
        codes: list[str] = []
        if self.json is not None:
            for event in self.json.events:
                if event.kind.value == "StructureReencoded":
                    codes.append("json.projection.structure-reencoded@1")
                elif event.kind.value == "DuplicateCollapsed":
                    codes.append("json.object.duplicate-member@1")
        if self.toml is not None:
            for event in self.toml.events:
                codes.append(event.code)
        if self.properties is not None:
            for event in self.properties.events:
                codes.append(event.code)
        return codes


class ConversionProjectionProvenance:
    """Retains the complete format-owned source provenance of the
    projection stage (conversion.rs:74-93). Exactly one family provenance
    is set, matching the source family."""

    __slots__ = ("json", "toml", "yaml", "ini", "properties", "xml", "plist", "hcl")

    def __init__(self, json=None, toml=None, yaml=None, ini=None, properties=None,
                 xml=None, plist=None, hcl=None):
        self.json = json
        self.toml = toml
        self.yaml = yaml
        self.ini = ini
        self.properties = properties
        self.xml = xml
        self.plist = plist
        self.hcl = hcl


class ConversionMaterializationReport:
    """Retains the complete format-owned materialization report of the
    target stage (conversion.rs MaterializationReport). Exactly one family
    report is set, matching the target family."""

    __slots__ = ("json", "toml", "yaml", "ini", "properties", "xml", "plist", "hcl")

    def __init__(self, json=None, toml=None, yaml=None, ini=None, properties=None,
                 xml=None, plist=None, hcl=None):
        self.json = json
        self.toml = toml
        self.yaml = yaml
        self.ini = ini
        self.properties = properties
        self.xml = xml
        self.plist = plist
        self.hcl = hcl

    def event_codes(self) -> list[str]:
        """The ordered materialization report event codes."""
        codes: list[str] = []
        for report in (self.json, self.toml, self.yaml, self.ini, self.properties,
                       self.xml, self.plist, self.hcl):
            if report is None:
                continue
            for event in report.events:
                code = getattr(event, "code", None)
                if code is not None:
                    codes.append(code)
        return codes


class ConversionMaterializationProvenance:
    """Retains the complete format-owned
    portable-value-to-target-document provenance of the target stage."""

    __slots__ = ("json", "toml", "yaml", "ini", "properties", "xml", "plist", "hcl")

    def __init__(self, json=None, toml=None, yaml=None, ini=None, properties=None,
                 xml=None, plist=None, hcl=None):
        self.json = json
        self.toml = toml
        self.yaml = yaml
        self.ini = ini
        self.properties = properties
        self.xml = xml
        self.plist = plist
        self.hcl = hcl


class ConversionReport:
    """The complete ordered report for both conversion stages
    (conversion.rs:95-149)."""

    __slots__ = (
        "projection_fidelity",
        "projection_report",
        "materialization_fidelity",
        "materialization_report",
        "overall_fidelity",
        "source_profile",
        "target_profile",
    )

    def __init__(
        self,
        projection_fidelity: ConversionFidelity,
        projection_report: ConversionProjectionReport,
        materialization_fidelity: MaterializationFidelity,
        materialization_report: ConversionMaterializationReport,
        overall_fidelity: ConversionFidelity,
        source_profile: ProfileId,
        target_profile: ProfileId,
    ):
        self.projection_fidelity = projection_fidelity
        self.projection_report = projection_report
        self.materialization_fidelity = materialization_fidelity
        self.materialization_report = materialization_report
        self.overall_fidelity = overall_fidelity
        self.source_profile = source_profile
        self.target_profile = target_profile


class CompleteConversion:
    """The complete conversion result with both provenance directions kept
    distinct (conversion.rs:151-164)."""

    __slots__ = (
        "document",
        "projected_value",
        "projection_provenance",
        "materialization_provenance",
        "report",
    )

    def __init__(
        self,
        document: Document,
        projected_value: PortableValue,
        projection_provenance: ConversionProjectionProvenance,
        materialization_provenance: ConversionMaterializationProvenance,
        report: ConversionReport,
    ):
        self.document = document
        self.projected_value = projected_value
        self.projection_provenance = projection_provenance
        self.materialization_provenance = materialization_provenance
        self.report = report


# ---------------------------------------------------------------------------
# failures
# ---------------------------------------------------------------------------


class ConversionFailureKind(enum.Enum):
    """Classifies a conversion failure (conversion.rs:280-308). No failure
    carries a partial target document."""

    PROJECTION_FAILED = "ProjectionFailed"
    MATERIALIZATION_FAILED = "MaterializationFailed"
    UNAUTHORIZED_LOSS = "UnauthorizedLoss"


class ConversionFailure(Exception):
    """The typed conversion failure; implements the RFC 0016 §6 code
    contract with the frozen registered codes (conversion.rs:310-333).
    Exactly one payload group is set per kind."""

    def __init__(
        self,
        kind: ConversionFailureKind,
        *,
        projection_report: ConversionProjectionReport | None = None,
        projection_diagnostics: tuple = (),
        partial_analysis: tuple = (),
        yaml_projection_failure=None,
        materialization_failure=None,
        materialization_report: ConversionMaterializationReport | None = None,
        analyzed_input_paths: tuple = (),
        invalid_request_reason: str | None = None,
        unsupported_profile: bool = False,
    ):
        super().__init__(kind.value)
        self.kind = kind
        self.projection_report = projection_report or ConversionProjectionReport()
        self.projection_diagnostics = projection_diagnostics
        self.partial_analysis = partial_analysis
        self.yaml_projection_failure = yaml_projection_failure
        self.materialization_failure = materialization_failure
        self.materialization_report = materialization_report or ConversionMaterializationReport()
        self.analyzed_input_paths = analyzed_input_paths
        self.invalid_request_reason = invalid_request_reason
        self.unsupported_profile = unsupported_profile

    def code(self) -> str:
        """The frozen registered code for the failure kind."""
        return {
            ConversionFailureKind.PROJECTION_FAILED: "core.conversion.projection-failed@1",
            ConversionFailureKind.MATERIALIZATION_FAILED: "core.conversion.materialization-failed@1",
            ConversionFailureKind.UNAUTHORIZED_LOSS: "core.conversion.unauthorized-loss@1",
        }[self.kind]


# ---------------------------------------------------------------------------
# the record-consumption gate
# ---------------------------------------------------------------------------

XML_ELEMENT_TREE_RECORD = "xml.element-tree@1"
PLIST_VALUE_TREE_RECORD = "plist.value-tree@1"
HCL_BODY_RECORD = "hcl.body@1"

_PUBLISHED_RECORDS = {
    XML_ELEMENT_TREE_RECORD: "xml",
    PLIST_VALUE_TREE_RECORD: "plist",
    HCL_BODY_RECORD: "hcl",
}


def _format_family(profile_id: str) -> str:
    from consema.registry import _FAMILY_BY_PROFILE

    return _FAMILY_BY_PROFILE.get(profile_id, "")


def _published_record(value: PortableValue) -> str | None:
    """One published Consema format record envelope id when the value is an
    object whose ``record`` member equals a published versioned record id;
    any other object is ordinary content."""
    if value.kind is not Kind.OBJECT:
        return None
    for key, member in value.as_object():
        if key == "record" and member.kind is Kind.STRING:
            record = member.as_string()
            if record in _PUBLISHED_RECORDS:
                return record
    return None


def _record_family_message(record: str) -> str:
    return {
        XML_ELEMENT_TREE_RECORD: (
            "the projected value is the xml.element-tree@1 internal record; "
            "only the xml family materializer consumes it"
        ),
        PLIST_VALUE_TREE_RECORD: (
            "the projected value is the plist.value-tree@1 internal record; "
            "only the plist family materializer consumes it"
        ),
        HCL_BODY_RECORD: (
            "the projected value is the hcl.body@1 internal record; "
            "only the hcl family materializer consumes it"
        ),
    }[record]


def _validate_record_consumption(
    source_profile: ProfileId, value: PortableValue, request: MaterializationRequest
) -> ConversionFailure | None:
    """The record-consumption gate of the composition (conversion.rs
    validate_record_consumption). A record-format source (XML, plist, HCL)
    projects its versioned internal record envelope; the envelope is
    consumed only by the owning format family's materializer."""
    source_family = _format_family(source_profile.id)
    if source_family not in ("xml", "plist", "hcl"):
        return None
    record = _published_record(value)
    if record is None:
        return None
    if _PUBLISHED_RECORDS[record] == _format_family(request.target_profile.id):
        return None
    return ConversionFailure(
        ConversionFailureKind.MATERIALIZATION_FAILED,
        invalid_request_reason=_record_family_message(record),
    )


# ---------------------------------------------------------------------------
# the target dispatch
# ---------------------------------------------------------------------------


def _materialize_target(
    value: PortableValue, request: MaterializationRequest
) -> tuple[Document, MaterializationFidelity,
           ConversionMaterializationReport, ConversionMaterializationProvenance] | ConversionFailure:
    """Dispatches the intermediate portable value to the materializer of the
    target profile's family (conversion.rs materialize_target). Unknown
    target profiles fail atomically with the unsupported-profile
    vocabulary; the target document never exists on failure."""
    from consema.hcl import materialization as hcl_materialization
    from consema.ini import materialization as ini_materialization
    from consema.json import materialization as json_materialization
    from consema.plist import materialization as plist_materialization
    from consema.properties import materialization as properties_materialization
    from consema.toml import materialization as toml_materialization
    from consema.xml import materialization as xml_materialization
    from consema.yaml import materialization as yaml_materialization

    profile_id = request.target_profile.id
    result: MaterializationResult
    if profile_id in ("json.strict", "jsonc.bounded", "json5.standard"):
        result = json_materialization.materialize(value, request)
        return _unpack_target(result, "json")
    if profile_id == "toml.1.0":
        result = toml_materialization.materialize(value, request)
        return _unpack_target(result, "toml")
    if profile_id in ("yaml.1.2-core", "yaml.1.1-compat"):
        result = yaml_materialization.materialize_value(value, request)
        return _unpack_target(result, "yaml")
    if profile_id in ("ini.portable", "ini.windows", "ini.python-configparser"):
        result = ini_materialization.materialize(value, request)
        return _unpack_target(result, "ini")
    if profile_id in ("java-properties.reader", "java-properties.latin1"):
        result = properties_materialization.materialize(value, request)
        return _unpack_target(result, "properties")
    if profile_id == "xml.1.0-safe":
        result = xml_materialization.materialize(value, request)
        return _unpack_target(result, "xml")
    if profile_id in ("plist.xml", "plist.binary"):
        result = plist_materialization.materialize(value, request)
        return _unpack_target(result, "plist")
    if profile_id in ("hcl.native", "hcl.tfvars"):
        result = hcl_materialization.materialize(value, request)
        return _unpack_target(result, "hcl")
    return ConversionFailure(
        ConversionFailureKind.MATERIALIZATION_FAILED, unsupported_profile=True
    )


def _unpack_target(
    result: MaterializationResult, family: str
) -> tuple[Document, MaterializationFidelity,
           ConversionMaterializationReport, ConversionMaterializationProvenance] | ConversionFailure:
    if isinstance(result, FailedMaterializationAttempt):
        return ConversionFailure(
            ConversionFailureKind.MATERIALIZATION_FAILED,
            materialization_failure=result.failure,
            materialization_report=ConversionMaterializationReport(**{family: result.report}),
            analyzed_input_paths=result.analyzed_input_paths,
        )
    complete: CompleteMaterialization = result
    fidelity = (
        MaterializationFidelity.TRANSFORMED
        if complete.fidelity is DocumentMaterializationFidelity.TRANSFORMED
        else MaterializationFidelity.EXACT
    )
    return (
        Document(complete.document),
        fidelity,
        ConversionMaterializationReport(**{family: complete.report}),
        ConversionMaterializationProvenance(**{family: complete.provenance}),
    )


def _complete_conversion(
    source_profile: ProfileId,
    projected_value: PortableValue,
    projection_fidelity: ConversionFidelity,
    projection_report: ConversionProjectionReport,
    projection_provenance: ConversionProjectionProvenance,
    request: MaterializationRequest,
) -> "CompleteConversion | ConversionFailure":
    gate = _validate_record_consumption(source_profile, projected_value, request)
    if gate is not None:
        return gate
    target = _materialize_target(projected_value, request)
    if isinstance(target, ConversionFailure):
        return target
    document, materialization_fidelity, materialization_report, materialization_provenance = target
    materialization_overall = (
        ConversionFidelity.TRANSFORMED
        if materialization_fidelity is MaterializationFidelity.TRANSFORMED
        else ConversionFidelity.EXACT
    )
    return CompleteConversion(
        document=document,
        projected_value=projected_value,
        projection_provenance=projection_provenance,
        materialization_provenance=materialization_provenance,
        report=ConversionReport(
            projection_fidelity=projection_fidelity,
            projection_report=projection_report,
            materialization_fidelity=materialization_fidelity,
            materialization_report=materialization_report,
            overall_fidelity=_max_fidelity(projection_fidelity, materialization_overall),
            source_profile=source_profile,
            target_profile=request.target_profile,
        ),
    )


def _max_fidelity(left: ConversionFidelity, right: ConversionFidelity) -> ConversionFidelity:
    order = {
        ConversionFidelity.EXACT: 0,
        ConversionFidelity.TRANSFORMED: 1,
        ConversionFidelity.LOSSY: 2,
    }
    return left if order[left] >= order[right] else right


def _family_fidelity(fidelity) -> ConversionFidelity:
    """Maps one family projection fidelity onto the root conversion
    fidelity."""
    return {
        "Exact": ConversionFidelity.EXACT,
        "Transformed": ConversionFidelity.TRANSFORMED,
        "Lossy": ConversionFidelity.LOSSY,
    }[fidelity.value]


def _projection_failed(report: ConversionProjectionReport, attempt) -> ConversionFailure:
    diagnostics = getattr(attempt, "diagnostics", ())
    partial = getattr(attempt, "partial_analysis", ())
    return ConversionFailure(
        ConversionFailureKind.PROJECTION_FAILED,
        projection_report=report,
        projection_diagnostics=tuple(diagnostics),
        partial_analysis=tuple(partial),
    )


# ---------------------------------------------------------------------------
# the eight convert entries
# ---------------------------------------------------------------------------


def convert_json(
    source,
    projection_request,
    materialization_request: MaterializationRequest,
) -> "CompleteConversion | ConversionFailure":
    """Converts one JSON document by composing its published projection and
    the requested target materializer (conversion.rs convert_json). A lossy
    projection whose lossy events carry no explicit authorizing policy rule
    fails atomically with :class:`ConversionFailure` (UNAUTHORIZED_LOSS)
    before any materialization."""
    from consema.json import projection as json_projection

    result = json_projection.project(source, projection_request)
    if isinstance(result, json_projection.FailedProjectionAttempt):
        return _projection_failed(ConversionProjectionReport(json=result.report), result)
    projection = result
    if projection.fidelity.value == "Lossy":
        for event in projection.report.events:
            if event.loss.value == "Lossy" and event.policy is None:
                return ConversionFailure(ConversionFailureKind.UNAUTHORIZED_LOSS)
    return _complete_conversion(
        source.profile_id(),
        projection.value,
        _family_fidelity(projection.fidelity),
        ConversionProjectionReport(json=projection.report),
        ConversionProjectionProvenance(json=projection.provenance),
        materialization_request,
    )


def convert_toml(
    source,
    projection_request,
    materialization_request: MaterializationRequest,
) -> "CompleteConversion | ConversionFailure":
    """Converts one TOML document by composing its published projection and
    the requested target materializer (conversion.rs convert_toml). TOML 1.0
    exact projections never emit lossy events, so no unauthorized-loss gate
    applies."""
    from consema.toml import projection as toml_projection

    result = toml_projection.project_document(source, projection_request)
    if isinstance(result, toml_projection.FailedProjectionAttempt):
        return _projection_failed(ConversionProjectionReport(toml=result.report), result)
    projection = result
    return _complete_conversion(
        source.profile(),
        projection.value,
        _family_fidelity(projection.fidelity),
        ConversionProjectionReport(toml=projection.report),
        ConversionProjectionProvenance(toml=projection.provenance),
        materialization_request,
    )


def convert_yaml(
    source,
    projection_request,
    materialization_request: MaterializationRequest,
) -> "CompleteConversion | ConversionFailure":
    """Converts one YAML stream through its explicit PortableValue
    projection (conversion.rs convert_yaml). The default request rejects
    sharing and cycles; both fail atomically with the exact YAML projection
    failure, and conversion never implicitly enables an acyclic duplication
    strategy."""
    from consema.yaml import projection as yaml_projection

    result = yaml_projection.project_value(source, projection_request)
    if isinstance(result, yaml_projection.FailedValueProjection):
        return ConversionFailure(
            ConversionFailureKind.PROJECTION_FAILED,
            yaml_projection_failure=result,
        )
    projection = result
    return _complete_conversion(
        source.profile_id(),
        projection.value,
        _family_fidelity(projection.fidelity),
        ConversionProjectionReport(yaml=projection.report),
        ConversionProjectionProvenance(yaml=projection.provenance),
        materialization_request,
    )


def convert_ini(
    source,
    projection_request,
    materialization_request: MaterializationRequest,
) -> "CompleteConversion | ConversionFailure":
    """Converts one INI document by composing its explicit projection and a
    target materializer (conversion.rs convert_ini)."""
    from consema.ini import projection as ini_projection

    result = ini_projection.project(source, projection_request)
    if isinstance(result, ini_projection.FailedProjectionAttempt):
        return _projection_failed(ConversionProjectionReport(ini=result.report), result)
    projection = result
    return _complete_conversion(
        source.profile_id(),
        projection.value,
        _family_fidelity(projection.fidelity),
        ConversionProjectionReport(ini=projection.report),
        ConversionProjectionProvenance(ini=projection.provenance),
        materialization_request,
    )


def convert_properties(
    source,
    projection_request,
    materialization_request: MaterializationRequest,
) -> "CompleteConversion | ConversionFailure":
    """Converts one Java Properties document through an explicit duplicate
    policy (conversion.rs convert_properties)."""
    from consema.properties import projection as properties_projection

    result = properties_projection.project(source, projection_request)
    if isinstance(result, properties_projection.FailedProjectionAttempt):
        return _projection_failed(ConversionProjectionReport(properties=result.report), result)
    projection = result
    return _complete_conversion(
        source.profile_id(),
        projection.value,
        _family_fidelity(projection.fidelity),
        ConversionProjectionReport(properties=projection.report),
        ConversionProjectionProvenance(properties=projection.provenance),
        materialization_request,
    )


def convert_xml(
    source,
    projection_request,
    materialization_request: MaterializationRequest,
) -> "CompleteConversion | ConversionFailure":
    """Converts one XML document by composing its element-tree projection
    and a target materializer (conversion.rs convert_xml). The XML
    projection publishes the exact ``xml.element-tree@1`` record, which only
    the XML materializer family consumes; the record-consumption gate
    rejects the record atomically for every non-XML target. Recovered
    documents never project."""
    from consema.xml import projection as xml_projection

    result = xml_projection.project_document(source, projection_request)
    if isinstance(result, xml_projection.FailedProjectionAttempt):
        return _projection_failed(ConversionProjectionReport(xml=result.report), result)
    projection = result
    return _complete_conversion(
        source.profile(),
        projection.value,
        _family_fidelity(projection.fidelity),
        ConversionProjectionReport(xml=projection.report),
        ConversionProjectionProvenance(xml=projection.provenance),
        materialization_request,
    )


def convert_plist(
    source,
    projection_request,
    materialization_request: MaterializationRequest,
) -> "CompleteConversion | ConversionFailure":
    """Converts one Property List document by composing its value-tree
    projection and a target materializer (conversion.rs convert_plist). The
    plist projection publishes the exact ``plist.value-tree@1`` record,
    which only the plist materializer family consumes. Recovered documents
    never project."""
    from consema.plist import projection as plist_projection

    result = plist_projection.project(source, projection_request)
    if result.failed is not None:
        attempt = result.failed
        return _projection_failed(ConversionProjectionReport(plist=attempt.report), attempt)
    projection = result.complete
    return _complete_conversion(
        source.profile_id(),
        projection.value,
        _family_fidelity(projection.fidelity),
        ConversionProjectionReport(plist=projection.report),
        ConversionProjectionProvenance(plist=projection.provenance),
        materialization_request,
    )


def convert_hcl(
    source,
    projection_request,
    materialization_request: MaterializationRequest,
) -> "CompleteConversion | ConversionFailure":
    """Converts one HCL document by composing its body projection and a
    target materializer (conversion.rs convert_hcl). The HCL projection
    publishes the exact ``hcl.body@1`` record, which only the HCL
    materializer family consumes. The exact body target is the default
    ExpressionPolicyFail; conversion never implicitly enables the
    ProjectExpression strategy."""
    from consema.hcl import projection as hcl_projection

    result = hcl_projection.project(source, projection_request)
    if isinstance(result, hcl_projection.FailedProjectionAttempt):
        return _projection_failed(ConversionProjectionReport(hcl=result.report), result)
    projection = result
    return _complete_conversion(
        source.profile_id(),
        projection.value,
        _family_fidelity(projection.fidelity),
        ConversionProjectionReport(hcl=projection.report),
        ConversionProjectionProvenance(hcl=projection.provenance),
        materialization_request,
    )


def materialize_target(value: PortableValue, request: MaterializationRequest):
    """Public target-stage dispatch (used by the conformance runner for
    materialization cases without a source document)."""
    result = _materialize_target(value, request)
    if isinstance(result, ConversionFailure):
        return result
    document, fidelity, report, provenance = result
    return document, fidelity, report, provenance
