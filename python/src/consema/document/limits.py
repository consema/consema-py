"""Parse resource limits; exceeding one is a fatal formation failure.

Authority:
- https://github.com/consema/consema-rs/blob/main/consema-document/src/lib.rs — the exact fields and frozen
  defaults (64 MiB source, depth 256, 2M tokens, 1M nodes, 10k diagnostics).
  ``max_number_digits`` (100_000, coefficient plus exponent digits of one
  number literal) is the same-wave cross-language magnitude bound for the
  json/toml/yaml number paths; HCL carries the same default in its own
  HclParseLimits.
- RFC 0016 §5.1 (https://github.com/consema/consema/blob/main/docs/rfcs/0016-go-api-mapping-v1.md) — ParseLimits
  (and per-family limits) mirror the Rust defaults; exceeding a limit is a
  fatal formation failure carrying the frozen limit code.
- https://github.com/consema/consema-rs/blob/main/consema-protocol/src/error_registry.rs — the fatal formation
  resource-limit code core.parse.resource-limit@1 used at the protocol layer
  (RFC 0015 §5.2 classification applies at the protocol layer, not in the
  SDK; per RFC 0016 §6 the SDK never classifies).

https://github.com/consema/consema-go/blob/main/go/document/limits.go is a cross-reference only (same frozen defaults).
"""

from __future__ import annotations

from dataclasses import dataclass

# Frozen defaults, https://github.com/consema/consema-rs/blob/main/consema-document/src/lib.rs
_DEFAULT_MAX_SOURCE_BYTES = 64 * 1024 * 1024
_DEFAULT_MAX_NESTING_DEPTH = 256
_DEFAULT_MAX_TOKEN_COUNT = 2_000_000
_DEFAULT_MAX_NODE_COUNT = 1_000_000
_DEFAULT_MAX_DIAGNOSTICS = 10_000
# Same-wave cross-language default for the per-number digit magnitude bound
# (coefficient plus exponent; the reference family fix lands in the same
# wave, HCL precedent HclParseLimits.max_number_digits = 100_000).
_DEFAULT_MAX_NUMBER_DIGITS = 100_000


@dataclass(frozen=True, slots=True)
class ParseLimits:
    """Parse resource limits (https://github.com/consema/consema-rs/blob/main/consema-document/src/lib.rs).

    ``max_token_count`` bounds tokens plus trivia/error regions (the source
    amplification control: tokens and nodes are bounded separately from raw
    bytes); ``max_node_count`` bounds format syntax nodes. Exceeding any
    limit is a fatal formation failure; there is no truncation-then-success
    (RFC 0016 §6, "no truncation-then-success", SECURITY.md).
    """

    max_source_bytes: int = _DEFAULT_MAX_SOURCE_BYTES
    max_nesting_depth: int = _DEFAULT_MAX_NESTING_DEPTH
    max_token_count: int = _DEFAULT_MAX_TOKEN_COUNT
    max_node_count: int = _DEFAULT_MAX_NODE_COUNT
    max_diagnostics: int = _DEFAULT_MAX_DIAGNOSTICS
    max_number_digits: int = _DEFAULT_MAX_NUMBER_DIGITS
