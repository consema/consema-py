# Consema Python implementation

The Python implementation of the language-neutral Consema
configuration-processing contracts (RFC 0002/0003/0004/0006 contract
family; authority: the spec repository docs/rfcs/; equal footing with
Rust/Go/TS/Kotlin per the 2026-08-11 owner decision). Zero third-party
runtime dependencies (`dependencies = []`, pyproject.toml:35; pytest is a
dev extra only) and never imports or calls the other implementations.

## Verify

**前置：** 全量 pytest 与 conformance runner 需要规范仓的 conformance 数据
（`conformance/vectors`、`conformance/differential`、`conformance/fixtures`
与 `docs/fc-manifest-0.13.0.json`），它们不随本仓提供（权威在
github.com/consema/consema，CI 钉在 `096e5f8`（2026-08-14 波 2 统一升级）——cfd6e296 519-case 清单对应
commit）。全新 clone 直接跑 `python -m pytest` 会失败：runner 测试与
capability parity 测试读取缺失的向量/清单数据而报错，各格式家族的 fixture
测试在 fixture 缺失时失败（G68 守卫：缺失即失败，不做静默 skip）；只有差分
integrity 测试与差分 case 文件完整性测试在缺数据时按 documented skip 跳过。
本地运行前把规范仓并排检出并把数据 provision 到本仓根（与 CI 的
`.github/actions/provision-conformance` 复合 action 相同；本地 checkout
必须与 CI 钉在同一个 commit `096e5f8`（2026-08-14 波 2 统一升级），否则 provision 的数据与 CI 不同）：

```
# 规范仓并排检出到 ../consema（git clone https://github.com/consema/consema ../consema
# && cd ../consema && git checkout 096e5f840ecc714912db779706fd881405b92308）后：
cp -r ../consema/conformance ./conformance
mkdir -p docs
cp ../consema/docs/fc-manifest-0.13.0.json ./docs/
# PowerShell 等价：Copy-Item -Recurse ../consema/conformance ./conformance
# 与 Copy-Item ../consema/docs/fc-manifest-0.13.0.json ./docs/
```

数据在场后：

```
cd python
python -m pip install -e '.[dev]'  # editable install；tests/ 树无 __init__.py，未安装时 collection ERROR
# CI 同款旗标：--import-mode=importlib + PYTHONPATH=tests/xml
PYTHONPATH=tests/xml python -m pytest --import-mode=importlib   # testpaths = tests（pyproject.toml [tool.pytest.ini_options] testpaths；wheel 打包由同文件 [tool.hatch.build.targets.wheel] packages 定义）
# PowerShell 等价：$env:PYTHONPATH='tests/xml'; python -m pytest --import-mode=importlib
python -m consema.conformance     # runner CLI (18 suites / 519 cases; __main__ at python/src/consema/conformance/__main__.py) — 仅仓库 checkout 可执行（wheel 安装后找不到 conformance/vectors）
# CI runs `python -m pytest tests/conformance/` (ci-python.yml, python-conformance job);
# differential tests live under tests/differential/ and require the golden
# env vars by harness:
#   CONSEMA_DIFFERENTIAL_RUST_DIR                       (byte parity)
#   CONSEMA_DIFFERENTIAL_NORMALIZED_RUST_DIR            (normalized differential,
#   CONSEMA_DIFFERENTIAL_NORMALIZED_PYTHON_DIR           forward + reverse emission)
#   CONSEMA_EXCHANGE_RUST_DIR / CONSEMA_EXCHANGE_PYTHON_DIR (protocol exchange)
# missing env = documented skip (the exchange test requires BOTH variables);
# without the conformance data the differential integrity tests skip too
# (documented; the python-verify-*.ps1 scripts provision both)
```

## Conformance

18 suites / 519 cases / aggregate digest `cfd6e296…` are pinned in
`tests/conformance/test_runner.py` (per-suite applicable surface
(passed, 0, 0) for every suite — any documented skip fails); 519/519 pass
in CI (ci-python.yml, python-conformance job).

## References

- Language plan: `https://github.com/consema/consema/blob/main/docs/multi-language-implementation-plan.md` (L0-L5 closed
  for all three new languages, 2026-08-12)
- CI and cross-language verification design: `https://github.com/consema/consema/blob/main/docs/five-language-ci-design.md`
