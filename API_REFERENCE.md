# API 参考

薄入口：完整 Schema 以运行时 **OpenAPI** 为准。

## 在线文档

```bash
uvicorn chibyterm.main:app --host 127.0.0.1 --port 8000
```

浏览器打开：`http://127.0.0.1:8000/docs`

开源默认 OpenAPI **不应**出现 `/api/mobile/*`、`/ws/hermes`（需安装闭源扩展并开启对应环境变量后才会挂载）。

## 核心路由前缀

| 前缀 / 路径 | 说明 | 开源 |
|-------------|------|:---:|
| `/ws/terminal/{session_id}` | Web 终端 WebSocket | ✅ |
| `/api/sessions/*` | 终端会话 CRUD / 转录 / AI 流 | ✅ |
| `/api/hosts/*` | 主机目录与连通性探测 | ✅ |
| `/api/hosts/{id}/closure-execute` | 按主机闭环执行 | ✅ |
| `/api/sessions/{id}/closure-execute` | 按会话闭环执行 | ✅ |
| `/api/closure-interactive/{trace_id}/resume` | 人机共编闭环续跑 | ✅ |
| `/api/kb/*` | KnowledgeHub（`/stats` `/search` `/kb` `/ingest` …） | ✅ |
| `/api/docs/*` | DocHub | ✅ |
| `/api/tools/*` | 工具目录 / 技能包（`catalog` `packs`） | ✅ |
| `/api/broadcast/*` | Fleet 批量任务 / 报告 / 定时 | ✅ |
| `/api/llm/*` | LLM 配置 | ✅ |
| `/api/health` | 健康检查 | ✅ |
| `/api/mobile/*` | 掌上 AI | ❌ 闭源扩展 |
| `/ws/hermes` | Hermes WebSocket | ❌ 闭源扩展 |

专题说明：

- KnowledgeHub：[docs/knowledge-hub-user-manual.md](./docs/knowledge-hub-user-manual.md)
- DocHub：[docs/doc-hub-user-manual.md](./docs/doc-hub-user-manual.md)
- 闭环：[docs/closure-api.md](./docs/closure-api.md)

## 认证

当前演示/默认部署可用基础 UI 登录与 `external_user_id` 类标识；生产部署建议前置认证代理（SSO 为企业扩展能力）。

## 示例

```bash
# 健康检查
curl http://127.0.0.1:8000/api/health

# 列出主机（勿在日志中打印密码字段）
curl http://127.0.0.1:8000/api/hosts

# KnowledgeHub 统计
curl http://127.0.0.1:8000/api/kb/stats

# 工具目录
curl http://127.0.0.1:8000/api/tools/catalog
```

命令执行与确认卡主路径在 **Web 终端会话**（`/api/sessions` + `/ws/terminal/...`）与 **闭环 API**；完整请求体见 `/docs`。
