"""consema.differential — cross-language differential verification against
the Rust authority (RFC 0016 §1.1; docs/five-language-ci-design.md §3).

The Python side of the differential harnesses that the Go implementation
already runs (go/conformance/differential/): byte parity of the PVCE/PGCE
encoders, normalized-result differential (bidirectional), and protocol
exchange. The Rust examples in crates/consema-conformance emit the golden
bytes/evidence files; this package computes the Python side and compares.
Orchestration: scripts/python-verify-*.ps1.
"""
