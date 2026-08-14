"""Parser grammar checks transcribed from the ad-hoc grammar verifier
(formerly tests/yaml/_verify_grammar.py, deleted after transcription).

The %TAG directive test is the only %TAG coverage in the Python suite
(backend.rs:184-213); the remaining tests pin parser paths the vector
suite does not cover directly: multiline plain scalars, compact mappings,
nested collections, same-line marker content, explicit keys, quoted
escapes, block scalar folding/chomping/indent indicators, anchors before
block collections, the merge key as an ordinary association (RFC 0007 s5),
bounded deep nesting, real-world-shaped documents, and the 1.1 binary /
timestamp value projections (formerly tests/yaml/_verify_extended.py).

Authority: https://github.com/consema/consema-rs/blob/main/consema-yaml backend behavior; every assertion is a
byte-exact or decoded-exact pin of the Python parser.
"""

from __future__ import annotations

from consema.yaml import (
    YamlProfile,
    project_graph,
    project_value,
)
from consema.yaml.projection import ValueProjectionRequest
from tests.yaml.conftest import parse_source


def _projected_root(source: str):
    """Best-exact value projection of one scalar/mapping root."""
    document = parse_source(source, YamlProfile.YAML12_CORE_V1)
    result = project_value(document, ValueProjectionRequest.best_exact_v1())
    assert not hasattr(result, "code"), result.code
    return result.value


def test_tag_directive_resolution():
    # backend.rs:184-213: a %TAG directive prefix resolves the tagged
    # scalar to the full handle and keeps the alias edge in the same
    # stream as a second document.
    document = parse_source(
        "%TAG !e! tag:example.com,2026:\n"
        "---\nroot: &node !e!thing [one, *node]\n"
        "---\nsecond: |\n  text\n",
        YamlProfile.YAML12_CORE_V1,
    )
    assert document.document_count() == 2
    value = document.document(0).root().mapping_entry(0).value()
    assert value.tag() == "tag:example.com,2026:thing"
    assert document.alias_count() == 1


def test_plain_scalar_continuation():
    # A more-indented plain continuation folds into the decoded scalar.
    document = parse_source("key: text\n  more\n", YamlProfile.YAML12_CORE_V1)
    scalar = document.document(0).root().mapping_entry(0).value().scalar()
    assert scalar.decoded() == "text more"


def test_nested_plain_value_on_next_line():
    # The value starts on the following line and its continuation is
    # more-indented than the key.
    document = parse_source("key:\n  text\n    more\n", YamlProfile.YAML12_CORE_V1)
    scalar = document.document(0).root().mapping_entry(0).value().scalar()
    assert scalar.decoded() == "text more"


def test_compact_mapping_in_sequence():
    # Flow-style compact mappings are valid sequence items.
    document = parse_source("- a: 1\n  b: 2\n- c: 3\n", YamlProfile.YAML12_CORE_V1)
    root = document.document(0).root()
    assert root.sequence_len() == 2
    assert root.sequence_item(0).node().mapping_len() == 2
    entry = root.sequence_item(1).node().mapping_entry(0)
    assert entry.key().scalar().decoded() == "c"


def test_same_line_marker_content():
    # Document-start marker with same-line content.
    document = parse_source("--- {k: v}\n", YamlProfile.YAML12_CORE_V1)
    root = document.document(0).root()
    assert root.mapping_len() == 1
    assert root.mapping_entry(0).key().scalar().decoded() == "k"


def test_nested_block_collections():
    # Nested block sequences and mappings with deeper block values.
    source = "seq:\n  - one\n  - two\nmap:\n  a: 1\n  b:\n    - x\n    - y\n"
    document = parse_source(source, YamlProfile.YAML12_CORE_V1)
    root = document.document(0).root()
    seq = root.mapping_entry(0).value()
    nested = root.mapping_entry(1).value()
    assert seq.sequence_len() == 2
    assert nested.mapping_len() == 2
    assert nested.mapping_entry(1).value().sequence_len() == 2


def test_explicit_keys_on_their_own_lines():
    # Explicit-key syntax: a flow sequence key and a scalar key.
    document = parse_source("? [a, b]\n: one\n? c\n: two\n", YamlProfile.YAML12_CORE_V1)
    root = document.document(0).root()
    assert root.mapping_len() == 2
    assert root.mapping_entry(0).key().kind().value == "Sequence"
    assert root.mapping_entry(1).key().scalar().decoded() == "c"


