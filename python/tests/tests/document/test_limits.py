"""Frozen limit defaults and resource-limit vector cases.

Golden cases transcribed from conformance/vectors/source-v1.json:
- ``source.resource.raw-limit`` (lines 155-160): raw 6162 with
  max_raw_bytes 1 -> core.source.resource-limit@1;
- ``source.resource.decoded-limit`` (lines 161-166): latin-1 e9 with
  max_decoded_utf8_bytes 1 -> core.source.resource-limit@1.

Frozen defaults:
- SourceLimits: crates/consema-document/src/source.rs:401-409 (64 MiB raw,
  128 MiB decoded UTF-8, 64 MiB decoded scalars);
- ParseLimits: crates/consema-document/src/lib.rs:629-639 (64 MiB source,
  depth 256, 2M tokens, 1M nodes, 10k diagnostics) — also pinned by
  RFC 0016 §5.1 and go/document/limits.go (cross-reference);
- MaterializationLimits: crates/consema-document/src/materialization.rs:95-105
  (1M input nodes, 64 MiB output, depth 256, 100k report, 2M provenance);
- SourcePatchLimits: crates/consema-document/src/source_patch.rs:19-27
  (100k replacements, 128 MiB patch bytes).
"""

from __future__ import annotations

import pytest

from consema.document import (
    EncodingRequest,
    MaterializationLimits,
    ParseLimits,
    SourceEncoding,
    SourceError,
    SourceLimits,
    SourcePatchLimits,
    SourceSnapshot,
)

MIB = 1024 * 1024


def test_parse_limits_defaults() -> None:
    limits = ParseLimits()
    assert limits.max_source_bytes == 64 * MIB
    assert limits.max_nesting_depth == 256
    assert limits.max_token_count == 2_000_000
    assert limits.max_node_count == 1_000_000
    assert limits.max_diagnostics == 10_000


def test_source_limits_defaults() -> None:
    limits = SourceLimits()
    assert limits.max_raw_bytes == 64 * MIB
    assert limits.max_decoded_utf8_bytes == 128 * MIB
    assert limits.max_decoded_scalars == 64 * MIB


def test_materialization_limits_defaults() -> None:
    limits = MaterializationLimits()
    assert limits.max_input_nodes == 1_000_000
    assert limits.max_output_bytes == 64 * MIB
    assert limits.max_depth == 256
    assert limits.max_report_entries == 100_000
    assert limits.max_provenance_entries == 2_000_000


def test_source_patch_limits_defaults() -> None:
    limits = SourcePatchLimits()
    assert limits.source == SourceLimits()
    assert limits.max_replacements == 100_000
    assert limits.max_patch_bytes == 128 * MIB


def test_resource_raw_limit() -> None:
    """Vector case source.resource.raw-limit."""
    with pytest.raises(SourceError) as caught:
        SourceSnapshot.from_raw(
            bytes.fromhex("6162"),
            EncodingRequest.new(SourceEncoding.utf8()),
            SourceLimits(max_raw_bytes=1),
        )
    assert caught.value.code == "core.source.resource-limit@1"
    assert caught.value.name == "raw-bytes"
    assert caught.value.observed == 2
    assert caught.value.limit == 1


def test_resource_decoded_limit() -> None:
    """Vector case source.resource.decoded-limit: latin-1 0xe9 decodes to two
    UTF-8 bytes, exceeding max_decoded_utf8_bytes 1."""
    with pytest.raises(SourceError) as caught:
        SourceSnapshot.from_raw(
            bytes.fromhex("e9"),
            EncodingRequest.new(SourceEncoding.latin1()),
            SourceLimits(max_decoded_utf8_bytes=1),
        )
    assert caught.value.code == "core.source.resource-limit@1"
    assert caught.value.name == "decoded-utf8-bytes"
