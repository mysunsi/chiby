# TSM-A 运维手册 · OTP / Vault / SIEM / T4（记住·重放·归档）

版本：v1.1  
日期：2026-07-23  
关联：[tsm-a-security-model.md](./tsm-a-security-model.md)

---

## 1. 动态口令（OTP）

### 开启

```bash
# 高危确认卡在 YES 之外叠加 TOTP
set OPS_TSM_REQUIRE_OTP=1
set OPS_TSM_OTP_SECRET=JBSWY3DPEHPK3PXP   # Base32；生产请换自有种子
```

或企业校验（占位）：

```bash
set OPS_TSM_REQUIRE_OTP=1
set OPS_TSM_OTP_WEBHOOK=https://idp.example/otp/verify
# POST {"code":"123456","context":{...}} → {"ok":true}
```

### 行为

- 仅当 `require_otp` 为真（高危 + 已配置）时，允许前须校验。  
- 口令**不**写入审计；失败事件 `permission_otp_fail` 只记 `otp_reason`。  
- Demo 默认关闭，避免打断彩排。

### 自检

`GET /api/mobile/demo/status` → `tsm_l2.otp`；或 rehearsal checks `tsm_l2_otp`。

---

## 2. 主机密文（Vault / Fernet）

```bash
set OPS_ENCRYPT_HOST_SECRETS=1
# 可选：OPS_TSM_SECRET_STORE=local|vault
# Vault：OPS_VAULT_ADDR=...（未实现前失败安全）
```

短票：`OPS_TSM_EXEC_TICKET=1`（默认）、`OPS_TSM_EXEC_TICKET_TTL=120`。

---

## 3. SIEM 外送

### 配置文件

复制 `data/mobile_siem.example.yaml` → `data/mobile_siem.yaml`，或：

```bash
set OPS_TSM_SIEM_ENABLED=1
set OPS_TSM_SIEM_WEBHOOK=https://siem.example/ingest
# 或本地文件：
set OPS_TSM_SIEM_FILE=data/mobile_siem_out.jsonl
```

### 行为

- 审计写入后异步投递；失败进入 `data/mobile_siem_retry.jsonl`。  
- 不阻塞确认 / 执行主路径。  
- 可调用 `chibycore.siem_sink.flush_siem_retry_queue()` 冲刷重试。

### 自检

`status.tsm_l3.siem` / rehearsal `tsm_l3_siem`。

---

## 4. 建议生产组合

| 开关 | 生产建议 |
|------|----------|
| `OPS_ENCRYPT_HOST_SECRETS` | `1` |
| `OPS_TSM_EXEC_TICKET` | `1` |
| `OPS_TSM_REQUIRE_OTP` | 高危场景 `1` + 种子或 Webhook |
| `OPS_TSM_SIEM_*` | 至少 file 或 webhook 其一 |


---

## 5. T4：记住此类 / 取证重放 / 冷归档

### 记住此类（默认关）

```bash
set OPS_TSM_REMEMBER_CONFIRM=1
set OPS_TSM_REMEMBER_TTL_HOURS=24
```

- 仅 low/medium；高危 / YES / OTP / 结构化工具**永不**自动跳过确认卡。  
- 偏好文件：`data/confirm_prefs.json`。  
- 自检：`status.tsm_l1.remember_confirm`。

### 取证半自动重放

```http
POST /api/mobile/demo/forensic/replay
{"conversation_id":"...","turn_id":"...","dry_run":true}
```

- `dry_run=true`（默认）：只返回只读/变更拆分计划。  
- `dry_run=false`：只读命令可重跑；变更命令仅 `display_only`。

### 审计冷归档

```bash
set OPS_TSM_AUDIT_HOT_DAYS=14
```

```http
POST /api/mobile/demo/audit/archive
{"hot_days":14}
```

热窗口外行写入 `data/audit_archive/mobile_audit_YYYYMM.jsonl`。自检：`status.tsm_l3.audit_archive`。

---

## 6. 批量确认与 IM 富卡

### Web 批量确认

- 确认卡 `command_items` 多于 1 条时展示勾选（默认全选）。
- 允许时提交 `selected_indices`；未勾选则取消执行。
- 只读探测命令始终保留；勾选仅作用于变更项。

### 飞书

- 卡片含摘要 / 详情 / 门槛提示；header 按风险变色（红/橙/绿）。
- 高危：卡片内输入 `YES`；若开启 OTP 再填动态口令；回调透传至 `handle_permission`。

### 企微

- 文本卡含三层字段。
- 回复格式：`允许 <permission_id> [YES] [<OTP>]` / `拒绝 <permission_id>`。
