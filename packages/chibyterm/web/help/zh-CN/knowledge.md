# 知识库与文档库

开源核包含两条知识能力（演示页可从浏览器直接打开）：

| 能力 | 演示入口 | API 前缀 |
|------|----------|----------|
| **KnowledgeHub**（短经验 / 脚本知识） | `/demo/knowledge-hub` | `/api/kb` |
| **DocHub**（企业文档向量检索 MVP） | `/demo/doc-hub` | `/api/docs` |

## KnowledgeHub

- 维护可检索的运维短经验与脚本类条目  
- Agent 侧可通过 `kb_*` 类工具调用（视部署与模式）  
- 数据常落在 `data/knowledge_hub.db`

## DocHub

- 上传文档、切片与语义检索（embedding 可按环境降级）  
- 与 KnowledgeHub **双轨分工**：短经验 vs 长文档  
- 数据目录常见为 `data/doc_hub/`

## 工具市场

`/demo/tools-marketplace` 可浏览插件目录与技能包（`GET /api/tools/catalog` 等）。社区扩展约定见仓库 `tools/plugins/` 与 `docs/extending-agent-tools.md`。
