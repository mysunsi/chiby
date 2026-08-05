# tools/contrib — 社区工具贡献区

欢迎提交**可审核的** Agent 工具实现。维护者不承诺实现所有需求，但会按规范审安全与质量。

**推荐落地路径（已支持）**：审核通过后把目录晋升到 [`tools/plugins/`](../plugins/)（`manifest.yaml` + `handler.py`，`status: approved`），系统自动发现，无需改 `remote_tools.py`。  
规范见 [`docs/tool-plugin-architecture.md`](../../docs/tool-plugin-architecture.md)。

正式接线（主机工具 / 遗留显式注册）：[`docs/extending-agent-tools.md`](../../docs/extending-agent-tools.md)。  
Hello World：[`tools/plugins/example_echo/`](../plugins/example_echo/)。

## 你应该提交什么

1. **提案目录**：`tools/contrib/<your_tool_id>/`
   - `README.md`：用途、参数、风险、测试方式
   - 可选：按 plugins 规范写好的 `manifest.yaml` + `handler.py` 草稿（**本目录默认不会自动加载执行**）
2. **登记清单**：在 [`MANIFEST.json`](./MANIFEST.json) 的 `tools` 数组追加一条元数据（供工具市场页展示）。

## 审核底线（不通过则拒）

- **禁止**在工具参数中传递密码/密钥/token；凭据必须走 `host_id → hosts.json`。
- **禁止**未声明的外网任意 URL 拉取、任意代码执行、隐式提权。
- 本地工具必须 `host_required: false`，且由 loader 在 host 解析前短路。
- 变更类工具必须 `security.needs_confirmation: true`；高危操作需在 README 标明。
- 提供最小测试思路（可测 parse / confirm / 不触发 SSH）。

## 状态字段（MANIFEST）

| status | 含义 |
|--------|------|
| `proposed` | 仅登记想法 / 草稿，未合入运行时 |
| `review` | 审阅中 |
| `accepted` | 已晋升到 `tools/plugins/` 且 `status: approved` |
| `rejected` | 未采纳（可看 README 原因） |

## 推荐流程

```text
1. Fork → 按 _template/ 或 plugins/example_echo 复制
2. 本地将草稿放到临时 plugins 目录验证（status: approved）
3. 更新 MANIFEST.json（proposed）
4. 开 PR：标题 [contrib] your_tool_id
5. 维护者审安全 → 移入 tools/plugins/ → status: approved
```

工具市场预览页：`/demo/tools-marketplace`。
