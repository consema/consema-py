"""HCL-specific formation, structure, recovery, and report limits (RFC
0014 §11).

The common limits bound source bytes, generic nesting, token and node
counts, and diagnostics; the flat fields bound the HCL-specific facts:
decoded text, body/expression/template depth, per-body item counts,
identifier/string/number/template/heredoc lengths, constructor extents,
and recovery/error/piece/report counts. Every limit failure is a fatal
formation failure or an atomic operation failure; a limit failure never
masquerades as an empty body, truncated expression, shortened query,
partial target, or successful edit (hard gate 4).

Authority: crates/consema-hcl/src/lib.rs:166-234 (fields) and lib.rs:236-273
(the frozen R-3 default values, transcribed verbatim).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from consema.document.limits import ParseLimits


@dataclass(frozen=True, slots=True)
class HclParseLimits:
    """Resource bounds applied during HCL formation (RFC 0014 §11;
    lib.rs:166-234)."""

    common: ParseLimits = field(default_factory=ParseLimits)
    max_decoded_utf8_bytes: int = 128 * 1024 * 1024
    max_decoded_scalars: int = 64 * 1024 * 1024
    max_body_depth: int = 128
    max_expression_depth: int = 24
    max_template_depth: int = 256
    max_attribute_count: int = 1_000_000
    max_block_count: int = 1_000_000
    max_label_count: int = 1_000_000
    max_body_item_count: int = 1_000_000
    max_identifier_len: int = 1024
    max_string_len: int = 16 * 1024 * 1024
    max_number_digits: int = 100_000
    max_template_len: int = 16 * 1024 * 1024
    max_template_interpolations: int = 1_000_000
    max_heredoc_lines: int = 1_000_000
    max_heredoc_bytes: int = 16 * 1024 * 1024
    max_tuple_elements: int = 1_000_000
    max_object_entries: int = 1_000_000
    max_for_extent: int = 1_000_000
    max_recovery_regions: int = 100_000
    max_error_regions: int = 100_000
    max_syntax_pieces: int = 2_000_000
    max_report_events: int = 100_000
