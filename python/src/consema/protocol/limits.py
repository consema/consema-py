"""Protocol transport resource limits.

Authority: https://github.com/consema/consema-rs/blob/main/consema-protocol/src/limits.rs (the frozen defaults,
limits.rs:20-31). Go (https://github.com/consema/consema-go/blob/main/go/protocol/limits.go) is a cross-reference only.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProtocolLimits:
    """Resource limits shared by the canonical JSON and PVCE/1 transports."""

    max_bytes: int = 64 * 1024 * 1024
    max_depth: int = 256
    max_nodes: int = 1_000_000
    max_container_entries: int = 1_000_000
    max_blob_bytes: int = 64 * 1024 * 1024
    max_integer_bytes: int = 1024
