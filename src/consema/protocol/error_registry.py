"""The stable public diagnostic and failure code registry.

All records are transcribed VERBATIM from
crates/consema-protocol/src/error_registry.rs, the registry arbitration
source: ERROR_CODES_V1 (error_registry.rs:31-362, 55 codes), the per-version
new-code lists (SOURCE_CODES_V2_BEFORE_UTF8/AFTER_UTF8 at 364-410, 7 codes;
NEW_CODES_V3 at 446-615, 28; NEW_CODES_V4 at 647-660, 2; NEW_CODES_V5 at
692-933, 40; NEW_CODES_V6 at 965-1170, 34; NEW_CODES_V7 at 1205-1337, 21).
Versions v2..v7 are the sorted merges of the previous version plus the
version's new codes, exactly as the Rust const-merge builders produce
(error_registry.rs:412-1367); the counts are 55/62/90/92/132/166/187
(error_registry.rs:1717-1723). Go (go/protocol/error_registry.go) is a
cross-reference only.

The manifest form (`core.error-code-registry@1`, fields code / category /
introduced / stability / description) is also implemented here, including
strict validation (error_registry.rs:1573-1645).
"""

from __future__ import annotations

import enum

from consema.core.value import PortableValue
from consema.protocol.errors import ProtocolError, ProtocolErrorKind, protocol_error
from consema.protocol.schema import (
    exact_fields,
    schema_fields,
    sequence_of,
    string_of,
)


class DiagnosticCategory(enum.Enum):
    """The eleven frozen semantic categories (error_registry.rs:1657-1671)."""

    LEXICAL = "Lexical"
    SYNTAX = "Syntax"
    CONFORMANCE = "Conformance"
    SEMANTIC = "Semantic"
    QUERY = "Query"
    PROJECTION = "Projection"
    MATERIALIZATION = "Materialization"
    CONVERSION = "Conversion"
    EDIT = "Edit"
    RESOURCE = "Resource"
    ENCODING = "Encoding"


def parse_category(name: str, path: str) -> DiagnosticCategory:
    try:
        return DiagnosticCategory(name)
    except ValueError:
        raise protocol_error(
            ProtocolErrorKind.INVALID_VALUE, path, "unknown error-code category"
        ) from None


class ErrorCodeDescriptor:
    """One stable public code registry record (error_registry.rs:8-18)."""

    __slots__ = ("code", "category", "introduced", "description")

    def __init__(
        self,
        code: str,
        category: DiagnosticCategory,
        introduced: str,
        description: str,
    ):
        self.code = code
        self.category = category
        self.introduced = introduced
        self.description = description

    def __repr__(self) -> str:
        return f"ErrorCodeDescriptor({self.code!r}, {self.category.value})"


def _code(
    code: str, category: str, introduced: str, description: str
) -> ErrorCodeDescriptor:
    return ErrorCodeDescriptor(code, DiagnosticCategory(category), introduced, description)


