# Changelog

Consema 遵循 Semantic Versioning。本仓变更记录以规范仓 CHANGELOG 为权威；完整历史与跨语言时间线见 github.com/consema/consema 的 CHANGELOG.md。

## 1.0.0-rc.1（2026-08-12）

六仓拆分落地：本仓自规范仓（github.com/consema/consema）拆分独立（2026-08-12），承载 Python 实现（Python 3.12，运行时零依赖 `dependencies = []`，version 0.14.0）。

- L0-L4 落地（2026-08-12 · a0c318b / 5cf680b）：core / graph / protocol / document + 8 格式家族 + root facade + conformance runner；
- L5 差分 harness（2026-08-12 · 2f981df）：byte-parity / normalized differential / protocol-exchange 跨语言差分 + 五语言 CI workflow；差分发现的 wire-codec 缺陷随本 commit 修复；
- 首跑缺陷修复（2026-08-12 · dbba9a4）：python 测试夹具路径仓库相对化；
- conformance 519/519（18 套 / 聚合 digest cfd6e296 共钉）+ capability parity；
- CI（ci-python.yml）：editable install + pytest + 零依赖门禁、conformance runner 门禁、Python-Rust 差分门禁；
- 完整历史与跨语言时间线见规范仓 CHANGELOG。
