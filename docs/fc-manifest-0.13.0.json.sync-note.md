# fc-manifest-0.13.0.json 同步注记（vendored 副本）

本文件说明 consema-py 仓 `docs/fc-manifest-0.13.0.json` 副本的来源与同步机制。载体对应波 3 对抗审计派工纪律第 4 条「vendored 文档副本生成机制」与总指挥裁决 R12（组 W3-04）。

## 副本机制（为何不入版本控制）

本仓 `docs/fc-manifest-0.13.0.json` 被 `.gitignore` 显式忽略（`/docs/fc-manifest-0.13.0.json`，见 .gitignore 注释「Only the manifest file is ignored under docs/, never the rest of docs/」）：

- 单一权威 = consema 规范仓（`consema/docs/fc-manifest-0.13.0.json`）。
- 本仓 CI（`.github/workflows/ci-python.yml` 的 provision-conformance 动作、`.github/actions/provision-conformance/action.yml`）在运行时从 consema checkout 复制该文件覆盖本副本（`Copy-Item … -Force`）；本仓永不提交该副本。
- 本副本仅供本地开发 / 离线运行（python/src/consema/conformance runner、capability_parity 等读取 `digests.conformance_suite` 与 `capability_set` 记录）。

## 来源与同步

- source: consema@e6d0246（母仓 HEAD，2026-08-14 重同步时刻；内容 sha256 `21141047…`）
- synced: 2026-08-14（波 3 W3-04 修复，agent F3；W3-12/F7 重同步：母仓 f58dc1f 删注记字符串行号后副本逐字节重随）
- 同步范围：`feature_complete_judgment.open_items[].evidence`（C-2 条）残留行号区间 `docs/fuzz-evidence-0.13.0.md` → 母仓现行节锚 `docs/fuzz-evidence-0.13.0.md §7（完成路径）`；W3-12/F7 重同步随母仓注记字符串行号删除。
- 同步后 sha256：`211410478b455ec92ee7e6ad1df8f17fa2b4258e9db6c633debb1deb0544c407`（与 consema@e6d0246 的 `docs/fc-manifest-0.13.0.json` 逐字节一致）。

## 同步 / 比对命令

```bash
# 母仓权威内容（LF 规范态）
git -C <consema-checkout> show HEAD:docs/fc-manifest-0.13.0.json | sha256sum
# 本仓副本
sha256sum docs/fc-manifest-0.13.0.json
# 两值一致即为同步；不一致时用母仓内容覆盖本副本：
git -C <consema-checkout> show HEAD:docs/fc-manifest-0.13.0.json > docs/fc-manifest-0.13.0.json
```

## 收口注记（裁决 R12 建议）

若后续实测本副本零功能引用（CI 全部由 provision 覆盖，本地 runner 亦改为直接读母仓 checkout），建议仅母仓持有本文件、本仓副本不再保留；本波不删除。
