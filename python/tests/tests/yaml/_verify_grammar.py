"""Ad-hoc grammar robustness verification (not a gate): remaining parser
paths — %TAG directives, multiline plain scalars, compact mappings, nested
collections, same-line marker content, and a real-world-shaped document."""

from __future__ import annotations

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
    from consema.yaml import YamlProfile, parse, project_graph, project_value
    from consema.yaml.projection import ValueProjectionRequest

    LIMITS = ParseLimits()

    def ps(source: str, profile=YamlProfile.YAML12_CORE_V1):
        return parse(source.encode("utf-8"), profile, LIMITS)

    # %TAG directive resolution (backend.rs:184-213)
    doc = ps("%TAG !e! tag:example.com,2026:\n---\nroot: &node !e!thing [one, *node]\n---\nsecond: |\n  text\n")
    check("tag directive", doc.document_count() == 2
          and doc.document(0).root().mapping_entry(0).value().tag() == "tag:example.com,2026:thing"
          and doc.alias_count() == 1)

    # Multiline plain scalar continuation
    doc = ps("key: text\n  more\n")
    scalar = doc.document(0).root().mapping_entry(0).value().scalar()
    check("plain continuation", scalar.decoded() == "text more", repr(scalar.decoded()))

    # Nested value on the next line
    doc = ps("key:\n  text\n    more\n")
    scalar = doc.document(0).root().mapping_entry(0).value().scalar()
    check("nested plain", scalar.decoded() == "text more", repr(scalar.decoded()))

    # Compact mapping in a sequence
    doc = ps("- a: 1\n  b: 2\n- c: 3\n")
    root = doc.document(0).root()
    check("compact mapping", root.sequence_len() == 2
          and root.sequence_item(0).node().mapping_len() == 2
          and root.sequence_item(1).node().mapping_entry(0).key().scalar().decoded() == "c")

    # Same-line content after the marker
    doc = ps("--- {k: v}\n")
    root = doc.document(0).root()
    check("same-line marker content", root.mapping_len() == 1
          and root.mapping_entry(0).key().scalar().decoded() == "k")

    # Nested block collections
    doc = ps("seq:\n  - one\n  - two\nmap:\n  a: 1\n  b:\n    - x\n    - y\n")
    root = doc.document(0).root()
    seq = root.mapping_entry(0).value()
    nested = root.mapping_entry(1).value()
    check("nested collections", seq.sequence_len() == 2
          and nested.mapping_len() == 2
          and nested.mapping_entry(1).value().sequence_len() == 2)

    # Explicit keys on their own lines
    doc = ps("? [a, b]\n: one\n? c\n: two\n")
    root = doc.document(0).root()
    check("explicit keys", root.mapping_len() == 2
          and root.mapping_entry(0).key().kind().value == "Sequence"
          and root.mapping_entry(1).key().scalar().decoded() == "c")

    # Real-world-shaped document (compose-style)
    source = (
        "version: \"3\"\n"
        "services:\n"
        "  web:\n"
        "    image: nginx:1.25\n"
        "    ports:\n"
        "      - \"8080:80\"\n"
        "    environment:\n"
        "      - DEBUG=true\n"
        "      - NAME=web\n"
        "    volumes:\n"
        "      - ./html:/usr/share/nginx/html:ro\n"
        "  db:\n"
        "    image: postgres:16\n"
        "    environment:\n"
        "      POSTGRES_PASSWORD: secret\n"
        "      POSTGRES_DB: app\n"
        "networks:\n"
        "  default:\n"
        "    driver: bridge\n"
    )
    doc = ps(source)
    root = doc.document(0).root()
    services = root.mapping_entry(1).value()
    web = services.mapping_entry(0).value()
    check("compose-shaped", root.mapping_len() == 3
          and services.mapping_len() == 2
          and web.mapping_entry(1).value().sequence_len() == 1
          and web.mapping_entry(2).value().sequence_len() == 2
          and doc.render() == source.encode("utf-8"))

    # Kubernetes-shaped document with block scalars and anchors
    source = (
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n"
        "  name: &name web\n"
        "  labels:\n"
        "    app: web\n"
        "spec:\n"
        "  replicas: 3\n"
        "  template:\n"
        "    spec:\n"
        "      containers:\n"
        "        - name: *name\n"
        "          image: registry.example.com/web:1.2.3\n"
        "          command:\n"
        "            - /bin/sh\n"
        "            - -c\n"
        "            - |-\n"
        "              echo starting\n"
        "              exec web serve\n"
    )
    doc = ps(source)
    spec = doc.document(0).root().mapping_entry(3).value()
    template = spec.mapping_entry(1).value()
    containers = template.mapping_entry(0).value().mapping_entry(0).value()
    check("k8s-shaped", doc.alias_count() == 1
          and containers.sequence_len() == 1
          and doc.render() == source.encode("utf-8"))

    # Graph projection of the k8s-shaped document
    graph = project_graph(doc)
    check("k8s graph", graph.node_count() > 10)

    # Double-quoted escapes
    doc = ps('a: "line1\\nline2\\t\\u0041"\n')
    scalar = doc.document(0).root().mapping_entry(0).value().scalar()
    check("double escapes", scalar.decoded() == "line1\nline2\tA", repr(scalar.decoded()))

    # Single-quoted escaping
    doc = ps("a: 'it''s'\n")
    scalar = doc.document(0).root().mapping_entry(0).value().scalar()
    check("single quotes", scalar.decoded() == "it's", repr(scalar.decoded()))

    # Folded scalar folding
    doc = ps("a: >\n  one\n  two\n\n  three\n")
    scalar = doc.document(0).root().mapping_entry(0).value().scalar()
    check("folded folding", scalar.decoded() == "one two\nthree\n", repr(scalar.decoded()))

    # Block scalar chomping variants
    doc = ps("a: |+\n  x\n\n\nb: 1\n")
    scalar = doc.document(0).root().mapping_entry(0).value().scalar()
    check("literal keep", scalar.decoded() == "x\n\n\n", repr(scalar.decoded()))
    doc = ps("a: |-\n  x\n\n\nb: 1\n")
    scalar = doc.document(0).root().mapping_entry(0).value().scalar()
    check("literal strip", scalar.decoded() == "x", repr(scalar.decoded()))

    # Indentation indicator
    doc = ps("a: |2\n   x\nb: 1\n")
    scalar = doc.document(0).root().mapping_entry(0).value().scalar()
    check("indent indicator", scalar.decoded() == " x\n", repr(scalar.decoded()))

    # Anchored block mapping (the anchor-before-block-collection pattern)
    doc = ps("defaults: &defaults\n  retries: 3\n  timeout: 10\nuse: *defaults\n")
    check("anchor before block", doc.alias_count() == 1
          and doc.document(0).root().mapping_entry(0).value().anchor() == "defaults"
          and doc.alias(0).target().node_ref()
          == doc.document(0).root().mapping_entry(0).value().node_ref())

    # Merge key is an ordinary scalar/mapping association (RFC 0007 s5:
    # no merge execution in this version). No alias is involved, so the
    # default value projection completes.
    doc = ps("copy:\n  <<: {a: 1}\n  b: 2\n")
    projected = project_value(doc, ValueProjectionRequest.best_exact_v1())
    root = projected.value.as_object()
    nested = root[0][1].as_object()
    check("merge key ordinary", root[0][0] == "copy" and nested[0][0] == "<<"
          and len(nested) == 2)

    # Deep but bounded nesting
    source = "[" * 100 + "x" + "]" * 100 + "\n"
    doc = ps(source)
    check("deep nesting", doc.document_count() == 1)

    # Value projection of a full tree
    doc = ps("a: [1, 2.5, true, null, 's', {k: v}]\n")
    projected = project_value(doc, ValueProjectionRequest.best_exact_v1())
    check("tree projection", projected.value.as_object()[0][1].as_sequence()[1].as_decimal()
          is not None)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURES: {FAILURES}")
        return 1
    print("ALL GRAMMAR CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
