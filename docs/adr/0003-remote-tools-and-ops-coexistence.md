# ADR-0003：远程运维工具化与 plan-only / OPS_* 协同

## 状态

已接受（2026-07-20）。实现前决议：不废弃 `plan_only`；远端 SSH/WinRM 工具化与现有 `OPS_PLAN`/`OPS_JOB` **分级共存**；凭据永不进入 Hermes 工具参数。

关联：[0002-hermes-full-capability.md](./0002-hermes-full-capability.md)、外部稿 `hermes_ssh_winrm_as_tools_design_20260720.md`（仅作问题陈述参考，落地以本 ADR 为准）。

## 上下文

- 路径 A 已落地：Hermes **禁本机** terminal/file（`plan_only` / `headless_proxy`）→ 吐 `OPS_*` 或等价计划 → Assistant 无头 SSH/WinRM → 回灌。
- 痛点：文本契约脆弱、多轮「规划↔执行」割裂、条件分支与多机表达不自然。
- 外部方案主张「SSH/WinRM 作 Hermes 原生工具 + 废弃 plan_only」。对管机房有价值，但若无门闩会削弱确认卡 / ACL / 审计；且与「本机动手」易混淆。
- 产品目标：管理整个机房；动手面永远是**远程主机**；本机 `native_workspace` 非主路径（见 ADR-0002）。

## 决策

### 1. 两条执行通道（并存，不互相替代）

| 通道 | 形态 | Hermes 侧 | Assistant 侧 | 默认 |
|------|------|-----------|--------------|------|
| **A1 契约透传** | `<<<OPS_PLAN>>>` / `<<<OPS_JOB>>>` | 只规划，无远端工具 | 解析契约 → 策略门闩 → 无头执行 → 文本回灌 | `remote_tools.enabled=false` 时的主路径 |
| **A2 远端工具** | `ssh_execute` / `winrm_execute` / `*_batch` / `host_list` … | 调工具（仅 `host_id`+命令） | 路由工具 → **同一套** ACL/确认/执行器 → **结构化**结果 | `remote_tools.enabled=true` 时的**唯一**执行通道 |

共同点：

- 均 **禁止** Hermes 本机执行（`plan_only` 语义保留：**禁本机**，不是「禁远程」）。
- 凭据只在 Assistant：`host_id` → `hosts.json`（password / 密钥）；工具 schema **不得**含 username/password。
- 只读可自动；变更 / 写文件须确认卡；审计落 JSONL。

### 2. 何时用哪一条：策略表

由 **Assistant 桥的路由策略**决定（配置 + 运行时能力），**不是**用户每次口头选择，也**不是**单靠模型自觉。

| 条件 | 选用 | 理由 |
|------|------|------|
| `remote_tools.enabled=false` | **仅 A1** | 阶段 0 / 默认生产安全姿态 |
| `enabled=true` 且本轮 Hermes 发出合法远端 tool_call | **仅 A2** | 结构化闭环；Hermes 唯一编排 |
| `enabled=true` 但 Hermes 误发 `OPS_*`、无 REMOTE_TOOL | **忽略 OPS**（显示正文 + 提示改用工具） | 单脑编排，禁止回退 A1 双跑 |
| 工具路由失败 / 未知工具 / 桥未实现该 tool | **明确错误**；不自动改走 A1 | 防重复变更 / 交叉管理 |
| `remote_tools.enabled=true` 时 | **Hermes 为唯一编排者**；Assistant 禁用 OPS_PLAN 生成/抽取；不再有双路径 | 单脑编排，避免双跑与超时 |
| 运维模式且规划器为 rules（非 Hermes） | **仅 A1**（或规则直出命令） | rules 无 ACP 工具环 |
| 高级 / 编程 + Hermes | 开工具后 **主路径 A2**；A1 为 fallback | 与「流畅闭环」目标一致 |

用户可见差异：开工具后，状态栏/审计更常出现 `tool=ssh_execute`；未开时仍是「计划块 → 执行」。**不必**在 UI 增加「选 A1/A2」开关（易混淆）；只需 `remote_tools.enabled`（及后续灰度百分比，可选）。

### 3. 谁来确定「有效时机」（职责）

| 角色 | 职责 | 不负责 |
|------|------|--------|
| **产品 / ADR（本文）** | 定默认、优先级、双跑禁止、非目标 | 每回合现场裁决 |
| **配置**（`hermes_bridge.yaml` → `remote_tools.enabled`） | 环境级总闸；演示/生产可不同 | 逐条命令 |
| **Assistant 桥路由**（实现时的单一决策点） | 按上表选 A1/A2；ACL；确认卡；凭据解析；审计 `exec_path=a1\|a2` | 把密码交给 Hermes |
| **Hermes（模型）** | 在工具可用时**只**调远端工具；是唯一编排者 | 选择凭据、绕过确认、输出 OPS_* |
| **用户** | 绑主机、顶栏多选、点「允许」、配 `hosts.json` | 选择 A1 vs A2 通道 |

