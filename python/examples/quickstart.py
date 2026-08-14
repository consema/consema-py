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
