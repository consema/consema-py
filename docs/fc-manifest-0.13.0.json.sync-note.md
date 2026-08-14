# fc-manifest-0.13.0.json 同步注记（vendored 副本）

本文件说明 consema-py 仓 `docs/fc-manifest-0.13.0.json` 副本的来源与同步机制。载体对应波 3 对抗审计派工纪律第 4 条「vendored 文档副本生成机制」与总指挥裁决 R12（组 W3-04）。

## 副本机制（为何不入版本控制）

本仓 `docs/fc-manifest-0.13.0.json` 被 `.gitignore` 显式忽略（`/docs/fc-manifest-0.13.0.json`，见 .gitignore 注释「Only the manifest file is ignored under docs/, never the rest of docs/」）：

- 单一权威 = consema 规范仓（`consema/docs/fc-manifest-0.13.0.json`）。
- 本仓 CI（`.github/workflows/ci-python.yml` 的 provision-conformance 动作、`.github/actions/provision-conformance/action.yml`）在运行时从 consema checkout 复制该文件覆盖本副本（`Copy-Item … -Force`）；本仓永不提交该副本。
- 本副本仅供本地开发 / 离线运行（python/src/consema/conformance runner、capability_parity 等读取 `digests.conformance_suite` 与 `capability_set` 记录）。

## 来源与同步

- source: consema@ccc9943（母仓 HEAD，2026-08-15 波 4 R5 统一 provision 钉、F2 再锚；内容 sha256 `5cb4ab51…`）
- synced: 2026-08-14（波 3 W3-04 修复，agent F3；W3-12/F7 重同步：母仓 f58dc1f 删注记字符串行号后副本逐字节重随）；2026-08-15 波 4 R5：source 重锚 9aa6597（母仓 70e8884 R27/R40/R38 修订 manifest：锚点、C-2 freeze、审计计数 76→83 —— 源 sha256 变为 3fdf9a77；本仓 gitignore 副本为旧内容 21141047，已按下方命令重随）；2026-08-15 波 4 补派 F2：source 再锚 ccc9943（母仓 b8bf4cb R40 把 manifest 证据中两处裸行号改为字段锚「行号可能漂移，以字段名为锚」+ ccc9943 re-vendor —— 源 sha256 变为 5cb4ab51）
- 同步范围：`feature_complete_judgment.open_items[].evidence`（C-2 条）残留行号区间 `docs/fuzz-evidence-0.13.0.md` → 母仓现行节锚 `docs/fuzz-evidence-0.13.0.md §7（完成路径）`；W3-12/F7 重同步随母仓注记字符串行号删除；F2 再锚随母仓 b8bf4cb/ccc9943（`digests.product_version` 与 G1.5 证据行号 → 字段锚）。
- 同步后 sha256：`5cb4ab518cc3b3e1754a07f21953693b4b9ec113443d44c6ed8a6d4fa8faa6f8`（与 consema@ccc9943 的 `docs/fc-manifest-0.13.0.json` 逐字节一致）。

## 同步 / 比对命令

```bash
# 母仓权威内容（LF 规范态；统一 provision 钉 ccc9943）
git -C <consema-checkout> show ccc99430a6e3003bc1b0830d81cbad245323f0a4:docs/fc-manifest-0.13.0.json | sha256sum
# 本仓副本
sha256sum docs/fc-manifest-0.13.0.json
# 两值一致即为同步；不一致时用母仓内容覆盖本副本：
git -C <consema-checkout> show ccc99430a6e3003bc1b0830d81cbad245323f0a4:docs/fc-manifest-0.13.0.json > docs/fc-manifest-0.13.0.json
```

## 收口注记（裁决 R12 建议）

若后续实测本副本零功能引用（CI 全部由 provision 覆盖，本地 runner 亦改为直接读母仓 checkout），建议仅母仓持有本文件、本仓副本不再保留；本波不删除。