**有效时机的判定顺序（实现必须遵守）：**

```text
1. remote_tools.enabled?
   └─ no  → 只认 A1（现网行为）
2. enabled=true 时：
   └─ Hermes 为唯一编排者，只发远端 tool_call，不生成 OPS_PLAN
   └─ Assistant 禁用 OPS_PLAN 抽取/规划，退为纯执行+显示+安全闸门
   └─ 若 Hermes 误发 OPS_*（模型未跟 schema）→ 忽略并提示「请使用工具」
   └─ 变更操作 → 弹确认卡 → 允许后执行（不写 OPS_PLAN 等规划）
3. A2 执行中失败且可归因「工具通道不可用」?
   └─ 提示用户重试；不自动把同一命令静默改走 A1 双跑
      （防重复变更；只读场景可由 Hermes 下一轮改调工具）
```

**单脑编排原则（新增，解决「两头忙」）：**

`remote_tools.enabled=true` 后，编排智能**只**在 Hermes 一侧：

- Hermes：规划、选工具、解释结果、决定下一步（唯一大脑）
- Assistant：机械执行 tool_call + 显示 + 安全闸门（ACL / 确认卡 / 审计）
- **Assistant 不再做 OPS_PLAN 抽取、不再规划、不再与 Hermes 竞争「思考」**
- 这样避免两侧都在「动脑子」导致的双跑、超时、协同失败

阶段 1 落地后，高级/编程模式的系统前缀应写明：「远端工具已启用，你**只**使用 `ssh_execute` / `winrm_execute` / `*_batch` 等工具；**不要**输出 `OPS_PLAN` / `OPS_JOB` 文本块。变更类工具会自动弹出确认卡，无需你预先写计划。」

### 4. 凭据铁律

1. 工具参数只允许：`host` / `hosts`（id 或 `__selected__`）、`command`/`script`、`timeout`、`workdir` 等非密钥字段。  
2. 桥：`host_id` → `hosts.json` → oneshot；缺凭据返回结构化 `credential_missing`（无密钥内容）。  
3. ACL：用户不可见的 host → `permission_denied`，不建连。  
4. 审计可记 `host_id`、命令摘要、exit_code；**禁止**记 password / 私钥。  
5. 用户「生效」方式不变：录入主机凭据 → ACL 挂 id → 会话绑定/多选 → `OPS_MOBILE_EXECUTOR=real` 真连。

### 5. 阶段与验收（实现前约定）

#### 阶段 1 契约面说明（已实现）

在 Hermes 尚未注册原生 `ssh_execute` ACP/MCP 工具前，A2 以正文契约落地：

```text
<<<REMOTE_TOOL>>>
{"tool":"ssh_execute","host":"<host_id>","command":"df -h"}
<<<END_REMOTE_TOOL>>>
```

桥路由、ACL、凭据、确认卡与执行器与终态 ACP 工具相同；后续换成真实 tool_call 时只换入站形态，不换策略。`plan_only` 仍禁本机 terminal/file。

#### 阶段 0（无工具代码，仅契约）

| 项 | 通过标准 |
|----|----------|
| 本 ADR 入库；index 挂链 | 评审同意 |
| `remote_tools.enabled` 设计为默认 false | 配置示例注释写清 |
| 未开工具时行为与现状一致 | 运维/高级各一轮冒烟 |

#### 阶段 1（最小工具面）

工具：`host_list`、`ssh_execute`、`winrm_execute`、`ssh_batch`、`winrm_batch`。  
策略与现网一致：只读自动、变更确认。

| # | 场景 | 通道 | 通过标准 |
|---|------|------|----------|
| 1 | 单机只读「看磁盘」 | A2 | 结构化结果 + 真实数字（real）或罐头（fake）；无确认卡 |
| 2 | 白名单外主机 | A2 拒 | `permission_denied`；零连接 |
| 3 | 缺凭据 | A2 错 | `credential_missing`；文案引导配主机 |
| 4 | 两机 nginx 状态 | A2 batch | 分主机结果 + summary |
| 5 | 「>80% 再查大文件」 | A2 连续调用 | 同会话内完成分支 |
| 6 | 重启 nginx | A2 + 卡 | 拒绝则不执行；允许后有 exit_code |
| 7 | 关工具或模型只吐 OPS_* | A1 | 旧路径仍可用 |
| 8 | WinRM 查内存 | A2 | 远端内存，非 Hermes Memory |
| 9 | 同轮 tool + OPS_* | 仅 A2 | 审计 `dual_path_ignored_ops`；命令不双跑 |

