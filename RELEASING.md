# Consema Python 发布流程（PyPI）

本文件是 consema-py 仓库的发布操作手册（六仓统一纪律见 consema 仓库根
`RELEASING.md`）。发布是**半自动**的：版本 bump、CHANGELOG、tag 由人完成；
tag 推送后 `.github/workflows/release.yml` 自动构建 wheel + sdist 并发布
`consema` 包到 PyPI。

## 1. 发布步骤（人执行的部分）

1. **版本 bump**：改 `python/pyproject.toml` 的 `version`，同步改以下全部
   位置（`check-version-consistency` 门禁强制一致，漏改即红）：
   - 仓根 `README.md` 的 `Version:` 行；
   - `python/src/consema/__init__.py` 的 `__version__`；
   - `.github/ISSUE_TEMPLATE/bug_report.yml` 环境信息节的版本字面量
     （`version（当前 <version>）`）。
2. **CHANGELOG 策展**：记录本版本变更；跨语言变更同步到
   consema 仓库 `https://github.com/consema/consema/blob/main/CHANGELOG.md`。
3. **质量门禁全绿**：main 分支 CI `check (all gates green)` 全绿
   （清单见各仓 ci 配置）。
4. **打 tag 并推送**（发布动作的唯一触发点）：
   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
   发布 workflow 会先校验两道前置守卫，任一不满足即 exit 1 中止：
   - **tag 必须指向 origin/main 历史内的 commit**（merge-base 祖先判定：
     tag 指向的 commit 必须在 origin/main 历史内，防止从陈旧/分叉 commit
     发布旧代码）；
   - **tag↔版本一致**（tag 去掉 `v` 前缀必须等于 `python/pyproject.toml`
     的 version）。
   校验通过后构建 wheel + sdist 并发布到 PyPI。

## 2. 凭证配置（用户侧一次性动作）—— trusted publishing

PyPI 的 **trusted publishing**（OIDC）是标准做法且**支持首次发布**
（与 crates.io 不同，无需先手动上传一次）。workflow 已按此编写
（`pypa/gh-action-pypi-publish@release/v1` + `id-token: write`），
**无需任何 token/密码 secret**。

1. 在 PyPI 发布过包的账号注册后，进入
   pypi.org → 项目（`consema`）→ **Manage → Publishing** →
   **Add a new publisher**：
   - **Publisher**：`GitHub`
   - **Repository owner**：`consema`
   - **Repository name**：`consema-py`
   - **Workflow file name**：`release.yml`
   - **Environment**：留空（workflow 未声明 environment）
2. 保存后即完成——后续推 tag 由 workflow 用短期 OIDC token 自动发布。

### 回退路径（可选）

若暂时不想用 trusted publishing，可改 workflow 为：
`pypa/gh-action-pypi-publish@release/v1` + `with: password:
${{ secrets.PYPI_API_TOKEN }}`（PyPI 账号 → API tokens 生成，写入 GitHub
仓库 secrets）。推荐直接 trusted publishing，无长期凭证。

## 3. 发布后核对

1. pypi.org/project/consema 确认新版本可见，描述（README）、
   classifiers、项目链接（Homepage/Repository）渲染正常。
2. GitHub Actions release workflow 全部步骤成功（build 步骤产出
   `python/dist/consema-<version>*.whl` + `.tar.gz`）。
3. 跨语言同步：按 consema 仓 RELEASING.md 的检查单核对其他语言仓的发布
   状态。

## 4. API reference 文档（决策：pdoc/sphinx 待发布时引入）

API reference 的 pdoc（或 sphinx）构建**尚未接线**（2026-08-12 决策）：
仓库不新增文档工具链依赖（零运行时依赖策略 §1.3 只约束运行时，但文档
工具链按"发布时引入"统一处理），对应的 docs CI job 与 typedoc/rustdoc
artifact 对标产物在工具链引入后补建。当前依赖面审计由
`.github/workflows/audit.yml`（pip-audit，dev extra 即全部依赖面）覆盖。