# ---------------------------------------------------------------------------
# Verbatim transcription of ERROR_CODES_V1 (error_registry.rs:31-362).
# ---------------------------------------------------------------------------
_ERROR_CODES_V1 = [
    _code("core.diagnostic.truncated@1", "Resource", "0.1.0", "Diagnostic limit truncated a sequence"),
    _code("core.parse.resource-limit@1", "Resource", "0.1.0", "Parser resource limit was reached"),
    _code("core.projection.conflicting-policy@1", "Projection", "0.1.0", "Projection policy rules conflict"),
    _code("core.projection.invalid-policy-target@1", "Projection", "0.1.0", "Projection policy target is invalid"),
    _code("core.projection.resource-limit@1", "Resource", "0.1.0", "Projection resource limit was reached"),
    _code("core.projection.target-not-applicable@1", "Projection", "0.1.0", "Projection target does not apply"),
    _code("core.projection.wrong-snapshot-policy@1", "Projection", "0.1.0", "Projection policy uses another snapshot"),
    _code("core.protocol.invalid-json@1", "Encoding", "0.3.0", "Protocol JSON is invalid"),
    _code("core.protocol.invalid-pvce@1", "Encoding", "0.3.0", "Protocol PVCE is invalid"),
    _code("core.protocol.invalid-value@1", "Encoding", "0.3.0", "Protocol field value violates its invariant"),
    _code("core.protocol.missing-field@1", "Encoding", "0.3.0", "Required protocol field is absent"),
    _code("core.protocol.non-canonical-json@1", "Encoding", "0.3.0", "Protocol JSON is not canonical"),
    _code("core.protocol.process-local-handle@1", "Encoding", "0.3.0", "Process-local handle cannot cross the wire"),
    _code("core.protocol.resource-limit@1", "Resource", "0.3.0", "Protocol resource limit was reached"),
    _code("core.protocol.schema-mismatch@1", "Encoding", "0.3.0", "Protocol schema or field order does not match"),
    _code("core.protocol.unknown-contract@1", "Encoding", "0.3.0", "Protocol contract ID or version is unknown"),
    _code("core.protocol.unknown-field@1", "Encoding", "0.3.0", "Fixed protocol schema contains an unknown field"),
    _code("core.protocol.wrong-type@1", "Encoding", "0.3.0", "Protocol field has the wrong value type"),
    _code("core.query.cancelled@1", "Query", "0.3.0", "Query execution was cancelled"),
    _code("core.query.cardinality-violation@1", "Query", "0.3.0", "Query selection cardinality was violated"),
    _code("core.query.domain-mismatch@1", "Query", "0.3.0", "Query domain is unknown or mismatched"),
    _code("core.query.invalid-argument@1", "Query", "0.3.0", "Query operator argument is invalid"),
    _code("core.query.invalid-composition@1", "Query", "0.3.0", "Query operator roles cannot be composed"),
    _code("core.query.missing-capability@1", "Query", "0.3.0", "Query implementation lacks a required capability"),
    _code("core.query.required-type-mismatch@1", "Query", "0.3.0", "Required query value type did not match"),
    _code("core.query.resource-limit@1", "Resource", "0.3.0", "Query resource limit was reached"),
    _code("core.query.target-unavailable@1", "Query", "0.3.0", "Target native semantics are unavailable"),
    _code("core.query.unknown-operator@1", "Query", "0.3.0", "Query operator ID or version is unknown"),
    _code("core.query.wrong-argument-type@1", "Query", "0.3.0", "Query operator argument has the wrong type"),
    _code("core.source.invalid-utf8@1", "Lexical", "0.1.0", "Source bytes are not valid UTF-8"),
    _code("json.edit.representation-fallback@1", "Edit", "0.1.0", "JSON edit used an authorized canonical fallback"),
    _code("json.object.duplicate-member@1", "Semantic", "0.1.0", "JSON object contains duplicate member names"),
    _code("json.projection.duplicate-keys@1", "Projection", "0.1.0", "JSON projection encountered duplicate keys"),
    _code("json.projection.semantic-unavailable@1", "Projection", "0.1.0", "Recovered JSON region lacks native semantics"),
    _code("json.strict.comment-not-allowed@1", "Conformance", "0.1.0", "Strict JSON profile rejects comments"),
    _code("json.strict.leading-bom@1", "Conformance", "0.1.0", "Strict JSON source has a leading BOM"),
    _code("json.strict.trailing-comma@1", "Conformance", "0.1.0", "Strict JSON profile rejects trailing commas"),
    _code("json.syntax.expected-object-key@1", "Syntax", "0.1.0", "JSON object key was expected"),
    _code("json.syntax.expected-value@1", "Syntax", "0.1.0", "JSON value was expected"),
    _code("json.syntax.invalid-number@1", "Syntax", "0.1.0", "JSON number syntax is invalid"),
    _code("json.syntax.invalid-string-escape@1", "Syntax", "0.1.0", "JSON string escape is invalid"),
    _code("json.syntax.missing-array-close@1", "Syntax", "0.1.0", "JSON array close delimiter is missing"),
    _code("json.syntax.missing-colon@1", "Syntax", "0.1.0", "JSON member colon is missing"),
    _code("json.syntax.missing-comma@1", "Syntax", "0.1.0", "JSON container comma is missing"),
    _code("json.syntax.missing-object-close@1", "Syntax", "0.1.0", "JSON object close delimiter is missing"),
    _code("json.syntax.missing-value@1", "Syntax", "0.1.0", "JSON value is missing"),
    _code("json.syntax.trailing-content@1", "Syntax", "0.1.0", "JSON has trailing content"),
    _code("json.syntax.unexpected-character@1", "Syntax", "0.1.0", "JSON has an unexpected character"),
    _code("json.syntax.unexpected-word@1", "Syntax", "0.1.0", "JSON has an unexpected word"),
    _code("json.syntax.unterminated-block-comment@1", "Syntax", "0.1.0", "JSONC block comment is unterminated"),
    _code("json.syntax.unterminated-string@1", "Syntax", "0.1.0", "JSON string is unterminated"),
    _code("toml.edit.representation-fallback@1", "Edit", "0.2.0", "TOML edit used an authorized canonical fallback"),
    _code("toml.parse.syntax@1", "Syntax", "0.2.0", "TOML syntax is invalid"),
    _code("toml.projection.core-invariant@1", "Projection", "0.2.0", "TOML projection hit a core invariant"),
    _code("toml.projection.unrepresentable-datetime@1", "Projection", "0.2.0", "TOML temporal value is not exactly representable"),
]

