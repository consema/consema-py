# Consema Python（consema-py）

![CI](https://img.shields.io/github/actions/workflow/status/consema/consema-py/ci-python.yml?branch=main)
![License](https://img.shields.io/github/license/consema/consema-py)

Consema 语言中立契约（RFC 0002/0003/0004/0006 契约家族；权威仓
docs/rfcs/）的 **Python 实现**仓库。本仓库是 Consema 六仓
拆分中的 Python 仓：规范权威（RFC、docs、路线图、跨语言 conformance suites）在
[github.com/consema/consema](https://github.com/consema/consema)；本仓承载
Python 实现与跨语言差分验证工具。

Version: 1.0.0-rc.1（`python/pyproject.toml` version；CI
check-version-consistency job 断言 README `Version:` 行与 pyproject 一致）。

## 快速开始（30 秒跑通）

```text
pip install consema（当前版本见上方 Version: 行；发布后可用）
```

把下面内容保存为 `python/quickstart.py` 后执行 `cd python && PYTHONPATH=src python quickstart.py`（PowerShell：`cd python; $env:PYTHONPATH='src'; python quickstart.py`）（一个 JSON 文档走完 parse → query → edit → render 四条链）：

```python
from consema.core import PortableValue
from consema.document import ProfileId
from consema.json import EditTransactionBuilder, RepresentationPolicy, commit
from consema.registry import parse_document


def member(value, name: str):
    """原生语义树成员查找（查询助手；完整操作符查询见 sdk_chain 示例）。"""
    members = value.object_members()
    if not members.is_available or members.value is None:
        raise RuntimeError("not an object")
    for m in members.value:
        if m.name().is_available and m.name().value == name:
            return m.value()
    raise KeyError(name)


def main() -> None:
    # 1. parse：json.strict 无损解析，render() 与源字节逐字节一致
    document = parse_document(b'{"a":1,"b":{"c":2}}', ProfileId.new("json.strict", 1))
    json_doc = document.as_json()
    assert json_doc is not None
    # 2. query：原生语义树读 `b.c`
    c = member(member(json_doc.root(), "b"), "c")
    # 3. edit：`b.c` 语义替换为 42（CanonicalForProfile），编辑外字节原样保留
    builder = EditTransactionBuilder(json_doc)
    builder.semantic_scalar(
        c.node_ref(), PortableValue.integer(42), RepresentationPolicy.CANONICAL_FOR_PROFILE
    )
    edited = commit(json_doc, builder.build()).document
    # 4. render：输出 {"a":1,"b":{"c":42}}
    print(edited.render().decode())


if __name__ == "__main__":
    main()
```

完整链示例（parse → 操作符式原生语义查询 → best-exact 投影 → 结构编辑 → canonical 物化 → 跨格式转换到 TOML）：[`python/examples/sdk_chain.py`](python/examples/sdk_chain.py)，运行 `cd python && PYTHONPATH=src python examples/sdk_chain.py`（PowerShell：`cd python; $env:PYTHONPATH='src'; python examples/sdk_chain.py`）。

## API 摘要

核心面一行式（下表；八个格式家族各有独立的 `parse_*` / `execute_*_query` / `project` / `materialize` 入口，`convert_*` 为根级统一入口、不按家族分包）：

| 操作 | facade 入口 |
| --- | --- |
| parse | `consema.registry.parse_document(source: bytes, profile: ProfileId) -> Document` |
| query | `consema.json.execute_json_query(executable: ExecutableQuery, document: JsonDocument, limits: JsonQueryLimits, cancellation: JsonCancellationToken) -> JsonQueryExecution` |
| project | `consema.json.project(document: JsonDocument, request: ProjectionRequest) -> ProjectionResult`（请求：`ProjectionRequestBuilder(ProjectionTarget.BEST_EXACT_CORE_V1).build()`） |
| edit | `consema.json.EditTransactionBuilder(document)` + `consema.json.commit(document, transaction) -> EditCommit`（`commit_result.document` 为编辑后文档） |
| materialize | `consema.json.materialize(value: PortableValue, request: MaterializationRequest) -> MaterializationResult` |
| convert | `consema.convert_json(source, projection_request, materialization_request) -> CompleteConversion \| ConversionFailure`（另有 convert_toml / convert_yaml / convert_ini / convert_properties / convert_xml / convert_plist / convert_hcl） |
| registry | `consema.format_families()` / `consema.profiles()` / `consema.query_domains()` / `consema.operation_registry(profile: ProfileId)`（8 家族 / 16 profiles / 21 查询域 / 16 操作注册表；`operation_registry` 接受 `ProfileId`，从 `profiles()` 取条目时用 `entry.profile_id()`） |

## 布局

- `python/`：Python 包（`requires-python >= 3.12`，CI 矩阵 3.12/3.13/3.14，
  运行时零依赖 `dependencies = []`）。完整文档见 [python/README.md](python/README.md)。
- `scripts/`：跨语言差分验证脚本（byte parity / normalized differential /
  protocol exchange）。脚本构建 consema-rs 的 Rust emitter 并对拍 Python 实现；
  Rust 侧来自 consema-rs 仓 checkout（CI 多仓模式），conformance 数据来自规范仓 checkout。
- `.github/workflows/ci-python.yml`：8 个门禁 job —— python-gates（compileall
  语法门禁 + editable install + pytest + 零依赖断言，3.12/3.13/3.14 矩阵）、
  coverage（pytest-cov 全量，总覆盖 >= 60%）、python-conformance（runner
  18 suites / 519 cases）、python-differential（Python-Rust 差分：byte parity /
  normalized differential / protocol exchange，windows-latest 多仓 checkout）、
  pip-audit（OSV advisory 声明面审计，push/PR 触发；每日 cron 在独立
  audit.yml）、python-package（pip wheel --no-deps 打包门禁）、
  check-version-consistency（四处一致：README `Version:` 行 /
  pyproject.toml version / `__init__.__version__` / bug_report.yml 版本
  字面量）、examples（SDK 链示例实跑）；另有
  aggregate `check (all gates green)` 门禁。

## 构建与测试

```text
cd python
python -m pip install -e '.[dev]'
# CI 同款旗标：--import-mode=importlib（多个目录共享 basename 的测试文件，
# prepend 模式会 import file mismatch）+ PYTHONPATH=tests/xml（xml 测试把
# conftest 作为模块导入：`from conftest import …`）
PYTHONPATH=tests/xml python -m pytest --import-mode=importlib tests/
# PowerShell 等价：$env:PYTHONPATH = 'tests/xml'; python -m pytest --import-mode=importlib tests/
```

**前置（重要）：** 全量测试需要规范仓的 conformance 数据——`conformance/`
（vectors、differential、fixtures）与 `docs/fc-manifest-0.13.0.json`
**不随本仓提供**（权威在 [github.com/consema/consema](https://github.com/consema/consema)）。
全新 clone 未 provision 时跑 `python -m pytest tests/` 会失败：conformance
runner 测试（tests/conformance/）与 capability parity 测试读取仓库相对路径
的向量数据/清单而报错，各格式家族的 fixture 测试读取 conformance/fixtures
而报错；差分 integrity 测试（tests/differential/）缺数据时按 documented
skip 跳过（见 python/README.md Verify）。本地运行前先把规范仓并排检出并把
conformance 数据 provision 到工作区根（与 CI 的
`.github/actions/provision-conformance` 复合 action 相同；CI 把规范仓钉在
`096e5f8`——cfd6e296 519-case 清单对应的 commit、2026-08-14 波 2 统一升级）：

```text
# 规范仓并排检出到 ../consema；本地 checkout 必须与 CI 钉在同一个 commit
# （CI 用 096e5f8——cfd6e296 519-case 清单对应的 commit、2026-08-14 波 2 统一升级），否则 provision
# 的数据与 CI 不同：
#   git clone https://github.com/consema/consema ../consema
#   cd ../consema && git checkout 096e5f840ecc714912db779706fd881405b92308
# PowerShell 等价：Copy-Item -Recurse ../consema/conformance ./conformance
# 与 Copy-Item ../consema/docs/fc-manifest-0.13.0.json ./docs/
cp -r ../consema/conformance ./conformance
mkdir -p docs
cp ../consema/docs/fc-manifest-0.13.0.json ./docs/
```

数据在场后全量 pytest 通过（CI 同款 `--import-mode=importlib` +
`PYTHONPATH=tests/xml`；能力 parity 4 个测试亦全绿）。现行计数以最近
CI run 为准（2026-08-15 实测 729 passed / 4 skipped，见 GitHub Actions；
本 README 不硬编码测试数量——wave-4 R16）。

## FAQ

- **支持哪些配置格式？** 八个格式家族、16 个 profiles：JSON（`json.strict@1` / `jsonc.bounded@1` / `json5.standard@1`）、TOML（`toml.1.0@1`）、YAML（`yaml.1.2-core@1` / `yaml.1.1-compat@1`）、INI（`ini.portable@1` / `ini.windows@1` / `ini.python-configparser@1`）、Java Properties（`java-properties.reader@1` / `java-properties.latin1@1`）、XML（`xml.1.0-safe@1`）、Property List（`plist.xml@1` / `plist.binary@1`）、HCL（`hcl.native@1` / `hcl.tfvars@1`）。完整面枚举见 `consema.profiles()`。
- **与 pydantic / jsonschema 的关系？** 互补而非竞争：pydantic 做运行时 schema 校验/类型转换，Consema 做格式内容处理（无损文档、查询、投影、原子编辑、跨格式转换）；Consema 明确不做业务 schema 校验（平台接入指南）。
- **性能如何？** 行为一致性由 18 suites / 519 cases conformance 门禁与跨语言差分门禁保证；解析/渲染基准基线见规范仓 `https://github.com/consema/consema/blob/main/docs/BENCHMARKS-0.13.0.md` 与 Go 仓 [consema-go/go/README.md](https://github.com/consema/consema-go/blob/main/go/README.md)。
- **零依赖吗？** 是——`dependencies = []`（pytest 仅 dev extra）。
- **跨语言一致性如何保证？** 18 套语言无关 conformance suite 共 519/519 cases（聚合 digest `cfd6e296…`）由规范仓维护、五仓共享；CI 多仓 checkout 跑 conformance runner 与 Python-Rust 差分门禁（byte parity / normalized differential / protocol-exchange）。
- **兼容承诺？** 语义化版本；`check-version-consistency` 门禁断言 README 版本行与 `pyproject.toml` 一致；兼容与支持政策见 RFC 0020。
- **如何贡献？** 见本仓 [CONTRIBUTING.md](CONTRIBUTING.md)（规范仓为权威版）；conformance 向量/夹具/oracle/差分数据权威在规范仓——向量变更是五仓同步事件，必须先回规范仓提交再同步五个语言仓。
- **"默认拒绝信息损失"是什么意思？** 投影/转换/编辑中的任何 loss（如 YAML 共享结构展开、Properties 重复键折叠、数值舍入）必须显式授权；未授权时操作原子失败（`convert_*` 返回 `ConversionFailure`；fidelity 三档：Exact / Transformed / Lossy）。

## 六仓导航

| 仓库 | 角色 |
| --- | --- |
| [consema](https://github.com/consema/consema) | 规范 / RFC / 路线图 / 审计证据 / conformance 仲裁层（语言无关权威） |
| [consema-rs](https://github.com/consema/consema-rs) | Rust 参考实现 |
| [consema-go](https://github.com/consema/consema-go) | Go 实现 |
| [consema-ts](https://github.com/consema/consema-ts) | TypeScript 实现 |
| [consema-py](https://github.com/consema/consema-py)（本仓） | Python 实现 |
| [consema-kt](https://github.com/consema/consema-kt) | Kotlin 实现 |

## 文档导航

- 规范仓（RFC / docs / 路线图 / conformance 权威）：https://github.com/consema/consema
- [RFC 0001-0016](https://github.com/consema/consema/tree/main/docs/rfcs) + [RFC 0020 兼容与支持政策](https://github.com/consema/consema/blob/main/docs/rfcs/0020-compatibility-and-support-policy-v1.md)：语言无关规范的权威载体
- [1.0.0 产品路线图](https://github.com/consema/consema/blob/main/Consema%201.0.0%20产品路线图与双语言落地设计.md)
- [平台接入指南](https://github.com/consema/consema/blob/main/docs/platform-integration-guide.md)
- [CLI Cookbook（可复制配方）](https://github.com/consema/consema/blob/main/docs/cookbook.md)
- [多语言实现计划](https://github.com/consema/consema/blob/main/docs/multi-language-implementation-plan.md) / [五语言 CI 设计](https://github.com/consema/consema/blob/main/docs/five-language-ci-design.md)
