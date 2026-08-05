# 插件场景集成测试

> 对应：`tests/test_plugin_scenario_chains.py`（`@pytest.mark.integration`）  
> 目标：用**真实运维故事**串起 parse → confirm/pending → 插件 → 内核，不连真实 SSH。

---

## 怎么跑

```bash
# 仅场景串链
pytest tests/test_plugin_scenario_chains.py -q

# 全部 integration
pytest -m integration -q
```

---

## 场景一览

| 故事 | 链路 | 关键断言 |
|------|------|----------|
| A 配置变更 | `host_list` → `remote_read_file` → `remote_backup` → `remote_write_file` | 写工具确认卡 pending 往返；host_list 不触 SSH |
| B 知识取正文 | `kb_search` → `kb_get` | 纯本地；结果 `entry_id` 可串联 |
| B2 统一调度 | `search_knowledge` → `get_content` | `full_id` 路由；禁 SSH |
| C 排障取证 | `host_list` → `list_dir` → `grep` → `logs` | 全部免确认；executor 三次 |
| D 回滚 | `backup` → `write` → `restore`；`remote_rollback` 别名 | pending 保留 `backup_path`；别名归一为 `remote_restore` |
| E Agent 多块 | `example_echo` + `doc_search` + Phase6 catalog | 一条消息多 `REMOTE_TOOL`；市场 `phase==6` |

---

## 边界

- **不**测真实网络 / LLM / 编排器完整 A2 对话。
- 主机面：`FakeExecutor`；知识面：临时 SQLite / DocHub fixture。
- 运行时白名单与市场 catalog 仍分离（见 [tool-marketplace-phase6.md](./tool-marketplace-phase6.md)）。