# Verbatim transcription of the v2 additions (error_registry.rs:364-410).
_NEW_CODES_V2 = [
    _code("core.source.encoding-conflict@1", "Encoding", "0.4.0", "Source encoding facts conflict"),
    _code("core.source.invalid-sequence@1", "Lexical", "0.4.0", "Source bytes are invalid for the selected encoding"),
    _code("core.source.patch-base-mismatch@1", "Edit", "0.4.0", "SourcePatch base digest does not match"),
    _code("core.source.patch-original-mismatch@1", "Edit", "0.4.0", "SourcePatch original-byte precondition does not match"),
    _code("core.source.patch-target-mismatch@1", "Edit", "0.4.0", "SourcePatch target digest does not match"),
    _code("core.source.resource-limit@1", "Resource", "0.4.0", "Source construction or patch limit was reached"),
    _code("core.source.unsupported-bom@1", "Encoding", "0.4.0", "Source begins with an unsupported byte-order mark"),
]

# Verbatim transcription of NEW_CODES_V3 (error_registry.rs:446-615).
_NEW_CODES_V3 = [
    _code("core.conversion.materialization-failed@1", "Conversion", "0.5.0", "Conversion target materialization failed"),
    _code("core.conversion.projection-failed@1", "Conversion", "0.5.0", "Conversion source projection failed"),
    _code("core.conversion.unauthorized-loss@1", "Conversion", "0.5.0", "Conversion encountered loss without explicit authorization"),
    _code("core.edit.conflicting-edits@1", "Edit", "0.5.0", "Edit operations have conflicting source ownership"),
    _code("core.edit.duplicate-key@1", "Edit", "0.5.0", "Edit would create a duplicate key"),
    _code("core.edit.exact-literal-requires-literal@1", "Edit", "0.5.0", "Exact literal policy requires a literal operation"),
    _code("core.edit.formation-failed@1", "Edit", "0.5.0", "Edited bytes did not form the required target document"),
    _code("core.edit.incomplete-target@1", "Edit", "0.5.0", "Edit target is not a complete syntax node"),
    _code("core.edit.invalid-literal@1", "Edit", "0.5.0", "Edit literal is invalid for the target profile"),
    _code("core.edit.operation-unsupported@1", "Edit", "0.5.0", "Edit operation is not supported for the target"),
    _code("core.edit.precondition-failed@1", "Edit", "0.5.0", "Edit original-byte or digest precondition failed"),
    _code("core.edit.representation-incompatible@1", "Edit", "0.5.0", "Edit representation policy cannot preserve the target category"),
    _code("core.edit.resource-limit@1", "Resource", "0.5.0", "Edit planning or commit resource limit was reached"),
    _code("core.edit.semantic-unavailable@1", "Edit", "0.5.0", "Edit target native semantics are unavailable"),
    _code("core.edit.target-not-found@1", "Edit", "0.5.0", "Edit target or placement anchor was not found"),
    _code("core.edit.unsupported-value@1", "Edit", "0.5.0", "Edit value is not representable by the target profile"),
    _code("core.edit.wrong-role@1", "Edit", "0.5.0", "Edit target has the wrong structural role"),
    _code("core.edit.wrong-snapshot@1", "Edit", "0.5.0", "Edit target belongs to another snapshot"),
    _code("core.materialization.formation-failed@1", "Materialization", "0.5.0", "Generated bytes did not form the target profile"),
    _code("core.materialization.invalid-request@1", "Materialization", "0.5.0", "Materialization request fields are contradictory"),
    _code("core.materialization.mapping-transformed@1", "Materialization", "0.5.0", "Ordered mapping was explicitly transformed into an object"),
    _code("core.materialization.resource-limit@1", "Resource", "0.5.0", "Materialization resource limit was reached"),
    _code("core.materialization.unrepresentable@1", "Materialization", "0.5.0", "Portable input cannot be represented by the target profile"),
    _code("core.materialization.unsupported-encoding@1", "Encoding", "0.5.0", "Target profile does not support the requested encoding"),
    _code("core.materialization.unsupported-newline@1", "Materialization", "0.5.0", "Target style does not support the requested newline policy"),
    _code("core.materialization.unsupported-profile@1", "Materialization", "0.5.0", "Requested materialization profile is unavailable"),
    _code("core.materialization.unsupported-style@1", "Materialization", "0.5.0", "Requested materialization style is unavailable"),
    _code("json.projection.structure-reencoded@1", "Projection", "0.5.0", "JSON object structure was reversibly represented as an entry mapping"),
]

