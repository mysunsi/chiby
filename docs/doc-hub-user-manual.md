# Chiby 企业文档库（DocHub）使用说明

本文说明 **DocHub** 与 **KnowledgeHub** 的双轨分工，以及如何导入文档、语义检索、在掌上 IM 中引用。

> DocHub = 企业长文档（PDF / Word / Markdown）切片 + 向量 TopK。  
> KnowledgeHub = 运维短经验（症状 → 根因 → 修复）。二者**不混库**。  
> **完整需求与设计 v2.0**见 [doc-hub-technical-design.md](./doc-hub-technical-design.md)（语义切片/混合检索/Chroma·Qdrant 双模式/一键重建；**§15 知识调度层**统一 search_knowledge——设计稿）。

---

## 1. 什么时候用哪个

| 场景 | 用 |
|------|-----|
| 「nginx 502 怎么修」这类故障经验 | KnowledgeHub · `kb_search` |
| 「变更窗口审批流程写在哪本手册」 | DocHub · `doc_search` |
| 上传公司规范 / 架构说明 / 操作手册 | DocHub 管理页 |
| Agent 沉淀刚修好的故障步骤 | KnowledgeHub · `kb_ingest` |

---

## 2. 入口

| 入口 | 地址 | 能力 |
|------|------|------|
| 管理页 | `/demo/doc-hub` | 多选上传、目录导入、列表、删除、试搜 |
| REST | `/api/docs/*`；Swagger `/docs` → DocHub | 上传 / 检索 / 删除 |
| 掌上 IM | `/demo/mobile-im` | Agent 调 `doc_search` / `doc_get` |

运维知识库仍在 `/demo/knowledge-hub` 与 `/api/kb`。

---

## 3. 部署与依赖（单机 MVP）

- 元数据：`data/doc_hub/docs.db`
- 原文副本：`data/doc_hub/files/`
- 向量：`data/doc_hub/chroma/`（需安装 `chromadb`；否则自动内存回退，**重启后向量丢失**）
- Embedding（按优先级）：
  1. `DOC_HUB_EMBEDDING_MODEL` + `DOC_HUB_EMBEDDING_API_KEY`（可选 `DOC_HUB_EMBEDDING_BASE_URL`）
  2. `OPENAI_API_KEY` → 默认模型 `text-embedding-3-small`
  3. 都没有 → **自动回退本地 hash 向量**（可入库可搜，语义较弱）
- 强制本地：`DOC_HUB_EMBEDDING_BACKEND=hash`
- 强制 API（无密钥则报错）：`DOC_HUB_EMBEDDING_BACKEND=litellm`

> 注意：DeepSeek 聊天密钥**不能**直接当 OpenAI embedding 用。没有 OpenAI / 兼容 embedding 网关时，DocHub 会走本地 hash，上传仍可成功。

```bash
# 建议装在「实际跑 uvicorn 的那个 venv」里
pip install chromadb pypdf python-docx
```

查看当前后端：`GET /api/docs/stats`
---

## 4. 导入

**管理页：** 选择 `.md` / `.txt` / `.pdf` / `.docx` →「上传入库」。

**目录批量（本机路径）：**

```http
POST /api/docs/ingest-path
{"path":"D:/docs/ops-manuals","recursive":true,"async_mode":true}
```

单文件 >20MB 或目录导入走后台任务，可用 `GET /api/docs/jobs/{job_id}` 查询。

---

## 5. 检索

```http
GET /api/docs/search?q=变更窗口审批&limit=8
```

Agent：

```text
<<<REMOTE_TOOL>>>
{"tool":"doc_search","q":"变更窗口审批","limit":5}
<<<END_REMOTE_TOOL>>>
```

读片段全文：

```text
<<<REMOTE_TOOL>>>
{"tool":"doc_get","chunk_id":"<chunk_id>"}
<<<END_REMOTE_TOOL>>>
```

`doc_search` / `doc_get` 只读、无确认卡；**没有** Agent 侧 `doc_ingest`（避免误传大文件）。

---

## 6. 体量预期

- MVP：几千～几万 chunk 可查；勿把几十 G 原文一次性 embed。
- 超大附件只存路径/副本，向量只存切片。
- 后续可替换 `vector_store` 实现为 Qdrant / pgvector，工具面可保持不变。

---

## 7. 与 KnowledgeHub 手册

运维短条目维护见 [knowledge-hub-user-manual.md](./knowledge-hub-user-manual.md)。
