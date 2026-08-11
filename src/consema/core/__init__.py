"""consema.core — the closed fifteen-kind PortableValue model.

Exports the value model (Kind, Decimal, PortableValue, ExtendedValue,
ObjectBuilder, EntryMappingBuilder), strict equality and the deterministic
hash, the PVCE/1 byte codec with its limits, and the typed
`core.pvce.*@1` errors.

Authority: RFC 0016 §4.1/§4.2; crates/consema-pvce/src/lib.rs (byte
arbitration); conformance/vectors/v1.json (golden bytes); go/core as a
cross-reference only.
"""

from consema.core.errors import DuplicateKeyError, PVCEError, PVCEErrorKind
from consema.core.equal import equal, hash_value
from consema.core.pvce import (
    MAGIC as PVCE_MAGIC,
    VERSION as PVCE_VERSION,
    DecodeLimits,
    EncodeLimits,
    decode,
    decode_value,
    encode,
    encode_bounded,
    encode_value,
)
from consema.core.value import (
    Decimal,
    EntryMappingBuilder,
    ExtendedValue,
    Kind,
    ObjectBuilder,
    PortableValue,
    decimal,
)

__all__ = [
    "Decimal",
    "DecodeLimits",
    "DuplicateKeyError",
    "EncodeLimits",
    "EntryMappingBuilder",
    "ExtendedValue",
    "Kind",
    "ObjectBuilder",
    "PVCE_MAGIC",
    "PVCE_VERSION",
    "PVCEError",
    "PVCEErrorKind",
    "PortableValue",
    "decimal",
    "decode",
    "decode_value",
    "encode",
    "encode_bounded",
    "encode_value",
    "equal",
    "hash_value",
]
