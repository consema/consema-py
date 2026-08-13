# Contributing to consema-py（Consema Python 实现）

Consema 六仓拆分的 Python 仓：本仓承载 Python 实现（`python/` 包）与跨语言
差分验证工具；规范权威（RFC / docs / 路线图 / conformance suites）在
[规范仓](https://github.com/consema/consema)。

**社区治理以规范仓主文档为准**：报 bug / 提 feature / RFC 流程 / 提交规范 /
评审规范 / 标签体系 / 发布纪律 / 行为准则，一律参见
[consema/CONTRIBUTING.md](https://github.com/consema/consema/blob/main/CONTRIBUTING.md)。
本文件只列本仓特有内容。

## 开发环境

- Python `requires-python >= 3.12`（`pyproject.toml` 声明）；CI 矩阵
  3.12/3.13/3.14。
- 运行时零依赖（`dependencies = []`）；dev 依赖由 `[dev]` extra 提供。

## 构建与测试

```text
cd python
python -m pip install -e '.[dev]'
python -m pytest tests/
```

**前置：** conformance 数据（`conformance/` 与 `docs/fc-manifest-0.13.0.json`）
不随本仓提供，权威在规范仓；全新 clone 直接跑全量 pytest 会失败。本地运行
前先并排检出规范仓并把数据 provision 到工作区根（与 CI 的
`.github/actions/provision-conformance` 复合 action 相同，步骤见
[python/README.md](python/README.md) Verify；规范仓钉在 `ad667021`，即
cfd6e296 519-case 清单对应 commit）。缺数据时差分 integrity 测试按
documented skip 跳过。

## 贡献点

- **Python 实现**：`python/` 包（PortableValue / 查询 / 投影 /
  materialization / 结构编辑 + 八格式家族）；完整文档见
  [python/README.md](python/README.md)。
- **差分 harness**：`scripts/` 跨语言差分验证（byte parity / normalized
  differential / protocol exchange）：
  `python-verify-byte-parity.ps1`、`python-verify-normalized-differential.ps1`、
  `python-verify-protocol-exchange.ps1`。脚本构建 consema-rs 的 Rust emitter
  对拍本实现。
- **Conformance 数据同步**：conformance 数据来自规范仓 checkout（CI 多仓
  模式），权威在规范仓，改动必须回规范仓提交后再同步。

## CI 门禁

`.github/workflows/ci-python.yml`：7 个门禁 job —— python-gates（compileall
语法门禁 + editable install + pytest + 零依赖断言，3.12/3.13/3.14 矩阵）、
coverage（pytest-cov 全量，总覆盖 >= 60%）、python-conformance（runner
18 suites / 519 cases）、python-differential（Python-Rust 差分：byte parity /
normalized differential / protocol exchange，windows-latest 多仓 checkout）、
python-package（pip wheel --no-deps 打包门禁）、check-version-consistency
（README 版本行与 pyproject.toml 一致）、examples（SDK 链示例实跑）；
aggregate `check (all gates green)` 是唯一 required check。push 到 main 或
PR 均触发；PR 另受 pr-labels.yml 的 kind 标签门禁约束（标签见规范仓
.github/LABELS.md）。

## 发布与安全

- 发布：本仓 [RELEASING.md](RELEASING.md)（PyPI `consema`，trusted
  publishing OIDC；tag `v*` 触发 release workflow，不要手动发布）。
- 安全：[SECURITY.md](SECURITY.md)；披露统一走规范仓 SECURITY.md 的渠道。