# Verbatim transcription of NEW_CODES_V4 (error_registry.rs:647-660).
_NEW_CODES_V4 = [
    _code("json5.string.unescaped-line-separator@1", "Conformance", "0.6.0", "JSON5 string contains an unescaped Unicode line separator"),
    _code("json5.syntax.invalid-identifier@1", "Syntax", "0.6.0", "JSON5 IdentifierName syntax is invalid"),
]

# Verbatim transcription of NEW_CODES_V5 (error_registry.rs:692-933).
_NEW_CODES_V5 = [
    _code("core.graph.invalid@1", "Semantic", "0.7.0", "PortableGraph construction invariants were violated"),
    _code("core.graph.resource-limit@1", "Resource", "0.7.0", "PortableGraph construction or traversal limit was reached"),
    _code("core.pgce.invalid@1", "Encoding", "0.7.0", "PGCE input is structurally invalid"),
    _code("core.pgce.non-canonical@1", "Encoding", "0.7.0", "PGCE input is valid but not canonical"),
    _code("core.pgce.resource-limit@1", "Resource", "0.7.0", "PGCE encode or decode limit was reached"),
    _code("core.pgce.unsupported-version@1", "Encoding", "0.7.0", "PGCE wire version is unsupported"),
    _code("yaml.alias.name-mismatch@1", "Semantic", "0.7.0", "YAML alias name does not match its resolved anchor"),
    _code("yaml.alias.name-unavailable@1", "Semantic", "0.7.0", "YAML alias event lacks a usable name"),
    _code("yaml.anchor.name-unavailable@1", "Semantic", "0.7.0", "YAML anchor event lacks a usable name"),
    _code("yaml.anchor.unknown@1", "Semantic", "0.7.0", "YAML alias refers to an undefined anchor"),
    _code("yaml.edit.anchor-dependency@1", "Edit", "0.7.0", "YAML edit would leave a live alias without its anchor"),
    _code("yaml.edit.anchor-not-visible@1", "Edit", "0.7.0", "YAML alias insertion target is not the visible anchor definition"),
    _code("yaml.edit.canonical-fallback@1", "Edit", "0.7.0", "YAML edit used an authorized canonical scalar fallback"),
    _code("yaml.edit.invalid-anchor-name@1", "Edit", "0.7.0", "YAML anchor edit name is invalid"),
    _code("yaml.edit.invalid-placement@1", "Edit", "0.7.0", "YAML structural edit placement is invalid"),
    _code("yaml.edit.structural-container-conflict@1", "Edit", "0.7.0", "Multiple structural edits target the same base YAML container"),
    _code("yaml.mapping.missing-value@1", "Semantic", "0.7.0", "YAML mapping event stream lacks an association value"),
    _code("yaml.materialization.cross-document-sharing@1", "Materialization", "0.7.0", "YAML cannot preserve graph sharing across document roots"),
    _code("yaml.materialization.round-trip-mismatch@1", "Materialization", "0.7.0", "Generated YAML did not reproduce the promised input value"),
    _code("yaml.materialization.tag-kind-mismatch@1", "Materialization", "0.7.0", "YAML tag is incompatible with the graph node kind"),
    _code("yaml.materialization.unsupported-tag@1", "Materialization", "0.7.0", "YAML materializer has no published constructor for a tag"),
    _code("yaml.native.invalid-source-span@1", "Semantic", "0.7.0", "YAML native event span is outside the source snapshot"),
    _code("yaml.native.trailing-events@1", "Semantic", "0.7.0", "YAML native composition left trailing structural events"),
    _code("yaml.native.trailing-named-occurrence@1", "Semantic", "0.7.0", "YAML native composition left an unmatched anchor or alias occurrence"),
    _code("yaml.native.unexpected-end@1", "Semantic", "0.7.0", "YAML native event stream ended unexpectedly"),
    _code("yaml.native.unexpected-event@1", "Semantic", "0.7.0", "YAML native event order is invalid"),
    _code("yaml.parse.syntax@1", "Syntax", "0.7.0", "YAML source does not satisfy the selected grammar"),
    _code("yaml.profile.version-directive@1", "Conformance", "0.7.0", "YAML version directive conflicts with the selected profile"),
    _code("yaml.projection.cycle@1", "Projection", "0.7.0", "YAML representation cycle cannot enter a PortableValue tree"),
    _code("yaml.projection.document-cardinality@1", "Projection", "0.7.0", "YAML stream cardinality does not satisfy a single-value projection"),
    _code("yaml.projection.graph-invalid@1", "Projection", "0.7.0", "YAML representation graph could not form a PortableGraph"),
    _code("yaml.projection.invalid-canonical-scalar@1", "Projection", "0.7.0", "YAML canonical scalar cannot form its promised PortableValue kind"),
    _code("yaml.projection.mapping-not-object@1", "Projection", "0.7.0", "YAML mapping does not satisfy the requested Object policy"),
    _code("yaml.projection.provenance-limit@1", "Resource", "0.7.0", "YAML graph projection provenance limit was reached"),
    _code("yaml.projection.resource-limit@1", "Resource", "0.7.0", "YAML value or graph projection limit was reached"),
    _code("yaml.projection.sharing@1", "Projection", "0.7.0", "YAML shared identity requires explicit tree-duplication policy"),
    _code("yaml.projection.unrepresentable-timestamp@1", "Projection", "0.7.0", "YAML timestamp is outside PortableValue temporal categories"),
    _code("yaml.projection.unsupported-tag@1", "Projection", "0.7.0", "YAML tag has no published target projection semantics"),
    _code("yaml.scalar.invalid-explicit-tag@1", "Semantic", "0.7.0", "YAML scalar content is invalid for its explicit tag"),
    _code("yaml.tag.kind-mismatch@1", "Semantic", "0.7.0", "YAML tag is incompatible with the representation node kind"),
]