def test_compose_shaped_document_round_trips():
    # A compose-style real-world document forms completely and renders
    # byte-exactly.
    source = (
        'version: "3"\n'
        "services:\n"
        "  web:\n"
        "    image: nginx:1.25\n"
        "    ports:\n"
        '      - "8080:80"\n'
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
    document = parse_source(source, YamlProfile.YAML12_CORE_V1)
    root = document.document(0).root()
    services = root.mapping_entry(1).value()
    web = services.mapping_entry(0).value()
    assert root.mapping_len() == 3
    assert services.mapping_len() == 2
    assert web.mapping_entry(1).value().sequence_len() == 1
    assert web.mapping_entry(2).value().sequence_len() == 2
    assert document.render() == source.encode("utf-8")


def test_kubernetes_shaped_document_with_block_scalars():
    # A k8s-shaped document with anchors, aliases, and a literal block
    # scalar renders byte-exactly and projects a graph.
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
    document = parse_source(source, YamlProfile.YAML12_CORE_V1)
    spec = document.document(0).root().mapping_entry(3).value()
    template = spec.mapping_entry(1).value()
    containers = template.mapping_entry(0).value().mapping_entry(0).value()
    assert document.alias_count() == 1
    assert containers.sequence_len() == 1
    assert document.render() == source.encode("utf-8")
    graph = project_graph(document)
    assert graph.node_count() > 10


def test_double_quoted_escapes():
    # Double-quoted escapes decode exactly (\n, \t, \u0041).
    document = parse_source('a: "line1\\nline2\\t\\u0041"\n', YamlProfile.YAML12_CORE_V1)
    scalar = document.document(0).root().mapping_entry(0).value().scalar()
    assert scalar.decoded() == "line1\nline2\tA"


def test_single_quoted_escaping():
    # Single-quoted escaping doubles the quote character.
    document = parse_source("a: 'it''s'\n", YamlProfile.YAML12_CORE_V1)
    scalar = document.document(0).root().mapping_entry(0).value().scalar()
    assert scalar.decoded() == "it's"


def test_folded_scalar_folding():
    # Folded scalars join lines with spaces; a blank line introduces a
    # newline (lib.rs folded decoding).
    document = parse_source("a: >\n  one\n  two\n\n  three\n", YamlProfile.YAML12_CORE_V1)
    scalar = document.document(0).root().mapping_entry(0).value().scalar()
    assert scalar.decoded() == "one two\nthree\n"


def test_block_scalar_chomping_variants():
    # Literal keep (|+) retains all trailing newlines; literal strip (|-)
    # removes them.
    document = parse_source("a: |+\n  x\n\n\nb: 1\n", YamlProfile.YAML12_CORE_V1)
    scalar = document.document(0).root().mapping_entry(0).value().scalar()
    assert scalar.decoded() == "x\n\n\n"
    document = parse_source("a: |-\n  x\n\n\nb: 1\n", YamlProfile.YAML12_CORE_V1)
    scalar = document.document(0).root().mapping_entry(0).value().scalar()
    assert scalar.decoded() == "x"


def test_indentation_indicator():
    # The explicit |2 indentation indicator changes the content indent.
    document = parse_source("a: |2\n   x\nb: 1\n", YamlProfile.YAML12_CORE_V1)
    scalar = document.document(0).root().mapping_entry(0).value().scalar()
    assert scalar.decoded() == " x\n"


def test_anchor_before_block_collection():
    # An anchored block mapping (anchor before a block collection) is a
    # single node shared by the alias.
    source = "defaults: &defaults\n  retries: 3\n  timeout: 10\nuse: *defaults\n"
    document = parse_source(source, YamlProfile.YAML12_CORE_V1)
    anchored = document.document(0).root().mapping_entry(0).value()
    assert document.alias_count() == 1
    assert anchored.anchor() == "defaults"
    assert document.alias(0).target().node_ref() == anchored.node_ref()


def test_merge_key_is_an_ordinary_association():
    # RFC 0007 s5: no merge execution in this version; ``<<`` is an
    # ordinary scalar key, so the default value projection completes.
    document = parse_source("copy:\n  <<: {a: 1}\n  b: 2\n", YamlProfile.YAML12_CORE_V1)
    projected = project_value(document, ValueProjectionRequest.best_exact_v1())
    assert not hasattr(projected, "code")
    root = projected.value.as_object()
    nested = root[0][1].as_object()
    assert root[0][0] == "copy"
    assert nested[0][0] == "<<"
    assert len(nested) == 2


def test_deep_but_bounded_nesting_forms():
    # One hundred nested flow collections form a single document (the
    # nesting-depth limit is a separate fatal resource; see
    # test_nesting_depth_limit_is_fatal in test_formation.py).
    source = "[" * 100 + "x" + "]" * 100 + "\n"
    document = parse_source(source, YamlProfile.YAML12_CORE_V1)
    assert document.document_count() == 1


def test_tree_projection_of_full_value_tree():
    # A full scalar-kind tree projects with an exact decimal element.
    document = parse_source("a: [1, 2.5, true, null, 's', {k: v}]\n", YamlProfile.YAML12_CORE_V1)
    projected = project_value(document, ValueProjectionRequest.best_exact_v1())
    assert not hasattr(projected, "code")
    sequence = projected.value.as_object()[0][1].as_sequence()
    assert sequence[1].as_decimal() is not None


def test_yaml11_binary_and_timestamp_projection():
    # The 1.1 profile resolves !!binary and !!timestamp explicitly
    # (formerly tests/yaml/_verify_extended.py; no other Python test
    # covers these two value kinds).
    source = "bytes: !!binary SGVsbG8=\ntime: !!timestamp 2001-12-15T02:59:43Z\n"
    document = parse_source(source, YamlProfile.YAML11_COMPAT_V1)
    projected = project_value(document, ValueProjectionRequest.best_exact_v1())
    assert not hasattr(projected, "code")
    root = projected.value.as_object()
    assert root[0][1].as_bytes() == b"Hello"
    assert root[1][1].as_offset_date_time() is not None
