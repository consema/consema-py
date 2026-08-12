"""Root facade registry surface tests (crates/consema/src/lib.rs registry
module; go/registry.go cross-reference).

Covers the additive enumeration (8 families / 16 profiles / 21 query
domains), the per-profile operation registries (16/16 resolvable), the
single facade parse entry over the opaque Document union, and the
drift guards: the enumerated ids must equal the backend facts.

Runs under pytest or directly (python tests/test_registry.py).
"""

from __future__ import annotations

import sys

from consema.document.ids import FormatFamilyId, ProfileId
from consema.registry import (
    Document,
    ProfileError,
    format_families,
    operation_registry,
    parse_document,
    profiles,
    query_domains,
)


def test_eight_families_sorted():
    families = format_families()
    assert len(families) == 8
    ids = [family.id for family in families]
    assert ids == sorted(ids)
    assert ids == ["hcl", "ini", "java-properties", "json", "plist", "toml", "xml", "yaml"]
    assert all(family.version == 1 for family in families)


def test_sixteen_profiles_sorted_inventory():
    entries = profiles()
    assert len(entries) == 16
    ids = [entry.profile_id().id for entry in entries]
    assert ids == sorted(ids)
    assert ids == [
        "hcl.native",
        "hcl.tfvars",
        "ini.portable",
        "ini.python-configparser",
        "ini.windows",
        "java-properties.latin1",
        "java-properties.reader",
        "json.strict",
        "json5.standard",
        "jsonc.bounded",
        "plist.binary",
        "plist.xml",
        "toml.1.0",
        "xml.1.0-safe",
        "yaml.1.1-compat",
        "yaml.1.2-core",
    ]
    for entry in entries:
        assert entry.family_id().version == 1
        assert entry.profile_id().version == 1


def test_twenty_one_query_domains_sorted_unique():
    domains = query_domains()
    assert len(domains) == 21
    pairs = [(domain.id, domain.version) for domain in domains]
    assert pairs == sorted(pairs)
    assert len(set(pairs)) == len(pairs)
    assert ("core.portable-value-query", 1) in pairs
    assert ("hcl.native-semantic-query", 1) in pairs
    assert ("plist.binary-structure-query", 1) in pairs


def test_every_profile_resolves_an_operation_registry():
    for entry in profiles():
        registry = operation_registry(entry.profile_id())
        assert registry is not None, entry.profile_id()
        assert registry.profile_id() == entry.profile_id()
        assert registry.operations()


def test_unknown_profile_has_no_registry():
    assert operation_registry(ProfileId.new("example.unknown", 1)) is None


def test_parse_document_round_trips_every_profile():
    cases = [
        ("ini.portable", b"[section]\nvalue=1\n"),
        ("ini.windows", b"[section]\nvalue=1\r\n"),
        ("ini.python-configparser", b"[section]\nvalue=1\n"),
        ("java-properties.reader", b"name=api\n"),
        ("java-properties.latin1", b"name=api\n"),
        ("json.strict", b'{"a":1}'),
        ("jsonc.bounded", b'{"a":1,}'),
        ("json5.standard", b"{a:1,}"),
        ("toml.1.0", b"value = 1\n"),
        ("yaml.1.2-core", b"value: 1\n"),
        ("yaml.1.1-compat", b"value: 1\n"),
        ("xml.1.0-safe", b"<service><name>catalog</name></service>"),
        ("plist.xml", b'<plist version="1.0"><string>x</string></plist>'),
        ("hcl.native", b"a = 1\n"),
        ("hcl.tfvars", b"a = 1\n"),
    ]
    for profile_id, source in cases:
        document = parse_document(source, ProfileId.new(profile_id, 1))
        assert document.profile().id == profile_id
        assert document.render() == source
        assert document.format_family().version == 1


def test_parse_document_unknown_profile_raises_typed_error():
    try:
        parse_document(b"x", ProfileId.new("example.unknown", 1))
    except ProfileError as error:
        assert error.code() == "core.source.encoding-conflict@1"
    else:
        raise AssertionError("unknown profile must raise ProfileError")


def test_document_typed_adapters_dispatch_by_family():
    json_document = parse_document(b'{"a":1}', ProfileId.new("json.strict", 1))
    assert json_document.as_json() is not None
    assert json_document.as_toml() is None
    assert json_document.as_ini() is None

    toml_document = parse_document(b"value = 1\n", ProfileId.new("toml.1.0", 1))
    assert toml_document.as_toml() is not None
    assert toml_document.as_json() is None

    ini_document = parse_document(b"[s]\nk=1\n", ProfileId.new("ini.portable", 1))
    assert ini_document.as_ini() is not None
    assert ini_document.as_properties() is None


def test_family_ids_match_parsed_backend_documents():
    # Drift guard: the enumerated family ids must equal the parsed
    # documents' format_family() facts.
    cases = [
        ("hcl", "hcl.native", b"a = 1\n"),
        ("ini", "ini.portable", b"value=1\n"),
        ("java-properties", "java-properties.reader", b"name=api\n"),
        ("json", "json.strict", b"{}"),
        ("plist", "plist.xml", b'<plist version="1.0"><string>x</string></plist>'),
        ("toml", "toml.1.0", b"value = 1\n"),
        ("xml", "xml.1.0-safe", b"<a/>"),
        ("yaml", "yaml.1.2-core", b"value: 1\n"),
    ]
    for family_id, profile_id, source in cases:
        document = parse_document(source, ProfileId.new(profile_id, 1))
        assert document.format_family().id == family_id


def test_document_union_is_opaque_and_snapshot_bound():
    first = parse_document(b'{"a":1}', ProfileId.new("json.strict", 1))
    second = parse_document(b'{"a":1}', ProfileId.new("json.strict", 1))
    assert first.snapshot_identity() != second.snapshot_identity()
    assert first.diagnostics() == ()
    assert first.formation_status().value == "Complete"


def main() -> None:
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn()
            print(f"PASS {name}")
        except Exception as error:  # noqa: BLE001
            failures += 1
            print(f"FAIL {name}: {error}")
    print(f"passed={sum(1 for n in globals() if n.startswith('test_')) - failures} failed={failures}")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()

