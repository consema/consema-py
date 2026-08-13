# Consema Python implementation

The Python implementation of the language-neutral Consema
configuration-processing contracts (RFC 0016; equal footing with
Rust/Go/TS/Kotlin per the 2026-08-11 owner decision). Zero third-party
runtime dependencies (`dependencies = []`, pyproject.toml:35; pytest is a
dev extra only) and never imports or calls the other implementations.

## Verify

**前置：** 全量 pytest 与 conformance runner 需要规范仓的 conformance 数据
（`conformance/vectors`、`conformance/differential`、`conformance/fixtures`
与 `docs/fc-manifest-0.13.0.json`），它们不随本仓提供（权威在
github.com/consema/consema，CI 钉在 `ad667021`——cfd6e296 519-case 清单对应
commit）。全新 clone 直接跑 `python -m pytest` 会失败（runner 测试与各格式
家族的 fixture 测试报错）。本地运行前把规范仓并排检出并把数据 provision 到
本仓根（与 CI 的 `.github/actions/provision-conformance` 复合 action 相同）：

```
# 规范仓并排检出到 ../consema 后：
cp -r ../consema/conformance ./conformance
mkdir -p docs
cp ../consema/docs/fc-manifest-0.13.0.json ./docs/
```

数据在场后：

```
cd python
python -m pytest                  # testpaths = tests (pyproject.toml:47-48)
python -m consema.conformance     # runner CLI (18 suites / 519 cases; __main__ at python/src/consema/conformance/__main__.py)
# CI runs `python -m pytest tests/conformance/` (ci-python.yml, python-conformance job);
# differential tests live under tests/differential/ and require the
# CONSEMA_DIFFERENTIAL_* golden env vars (missing env = documented skip);
# without the conformance data the differential integrity tests skip too
# (documented; the python-verify-*.ps1 scripts provision both)
```

## Conformance

18 suites / 519 cases / aggregate digest `cfd6e296…` are pinned in
`tests/conformance/test_runner.py` (per-suite applicable surface
(passed, 0, 0) for every suite — any documented skip fails); 519/519 pass
in CI (ci-python.yml, python-conformance job).

## References

- Language plan: `docs/multi-language-implementation-plan.md` (L0-L5 closed
  for all three new languages, 2026-08-12)
- CI and cross-language verification design: `docs/five-language-ci-design.md`