# Verbatim transcription of NEW_CODES_V6 (error_registry.rs:965-1170).
_NEW_CODES_V6 = [
    _code("core.source.code-page-required@1", "Encoding", "0.8.0", "The selected source profile requires an explicit Windows code page"),
    _code("core.source.unsupported-code-page@1", "Encoding", "0.8.0", "The requested Windows code page is not in the portable registry"),
    _code("ini.edit.canonical-fallback@1", "Edit", "0.8.0", "INI editing used an authorized canonical representation fallback"),
    _code("ini.edit.case-collision@1", "Edit", "0.8.0", "INI editing would create a profile-equivalent name collision"),
    _code("ini.edit.invalid-name@1", "Edit", "0.8.0", "INI section or entry name is invalid for the selected profile"),
    _code("ini.edit.invalid-placement@1", "Edit", "0.8.0", "INI structural edit placement is invalid"),
    _code("ini.formation.case-collision@1", "Semantic", "0.8.0", "INI formation found profile-equivalent names with different spelling"),
    _code("ini.formation.duplicate-entry@1", "Semantic", "0.8.0", "INI formation found a duplicate entry"),
    _code("ini.formation.duplicate-section@1", "Semantic", "0.8.0", "INI formation found a duplicate section"),
    _code("ini.materialization.round-trip-mismatch@1", "Materialization", "0.8.0", "Generated INI did not reproduce the promised input value"),
    _code("ini.parse.invalid-character@1", "Syntax", "0.8.0", "INI source contains a character forbidden by the selected profile"),
    _code("ini.parse.invalid-continuation@1", "Syntax", "0.8.0", "INI continuation syntax is invalid"),
    _code("ini.parse.malformed-line@1", "Syntax", "0.8.0", "INI source line is malformed"),
    _code("ini.parse.malformed-section@1", "Syntax", "0.8.0", "INI section header is malformed"),
    _code("ini.parse.missing-delimiter@1", "Syntax", "0.8.0", "INI entry is missing a required key/value delimiter"),
    _code("ini.parse.missing-section@1", "Conformance", "0.8.0", "INI entry appears where the selected profile requires a section"),
    _code("ini.profile.encoding@1", "Encoding", "0.8.0", "INI source encoding conflicts with the selected profile"),
    _code("ini.profile.mismatch@1", "Conformance", "0.8.0", "INI operation profile does not match the document profile"),
    _code("ini.projection.collision@1", "Projection", "0.8.0", "INI projection encountered a rejected key or section collision"),
    _code("ini.projection.duplicate-collapsed@1", "Projection", "0.8.0", "INI projection collapsed a duplicate under explicit policy"),
    _code("ini.projection.incomplete-document@1", "Projection", "0.8.0", "Recovered INI syntax cannot enter a complete semantic projection"),
    _code("ini.query.invalid-name-mode@1", "Query", "0.8.0", "INI query name comparison mode is invalid"),
    _code("java-properties.edit.canonical-fallback@1", "Edit", "0.8.0", "Properties editing used an authorized canonical representation fallback"),
    _code("java-properties.edit.invalid-placement@1", "Edit", "0.8.0", "Properties structural edit placement is invalid"),
    _code("java-properties.java-string.invalid-wire@1", "Encoding", "0.8.0", "Exact Java UTF-16 string wire content is invalid"),
    _code("java-properties.java-string.non-canonical-wire@1", "Encoding", "0.8.0", "Exact Java UTF-16 string wire content is not canonical"),
    _code("java-properties.materialization.round-trip-mismatch@1", "Materialization", "0.8.0", "Generated Properties text did not reproduce the promised input value"),
    _code("java-properties.parse.malformed-unicode-escape@1", "Syntax", "0.8.0", "Properties Unicode escape is malformed"),
    _code("java-properties.profile.mismatch@1", "Conformance", "0.8.0", "Properties operation profile does not match the document profile"),
    _code("java-properties.projection.duplicate-collapsed@1", "Projection", "0.8.0", "Properties projection collapsed a duplicate under explicit policy"),
    _code("java-properties.projection.incomplete-document@1", "Projection", "0.8.0", "Recovered Properties syntax cannot enter a complete semantic projection"),
    _code("java-properties.projection.unpaired-surrogate@1", "Projection", "0.8.0", "Properties content with an unpaired surrogate cannot become a PortableValue String"),
    _code("java-properties.query.invalid-code-unit-filter@1", "Query", "0.8.0", "Properties query UTF-16 code-unit filter is invalid"),
    _code("java-properties.source.profile-encoding@1", "Encoding", "0.8.0", "Properties source encoding conflicts with the selected profile"),
]