出门门槛：**1 + 4 + 6 + 2 + 3 + 9** 必过。

#### 阶段 2（远端文件/目录工具 · 已落地）

工具：`remote_list_dir`、`remote_read_file`、`remote_write_file`、`remote_mkdir`、`remote_remove`。  
实现：无 SFTP 时由 Assistant 编译为安全 SSH/WinRM shell（写文件 base64）；确认卡挂起结构化调用（含 content），批准后走 `execute_remote_tool_call`。

#### 阶段 3（开发增强工具 · 已落地）

工具：`remote_grep`（别名 `remote_search`）、`remote_diff`、`remote_backup` / `remote_restore`、`remote_syntax_check`、`remote_logs`、`remote_run`。  
要点：grep/logs/syntax 结构化 `data`；diff 优先 git 否则 `.hermes_backups`；写文件前自动 backup（`OPS_MOBILE_AUTO_BACKUP`）；`remote_run`+`stream=true` 接通 oneshot `stream_chunk` → 掌上 SSE。

| # | 场景 | 通过标准 |
|---|------|----------|
| F1 | list/read | 只读免确认；路径非法/根路径拒绝 |
| F2 | mkdir（全能型） | `confirm_changes=false` 时可自动 |
| F3 | write / remove | 始终确认卡；批准后真实写入/删除 |
| F4 | 编程 + A2 | preamble 引导优先文件工具，禁 OPS_PLAN |
| M1 | 全能型多机 NL | 注入 A2 `ssh_batch`/`winrm_batch` prompt；**禁止**劫持 OPS_JOB |
| M2 | batch 变更 | 拆成单机 `ssh_execute`/`winrm_execute` 逐台确认卡 |

### 6. 非目标（本 ADR 明确不做）

- 废弃 `plan_only` / 开放 Hermes 本机 terminal 当机房执行面。  
- 工具参数携带账号密码。  
- UI 上让用户每句切换「计划模式 / 工具模式」。  
- 阶段 1 做满 SFTP/sudo/JEA/`host_status` 大全（阶段 2+；**文件工具已用 shell 编译落地，非 SFTP**）。  
- A2 开启后 Assistant 仍保留 OPS_PLAN 规划/抽取（应禁用，退为纯执行）。  
- A2 失败后对**同一变更命令**自动静默改走 A1 重跑。

## 后果

- 实现有单一路由表可测；灰度靠配置，不靠改 prompt 碰运气。  
- 模型被引导「有工具用工具」，透传降为兼容层，避免双主长期对等。  
- 安全控制面仍在 Assistant，工具化不削弱确认与 ACL。  
- **单脑编排**：`enabled=true` 时 Hermes 独揽规划，Assistant 退为纯执行+显示，消除「两头忙」导致的超时/双跑。  
- 阶段 2 文件/目录工具已挂全能型 A2 + 确认卡；编程模式 preamble 引导优先结构化文件工具。

## 配置草案

```yaml
# data/hermes_bridge.yaml
remote_tools:
  enabled: false          # 总闸；会话模式仍可强制覆盖（ADR-0004）
  # allowed_tools:
  #   [host_list, ssh_execute, winrm_execute, ssh_batch, winrm_batch, remote_run,
  #    remote_list_dir, remote_read_file, remote_write_file, remote_mkdir, remote_remove,
  #    remote_grep, remote_diff, remote_backup, remote_restore, remote_syntax_check, remote_logs]
  # prefer_over_ops_plan: true   # 同轮双路径时忽略 OPS_*
```

`plan_only` / `execution_mode: headless_proxy`：**保留**，语义定为「禁本机执行工具」；与 `remote_tools` 正交。

## 修订记录

| 日期 | 说明 |
|------|------|
| 2026-07-20 | 初版：A1/A2 协同、路由职责、凭据铁律、阶段 0/1 验收 |
| 2026-07-20 | 修订：新增「单脑编排原则」——`enabled=true` 时 Hermes 独揽规划，Assistant 禁用 OPS_PLAN 抽取，消除两头忙 |
| 2026-07-20 | 阶段 1 落地：`REMOTE_TOOL` 契约 + `remote_tools` 配置/路由；ACP 原生工具待后续 |
| 2026-07-20 | 阶段 2 落地：`remote_list/read/write/mkdir/remove` + 确认卡结构化挂起 |
| 2026-07-20 | 全能型多机对齐 A2：NL 走 `ssh_batch`/`winrm_batch`；batch 变更拆单台确认 |
| 2026-07-22 | 阶段 3：`remote_grep/diff/backup/restore/syntax_check/logs/run` + 写前自动备份 + run 流式 |
| 2026-07-22 | Phase1：跨会话 `data/host_snapshots/{host_id}.json` 注入 `[Host Snapshot]`；`remote_rollback`→`remote_restore` |
