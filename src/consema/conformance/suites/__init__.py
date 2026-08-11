"""Per-suite capability dispatch modules of the conformance runner.

Importing this package registers every frozen suite definition with the
runner's inventory (one module per vector suite, mirroring
crates/consema-conformance/src/lib.rs:3-25 and go/conformance). The import
order registers the suites in fc-manifest inventory order.
"""

from __future__ import annotations

from consema.conformance.suites import v1  # noqa: F401
from consema.conformance.suites import toml_v1  # noqa: F401
from consema.conformance.suites import protocol_v1  # noqa: F401
from consema.conformance.suites import source_v1  # noqa: F401
from consema.conformance.suites import syntax_query_v1  # noqa: F401
from consema.conformance.suites import protocol_v2  # noqa: F401
from consema.conformance.suites import operations_v1  # noqa: F401
from consema.conformance.suites import json_family_v2  # noqa: F401
from consema.conformance.suites import portable_graph_v1  # noqa: F401
from consema.conformance.suites import semantic_model_v5  # noqa: F401
from consema.conformance.suites import yaml_v1  # noqa: F401
from consema.conformance.suites import semantic_model_v6  # noqa: F401
from consema.conformance.suites import ini_v1  # noqa: F401
from consema.conformance.suites import java_properties_v1  # noqa: F401
from consema.conformance.suites import xml_1_0_safe_v1  # noqa: F401
from consema.conformance.suites import plist_v1  # noqa: F401
from consema.conformance.suites import hcl_v1  # noqa: F401
from consema.conformance.suites import cli_v1  # noqa: F401
