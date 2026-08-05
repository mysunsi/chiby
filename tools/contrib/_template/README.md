# Contrib 工具模板：`<your_tool_id>`

> 复制本目录为 `tools/contrib/<your_tool_id>/`，填完后登记到上级 `MANIFEST.json`。

## 元信息

| 项 | 填 |
|----|-----|
| tool id | `your_tool_id`（小写+下划线） |
| 类型 | 本地只读 / 本地写入 / 主机只读 / 主机变更 |
| 是否需 host | 是 / 否 |
| 确认卡 | 是 / 否 |
| 作者 | |
| 许可证 | 与主仓一致或注明 |

## 用途（给用户看的一句话）

…

## 参数契约

```json
{"tool":"your_tool_id","...":"..."}
```

字段说明：

| 字段 | 必填 | 说明 |
|------|------|------|
| | | |

## 风险与安全

- [ ] 不接收 password/secret
- [ ] 不任意外网
- [ ] 变更已声明确认卡策略

## 建议实现落点

- [ ] `terminal/mobile/<name>_tools.py` runner
- [ ] `execute_remote_tool_call` 短路（本地）或 `build_file_tool_command`（文件）
- [ ] `DEFAULT_ALLOWED_TOOLS` + preamble
- [ ] 测试 `tests/test_<name>_tools.py`

## 手工验收

1. …
2. …

## 参考

- 官方 Hello World：`terminal/mobile/example_tools.py`
- 说明书：`docs/extending-agent-tools.md`
