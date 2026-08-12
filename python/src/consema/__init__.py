"""consema — language-neutral Consema contracts (Python implementation).

L0 milestone: closed fifteen-kind PortableValue model, strict equality and
deterministic hashing, the PVCE/1 byte codec (consema.core), PortableGraph
and the PGCE/1 byte codec (consema.graph), and the protocol surface —
contract/error registries, canonical tagged JSON transport, Diagnostic,
registry descriptors, query definition validation, CLI machine records,
and CLI exit classification (consema.protocol).

L1-L4 milestones: the eight format families (json, toml, yaml, ini,
properties, xml, plist, hcl) with parse/query/projection/materialization/
edit surfaces, and the root facade — the additive registry enumeration
(consema.registry), the opaque Document union, the single facade parse
entry (``consema.registry.parse_document``), the audited
projection-to-materialization conversion composition
(``consema.convert``), the conformance runner over the shared
language-neutral vectors (``consema.conformance``), and the capability
parity assertion (``consema.capability_parity``).

Authority: RFC 0001-0016, conformance/vectors, and the Rust crates as
the byte/registry arbitration sources. This package is an independent,
Python-idiomatic implementation with zero third-party runtime dependencies.
"""

__version__ = "0.14.0"

from consema import convert, registry  # noqa: F401
from consema.convert import (  # noqa: F401
    CompleteConversion,
    ConversionFailure,
    ConversionFailureKind,
    ConversionFidelity,
    ConversionMaterializationProvenance,
    ConversionMaterializationReport,
    ConversionProjectionProvenance,
    ConversionProjectionReport,
    ConversionReport,
    convert_hcl,
    convert_ini,
    convert_json,
    convert_plist,
    convert_properties,
    convert_toml,
    convert_xml,
    convert_yaml,
)
from consema.registry import (  # noqa: F401
    Document,
    FormatMismatch,
    FormatProfile,
    OperationArgumentDescriptor,
    OperationDescriptor,
    OperationRegistry,
    ProfileError,
    format_families,
    operation_registry,
    parse_document,
    profiles,
    query_domains,
)

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
    "Document",
    "FormatMismatch",
    "FormatProfile",
    "OperationArgumentDescriptor",
    "OperationDescriptor",
    "OperationRegistry",
    "ProfileError",
    "convert_hcl",
    "convert_ini",
    "convert_json",
    "convert_plist",
    "convert_properties",
    "convert_toml",
    "convert_xml",
    "convert_yaml",
    "format_families",
    "operation_registry",
    "parse_document",
    "profiles",
    "query_domains",
]
