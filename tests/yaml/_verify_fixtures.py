"""Ad-hoc fixture verification (not a gate): byte-exact round trips of the
real-project fixtures under yaml.1.2-core@1, plus graph PGCE round trips
(the go/yaml fixture_test.go surface: kubernetes-workload.yaml,
github-actions-ci.yaml, compose-services.yaml, anchor-heavy.yaml)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, "C:/Users/franck/Documents/consema/python/src")

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"PASS {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name} {detail}")


def main() -> int:
    from consema.document.limits import ParseLimits
    from consema.graph import decode_pgce, encode_pgce
    from consema.yaml import YamlProfile, parse, project_graph, project_value
    from consema.yaml.projection import ValueProjectionRequest

    fixture_dir = "C:/Users/franck/Documents/consema/conformance/fixtures/yaml"
    names = [
        "kubernetes-workload.yaml",
        "github-actions-ci.yaml",
        "compose-services.yaml",
        "anchor-heavy.yaml",
    ]
    for name in names:
        path = os.path.join(fixture_dir, name)
        with open(path, "rb") as handle:
            raw = handle.read()
        document = parse(raw, YamlProfile.YAML12_CORE_V1, ParseLimits())
        check(f"{name} byte-exact", document.render() == raw)
        check(f"{name} complete", document.formation_status().value == "Complete")
        check(f"{name} lossless coverage",
              sum(piece.span.len() for piece in document.lossless_structural_index().pieces)
              == len(raw))
        graph = project_graph(document)
        encoded = encode_pgce(graph)
        decoded = decode_pgce(encoded, _pgce_limits())
        check(f"{name} pgce round trip", decoded == graph)

    # The anchor-heavy fixture must reject implicit sharing and complete
    # under explicit acyclic duplication (fixture_test.go:147-166).
    with open(os.path.join(fixture_dir, "anchor-heavy.yaml"), "rb") as handle:
        raw = handle.read()
    document = parse(raw, YamlProfile.YAML12_CORE_V1, ParseLimits())
    default = project_value(document, ValueProjectionRequest.best_exact_v1())
    check("anchor-heavy rejects sharing", default.code == "yaml.projection.sharing@1",
          getattr(default, "code", "completed"))
    from consema.yaml import SharingPolicy
    duplicated = project_value(
        document,
        ValueProjectionRequest.best_exact_v1().with_sharing(SharingPolicy.DUPLICATE_ACYCLIC),
    )
    check("anchor-heavy duplicated", not hasattr(duplicated, "code")
          and duplicated.fidelity.value == "Transformed")

    # The tree-shaped fixtures close through PortableValue.
    for name in ("github-actions-ci.yaml", "compose-services.yaml"):
        with open(os.path.join(fixture_dir, name), "rb") as handle:
            raw = handle.read()
        document = parse(raw, YamlProfile.YAML12_CORE_V1, ParseLimits())
        if document.document_count() != 1:
            check(f"{name} single document", False)
            continue
        projected = project_value(document, ValueProjectionRequest.best_exact_v1())
        check(f"{name} value closure", not hasattr(projected, "code"))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURES: {FAILURES}")
        return 1
    print("ALL FIXTURE CHECKS PASSED")
    return 0


def _pgce_limits():
    from consema.graph import PgceLimits

    return PgceLimits()


if __name__ == "__main__":
    sys.exit(main())