# Verbatim transcription of NEW_CODES_V7 (error_registry.rs:1205-1337):
# the RFC 0015 §13.1 CLI error family (20 codes) plus the 0.13.0
# registration of json.projection.incomplete-document@1 (audit finding F3).
_NEW_CODES_V7 = [
    _code("cli.data.invalid-request@1", "Encoding", "0.12.0", "Request or plan file failed strict decoding"),
    _code("cli.data.io@1", "Encoding", "0.12.0", "Input file could not be read"),
    _code("cli.detection.ambiguous@1", "Semantic", "0.12.0", "Candidate profiles are ambiguous and no profile was selected"),
    _code("cli.internal.unclassified@1", "Semantic", "0.12.0", "Unclassified internal CLI error"),
    _code("cli.interrupted.signal@1", "Semantic", "0.12.0", "CLI execution was interrupted by a signal"),
    _code("cli.limit.batch-count@1", "Resource", "0.12.0", "Batch file count exceeded the configured limit"),
    _code("cli.limit.file-size@1", "Resource", "0.12.0", "Input file exceeded the CLI file-size limit"),
    _code("cli.limit.manifest-size@1", "Resource", "0.12.0", "Manifest or request input exceeded the size limit"),
    _code("cli.usage.invalid-argument@1", "Syntax", "0.12.0", "Known argument received an invalid value"),
    _code("cli.usage.invalid-format@1", "Syntax", "0.12.0", "--format is missing or invalid"),
    _code("cli.usage.missing-plan@1", "Syntax", "0.12.0", "--apply requires a prior plan"),
    _code("cli.usage.missing-required@1", "Syntax", "0.12.0", "A required argument such as --profile is missing"),
    _code("cli.usage.redaction-pattern@1", "Syntax", "0.12.0", "--redact-keys pattern is invalid"),
    _code("cli.usage.unknown-argument@1", "Syntax", "0.12.0", "Unknown argument or rejected abbreviation"),
    _code("cli.usage.unknown-command@1", "Syntax", "0.12.0", "Unknown command"),
    _code("cli.write.io@1", "Edit", "0.12.0", "Write I/O failure such as a full disk"),
    _code("cli.write.permission@1", "Edit", "0.12.0", "Permission denied while writing the target"),
    _code("cli.write.read-only@1", "Edit", "0.12.0", "Target file is read-only"),
    _code("cli.write.symlink-policy@1", "Edit", "0.12.0", "Write path rejected by the symlink policy"),
    _code("cli.write.target-is-directory@1", "Edit", "0.12.0", "Write target is a directory"),
    _code("json.projection.incomplete-document@1", "Projection", "0.13.0", "Recovered JSON syntax cannot enter a complete semantic projection"),
]


