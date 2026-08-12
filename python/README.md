# Consema Python implementation

The Python implementation of the language-neutral Consema
configuration-processing contracts (RFC 0016; equal footing with
Rust/Go/TS/Kotlin per the 2026-08-11 owner decision). Zero third-party
runtime dependencies (`dependencies = []`, pyproject.toml:22; pytest is a
dev extra only) and never imports or calls the other implementations.

## Verify

```
cd python
python -m pytest                  # testpaths = tests (pyproject.toml:30-32)
python -m consema.conformance.runner  # runner CLI (18 suites / 508 cases; __main__ at runner.py:392)
# CI runs `python -m pytest tests/conformance/` (ci-python.yml:92-94); the plain
# `python -m consema.conformance` exits silently — the package has no __main__.py
# differential tests live under tests/differential/ and require the
# CONSEMA_DIFFERENTIAL_* golden env vars (missing env = documented skip)
```

## Conformance

18 suites / 508 cases / aggregate digest `35bebc8d…` are pinned in
`tests/conformance/test_runner.py` (per-suite applicable surface
(passed, 0, 0) for every suite — any documented skip fails); 508/508 pass
in CI (ci-python.yml, python-conformance job).

## References

- Language plan: `docs/multi-language-implementation-plan.md` (L0-L5 closed
  for all three new languages, 2026-08-12)
- CI and cross-language verification design: `docs/five-language-ci-design.md`
