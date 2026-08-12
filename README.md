# Consema Python（consema-py）

Consema 语言中立契约（RFC 0016）的 **Python 实现**仓库。本仓库是 Consema 六仓
拆分中的 Python 仓：规范权威（RFC、docs、路线图、跨语言 conformance suites）在
[github.com/consema/consema](https://github.com/consema/consema)；本仓承载
Python 实现与跨语言差分验证工具。

## 布局

- `python/`：Python 包（Python 3.12，运行时零依赖 `dependencies = []`）。
  完整文档见 [python/README.md](python/README.md)。
- `scripts/`：跨语言差分验证脚本（byte parity / normalized differential /
  protocol exchange）。脚本构建 consema-rs 的 Rust emitter 并对拍 Python 实现；
  Rust 侧来自 consema-rs 仓 checkout（CI 多仓模式），conformance 数据来自规范仓 checkout。
- `.github/workflows/ci-python.yml`：Python 门禁（editable install + pytest +
  零依赖）、conformance runner 门禁（18 suites / 508 cases）与 Python-Rust 差分
  门禁（windows-latest 多仓 checkout）。

## 构建与测试

```text
cd python
python -m pip install -e '.[dev]'
python -m pytest tests/
```

## 链接

- 规范仓（RFC / docs / 路线图）：https://github.com/consema/consema
- Rust 参考实现：https://github.com/consema/consema-rs