def _merge(left: list[ErrorCodeDescriptor], right: list[ErrorCodeDescriptor]) -> list[ErrorCodeDescriptor]:
    """Sorted merge of two sorted descriptor lists (the Rust const merges)."""
    output: list[ErrorCodeDescriptor] = []
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i].code < right[j].code:
            output.append(left[i])
            i += 1
        else:
            output.append(right[j])
            j += 1
    output.extend(left[i:])
    output.extend(right[j:])
    return output


_ERROR_CODES_V2 = _merge(_ERROR_CODES_V1, _NEW_CODES_V2)
_ERROR_CODES_V3 = _merge(_ERROR_CODES_V2, _NEW_CODES_V3)
_ERROR_CODES_V4 = _merge(_ERROR_CODES_V3, _NEW_CODES_V4)
_ERROR_CODES_V5 = _merge(_ERROR_CODES_V4, _NEW_CODES_V5)
_ERROR_CODES_V6 = _merge(_ERROR_CODES_V5, _NEW_CODES_V6)
_ERROR_CODES_V7 = _merge(_ERROR_CODES_V6, _NEW_CODES_V7)


class ErrorCodeRegistry:
    """A closed, explicitly versioned error-code registry."""

    def __init__(self, version: int):
        if not 1 <= version <= 7:
            raise ValueError("error-code registry version must be 1..7")
        self.version = version

    def codes(self) -> list[ErrorCodeDescriptor]:
        return {
            1: _ERROR_CODES_V1,
            2: _ERROR_CODES_V2,
            3: _ERROR_CODES_V3,
            4: _ERROR_CODES_V4,
            5: _ERROR_CODES_V5,
            6: _ERROR_CODES_V6,
            7: _ERROR_CODES_V7,
        }[self.version]

    def contains(self, candidate: str) -> bool:
        return self.descriptor(candidate) is not None

    def descriptor(self, candidate: str) -> ErrorCodeDescriptor | None:
        # The lists are sorted; binary search keeps lookups cheap.
        records = self.codes()
        low, high = 0, len(records)
        while low < high:
            middle = (low + high) // 2
            if records[middle].code < candidate:
                low = middle + 1
            else:
                high = middle
        if low < len(records) and records[low].code == candidate:
            return records[low]
        return None

    def validate(self, candidate: str, path: str = "$.code") -> None:
        if not self.contains(candidate):
            raise protocol_error(
                ProtocolErrorKind.INVALID_VALUE, path, f"unregistered public code: {candidate}"
            )


