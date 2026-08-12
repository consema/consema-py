# Consema Python（consema-py）

![CI](https://img.shields.io/github/actions/workflow/status/consema/consema-py/ci-python.yml?branch=main)
![Version](https://img.shields.io/github/v/tag/consema/consema-py)
![License](https://img.shields.io/github/license/consema/consema-py)

Consema 语言中立契约（RFC 0016）的 **Python 实现**仓库。本仓库是 Consema 六仓
拆分中的 Python 仓：规范权威（RFC、docs、路线图、跨语言 conformance suites）在
[github.com/consema/consema](https://github.com/consema/consema)；本仓承载
Python 实现与跨语言差分验证工具。

Version: 0.14.0（`python/pyproject.toml` version；CI
check-version-consistency job 断言与 README 一致）。

## 快速开始（30 秒跑通）

```text
pip install consema
```

把下面内容保存为 `python/quickstart.py` 后执行 `cd python && PYTHONPATH=src python quickstart.py`（一个 JSON 文档走完 parse → query → edit → render 四条链）：

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

完整链示例（parse → 操作符式原生语义查询 → best-exact 投影 → 结构编辑 → canonical 物化 → 跨格式转换到 TOML）：[`python/examples/sdk_chain.py`](python/examples/sdk_chain.py)，运行 `cd python && PYTHONPATH=src python examples/sdk_chain.py`。

## API 摘要

核心面一行式（完整签名见 [python/README.md](python/README.md)；八个格式家族各有独立的 `parse_*` / `execute_*_query` / `project` / `materialize` / `convert_*` 入口）：

| 操作 | facade 入口 |
| --- | --- |
| parse | `consema.registry.parse_document(source: bytes, profile: ProfileId) -> Document` |
| query | `consema.json.execute_json_query(executable: ExecutableQuery, document: JsonDocument, limits: JsonQueryLimits, cancellation: JsonCancellationToken) -> JsonQueryExecution` |
| project | `consema.json.project(document: JsonDocument, request: ProjectionRequest) -> ProjectionResult`（请求：`ProjectionRequestBuilder(ProjectionTarget.BEST_EXACT_CORE_V1).build()`） |
| edit | `consema.json.EditTransactionBuilder(document)` + `consema.json.commit(document, transaction) -> EditCommit`（`commit_result.document` 为编辑后文档） |
| materialize | `consema.json.materialize(value: PortableValue, request: MaterializationRequest) -> MaterializationResult` |
| convert | `consema.convert_json(source, projection_request, materialization_request) -> CompleteConversion \| ConversionFailure`（另有 convert_toml / convert_yaml / convert_ini / convert_properties / convert_xml / convert_plist / convert_hcl） |
| registry | `consema.format_families()` / `consema.profiles()` / `consema.query_domains()` / `consema.operation_registry(profile)`（8 家族 / 16 profiles / 21 查询域 / 16 操作注册表） |

## 布局

- `python/`：Python 包（Python 3.12，运行时零依赖 `dependencies = []`）。
  完整文档见 [python/README.md](python/README.md)。
- `scripts/`：跨语言差分验证脚本（byte parity / normalized differential /
  protocol exchange）。脚本构建 consema-rs 的 Rust emitter 并对拍 Python 实现；
  Rust 侧来自 consema-rs 仓 checkout（CI 多仓模式），conformance 数据来自规范仓 checkout。
- `.github/workflows/ci-python.yml`：Python 门禁（editable install + pytest +
  零依赖）、conformance runner 门禁（18 suites / 519 cases）与 Python-Rust 差分
  门禁（windows-latest 多仓 checkout）。

## 构建与测试

```text
cd python
python -m pip install -e '.[dev]'
python -m pytest tests/
```

## FAQ

- **支持哪些配置格式？** 八个格式家族、16 个 profiles：JSON（`json.strict@1` / `jsonc.bounded@1` / `json5.standard@1`）、TOML（`toml.1.0@1`）、YAML（`yaml.1.2-core@1` / `yaml.1.1-compat@1`）、INI（`ini.portable@1` / `ini.windows@1` / `ini.python-configparser@1`）、Java Properties（`java-properties.reader@1` / `java-properties.latin1@1`）、XML（`xml.1.0-safe@1`）、Property List（`plist.xml@1` / `plist.binary@1`）、HCL（`hcl.native@1` / `hcl.tfvars@1`）。完整面枚举见 `consema.profiles()`。
- **与 pydantic / jsonschema 的关系？** 互补而非竞争：pydantic 做运行时 schema 校验/类型转换，Consema 做格式内容处理（无损文档、查询、投影、原子编辑、跨格式转换）；Consema 明确不做业务 schema 校验（平台接入指南）。
- **性能如何？** 行为一致性由 18 suites / 519 cases conformance 门禁与跨语言差分门禁保证；解析/渲染基准基线见规范仓 `docs/BENCHMARKS-0.13.0.md` 与 Go 仓 [go/README.md](https://github.com/consema/consema-go/blob/main/go/README.md)。
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