def validate_versioned_code(code: str, path: str) -> None:
    """Validates one ``id@version`` code spelling (error_registry.rs:1647-1655)."""
    id_part, separator, version_text = code.rpartition("@")
    if not separator or not version_text:
        raise protocol_error(
            ProtocolErrorKind.INVALID_VALUE, path, "code lacks @version suffix"
        )
    if not version_text.isdigit() or int(version_text) == 0 or int(version_text) > 0xFFFFFFFF:
        raise protocol_error(
            ProtocolErrorKind.INVALID_VALUE, path, "code version is invalid"
        )
    from consema.protocol.contract import validate_identifier

    validate_identifier(id_part, path)


# ---------------------------------------------------------------------------
# core.error-code-registry@1 manifest
# ---------------------------------------------------------------------------

def error_code_manifest_value(version: int = 7) -> PortableValue:
    """Encodes the `core.error-code-registry@1` payload for one registry version.

    Every record is an Object with exactly code / category / introduced /
    stability ("Stable") / description in that order (error_registry.rs:1573-1594).
    """
    records = []
    for descriptor in ErrorCodeRegistry(version).codes():
        records.append(
            PortableValue.object(
                [
                    ("code", PortableValue.string(descriptor.code)),
                    ("category", PortableValue.string(descriptor.category.value)),
                    ("introduced", PortableValue.string(descriptor.introduced)),
                    ("stability", PortableValue.string("Stable")),
                    ("description", PortableValue.string(descriptor.description)),
                ]
            )
        )
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.error-code-registry@1")),
            ("error_codes", PortableValue.sequence(records)),
        ]
    )


def validate_error_code_manifest_value(value: PortableValue) -> None:
    """Strictly validates one transferable `core.error-code-registry@1` value.

    Identity, ordering, category, and stability are normative; the
    descriptions are presentation metadata (error_registry.rs:1596-1645).
    """
    fields = schema_fields(
        value,
        "core.error-code-registry@1",
        ["schema", "error_codes"],
        "$",
    )
    previous: str | None = None
    for index, item in enumerate(sequence_of(fields[1], "$.error_codes")):
        path = f"$.error_codes[{index}]"
        record = exact_fields(
            item,
            ["code", "category", "introduced", "stability", "description"],
            path,
        )
        code = string_of(record[0], f"{path}.code")
        validate_versioned_code(code, f"{path}.code")
        parse_category(string_of(record[1], f"{path}.category"), f"{path}.category")
        if not string_of(record[2], f"{path}.introduced") or not string_of(
            record[4], f"{path}.description"
        ):
            raise protocol_error(
                ProtocolErrorKind.INVALID_VALUE,
                path,
                "introduced and description must be non-empty",
            )
        if string_of(record[3], f"{path}.stability") != "Stable":
            raise protocol_error(
                ProtocolErrorKind.INVALID_VALUE,
                f"{path}.stability",
                "unknown error-code stability",
            )
        if previous is not None and previous >= code:
            raise protocol_error(
                ProtocolErrorKind.INVALID_VALUE,
                "$.error_codes",
                "error codes must be sorted and unique",
            )
        previous = code
