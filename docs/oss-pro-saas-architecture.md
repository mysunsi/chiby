# 开源 / 闭源整体架构：终端开源版 · 终端 Pro · 掌上 SaaS

版本：v1.4  
日期：2026-08-04  
状态：**产品架构定稿草案**（与实现切仓计划对齐）  
关联：

- [open-source-boundary-review.md](./open-source-boundary-review.md) — 文件级切分与依赖冲突评审（含 **§0.1 决议锁定** 与 **§8 P0 清单**）  
- [tsm-a-security-model.md](./tsm-a-security-model.md) — TSM-A 三层安全模型  
- [ai-agent-contest-system-gap.md](./ai-agent-contest-system-gap.md) — 参赛缺口与冲刺范围  
- [system-code-structure.md](./system-code-structure.md) — 当前单体仓库结构  

**产品品牌**：**Chiby（赤壁）** — 运维智能中枢（英文拼写 Chiby；中文取「运筹帷幄、决胜千里」）。  
**产品核设想**：终端模式开源版（**最新 Web/ops 终端全量**：闭环 + KB/Doc） + 终端 Pro / Chiby 中枢（智能型/全能型；可基于上游 Hermes Agent） + 掌上模式（完全闭源）。

**2026-08-04 决议**：开源核 = 全量终端（闭环/KB）；闭源 = 掌上 + Hermes 桥；**Monorepo 过渡**；**先 P0 代码解耦，后 P2 仓/镜像**。

**包名约定**：开源执行平面 **`ops-bridge` / `chibyterm`**；闭源过渡包 **`chiby_hermes_bridge`**、**`chiby_mobile`**；远期 Pro 为 **`assistant-pro` / `pro_core`**。

---

## 0. 术语与知识产权口径（必读）

### 0.1 命名：Chiby（赤壁）≠ 给 Hermes 改名

**不是给 Hermes 重新命名。** 上游仍叫 Hermes；我们的 AI Agent 产品叫 **Chiby（赤壁）**。

| 名称 | 角色 | 说明 |
|------|------|------|
| **Chiby（赤壁）** | 我们的 AI Agent **产品名** | 中文「赤壁」取「运筹帷幄、决胜千里」；英文品牌拼写 **Chiby**（区别于标准拼音 Chibi，便于商标独立） |
| **Hermes Agent** | **上游开源依赖**（MIT） | Nous Research；技术底座，不是我们的品牌 |
| **ops-bridge** | 开源执行平面包名 | 护栏、最小执行器、Demo |
| **assistant-pro** | 终端 Pro 闭源包名 | 含 `pro_core`、全量工具、连接池等 |

**对外说法**

- 主品牌：**Chiby（赤壁）** — 运维智能中枢
- 技术一句：基于 Hermes Agent（MIT）构建
- 避免：产品直接叫「尚思 Hermes」，或暗示 Hermes 品牌/版权归尚思

### 0.2 「大脑」分两层，勿混称

| 称呼 | 含义 | 开源？ |
|------|------|--------|
| **模型层**（狭义 AI 大脑） | LLM 权重/推理 API | 可采购第三方 API，或自建；≠产品开源义务 |
| **Chiby 中枢**（产品大脑） | 智能型/全能型闭环：规划门闸、工具调度、回灌、许可、专利绑定 | **商业闭源**（`pro_core` + 云中枢服务） |
| **上游 Hermes Agent** | Nous Research 的开源 Agent 运行时（现网 `D:/Open/Hermes`） | **MIT**；技术底座，不是产品品牌 |

### 0.3 上游 Hermes：MIT 允许商用，但不等于整棵归你们专有

现网依赖的 Hermes Agent 许可证为 **MIT（Copyright Nous Research）**。

| 可以做 | 不可以做 / 高风险 |
|--------|-------------------|
| 商用、修改、内嵌进 Pro/SaaS | 对外宣称「Hermes 整项目是我们的闭源大脑」 |
| 发行含自有代码的闭源二进制/云服务 | 去掉上游版权与 MIT 声明 |
| 闭源 **Chiby / `pro_core`** 自有逻辑 | 用商标叙事让人以为「Hermes」品牌/版权独家属于尚思 |
| 开源仓不打包智能型/全能型中枢 | 阻止他人依法使用**上游开源 Hermes** 做竞品 |

**合规底线**：凡发行物中含上游 Hermes 代码或实质衍生，须保留 MIT 版权与许可声明；法务复核 NOTICE/第三方清单。

### 0.4 闭源资产清单（真正要握在手里的）

1. **Chiby（赤壁）/ `pro_core`（C++）**：会话门闸、工具放行、许可、专利 A/B/C、与上游 Agent 的交互封装
2. **运维技能 / 追因模板 / 企业策略**（自有数据与逻辑）
3. **云试用中枢服务**（账号、trial token、调度）
4. **掌上 SaaS 后端**
5. **品牌：Chiby（赤壁）** — 中文取「运筹帷幄、决胜千里」；英文品牌拼写 **Chiby**（区别于标准拼音 Chibi，便于商标独立）

**不声称独占**：上游 Hermes 仓库本身、通用 LLM、开源 ops-bridge 护栏。

### 0.5 参赛 / 商务一句话

> 我们的 AI Agent 产品名为 **Chiby（赤壁）**，基于 Hermes Agent（MIT）构建。开源的是执行护栏与 Demo（ops-bridge）；智能型/全能型中枢作为商业 Pro/SaaS（试用默认云端，付费可选本地）。我们掌握的是 Chiby 中枢与许可，不是「收回开源 Hermes」。

---

## 1. 整体系统架构总览

```text
用户入口
  终端 CLI ──┐
  终端 Web UI ├──► 逻辑网关：/open → 开源版 · /pro → Pro · /mobile → SaaS
  掌上 App/微信┘

┌────────────────────────────┐  ┌────────────────────────────────────┐
│ 终端模式 · 开源版          │  │ 终端模式 · Pro 版（闭源）           │
│ ops-bridge                 │  │ assistant-pro                      │
│ · TSM-A L1/L2/L3 护栏      │  │ · 运维中枢（可接 Hermes Agent）     │
│ · SSH/WinRM 最小后端       │  │ · 全量工具面 / 连接池 / 追因库     │
│ · 只读工具子集             │  │ · 堡垒机集成                       │
│ · 指令型 + 分析型          │  │ · C++ pro_core（中枢门闸 + 许可） │
│ · Demo 编排 + 开源 Web     │  │ · 三专利核心算法                   │
└────────────────────────────┘  └────────────────────────────────────┘
                ▲ 依赖 pip：ops-bridge >=0.1,<1.0

┌────────────────────────────────────────────────────────────────────┐
│ 掌上模式 · 闭源 SaaS（独立代码库，不依赖 ops-bridge 本地安装）      │
│ · 移动后端 + IM + 多主机 Job + 企业策略 + 计费                     │
│ · 云端运维中枢；客户主机凭据经堡垒机/临时通道，SaaS 不落盘明文     │
└────────────────────────────────────────────────────────────────────┘
`

**模式命名对照（材料 ↔ 现网）**

| 架构用语 | 现网 gent_mode | 归属 |
|----------|-------------------|------|
| 指令型 | efficient | 开源版 |
| 分析型 | intelligent | 现网依赖中枢；**开源版仅提供无中枢弱化分析**（如 planner_m1），不得等同现网智能型 |
| 全能型 | omnipotent | **仅 Pro / SaaS 中枢** |

> 现网智能型/全能型均依赖中枢（可接 Hermes Agent）。**切仓后**开源包不得打包中枢或试用云密钥。

---

## 2. 开源部分：终端模式开源版（chibyterm / ops-bridge）

### 2.1 定位

- 免费、开源（**Apache 2.0**）、可独立安装运行  
- 面向个人开发者、小团队、学习与二次开发  
- 提供 **最新 Web/ops 终端全量体验**：TSM-A 护栏 + SSH/WinRM + NL→Shell + **闭环治理** + **KnowledgeHub / DocHub**  

### 2.2 包含模块（2026-08 决议）

| 模块 | 内容 | 说明 |
|------|------|------|
| web / session | `index.html`、standalone、会话 / PTY / WinRM | **开源核** |
| confirm / audit | 确认卡、YES、JSONL 审计 | TSM-A 亮点；链式哈希归 Pro |
| executor / gateway | oneshot + `execution_gateway` + 变更冻结 | **全量进开源** |
| closure | 闭环重试、认知摘要、replay bundle、治理 REST | **全量进开源** |
| knowledge / doc | KnowledgeHub + DocHub | **全量进开源** |
| llm | `llm_config` / providers / models store | 开源；不含云中枢密钥 |
| tools | 插件契约 + 社区只读为主；写操作经确认卡 | 掌上 A2 全量编排仍闭源 |
| modes | 指令型（规则）+ 弱化分析（无 Hermes） | **不含**智能型/全能型中枢 |

### 2.3 明确不包含

- 智能型/全能型运维中枢（`hermes_bridge` / Chiby ACP 路径）  
- 掌上 AI 机房后端与 Demo（`terminal/mobile/**`、IM、Job）  
- 堡垒机 / 企业计费 / 许可控制  
- 上游 Hermes 源码整仓（MIT 依赖，另仓声明，不混称自有闭源）  

### 2.4 与边界评审的硬约束

切仓时须满足 [open-source-boundary-review.md](./open-source-boundary-review.md)：

1. 开源包 **不得** import 闭源模块；闭源经 pip 依赖开源。  
2. **先 P0 代码解耦，再 P2 公开镜像**；不可「先拆仓、代码仍互 import」。  
3. 默认安装 **零闭源包** 即可跑通最新终端（闭环 + KB/Doc）。  

---

## 3. 闭源部分：终端模式 Pro 版（全能型）

### 3.1 定位

- 商业授权；**C++ `pro_core`** 承载运维中枢门闸与许可；可调用上游 Hermes Agent（MIT）或云中枢  
- 面向企业客户，完整 AI 运维 Agent 能力  
- 安装可选：全新安装 / 在开源版旁启用 Pro 插件目录  

### 3.2 包含模块

| 模块 | 内容 | 说明 |
|------|------|------|
| **pro/core（C++）** | **运维中枢门闸**（智能型/全能型）+ 许可 + 专利；封装对 Agent/云中枢的调用 | **主闭源堡垒**；见 §3.3；≠宣称独占上游 Hermes |
| pro/tools_full | 全量工具面（含写操作与服务控制）的 Python 适配层 | 真正执行仍走开源/Pro 执行器；调度闸在 C++ |
| pro/connectors | 生产级 SSH/WinRM 池、熔断、重试、备份 | 可 Python/C++ 混合；策略令牌校验在 C++ |
| pro/knowledge | 追因模板库数据与检索胶水 | 模板数据可独立；匹配/门闸可进 C++ |
| pro/fortress | 尚思堡垒机 ORM、PAM | 企业适配，Python 为主 |
| pro/python_shim | 薄 FFI：import pro_core → 调 C++ | **无业务决策**；只做编解码与异步桥 |

> **原则**：凡「是否允许进智能型/全能型、工具门闸、许可、专利绑定」优先在 **`pro_core`**；上游 Hermes Agent 仅作可替换运行时依赖（MIT）。Python 做 UI/HTTP/开源护栏与 FFI。

### 3.3 C++ 核心运行时（pro_core）

不只做许可。**建议把运维中枢门闸与（对上游 Agent / 云中枢的）交互封装编入同一 C++ 模块**，使：

1. 去掉/替换 Python 文件无法得到智能型/全能型中枢；  
2. 许可与中枢入口同信任边界（无令牌则拒绝）；  
3. 三专利与门闸同仓编译；上游 Hermes 仍按 MIT 保留声明，不混称为「自研闭源 Hermes」。

#### 3.3.1 建议纳入 C++ 的能力清单

| 能力域 | 建议放入 C++ 的内容 | 留在 Python 的内容 |
|--------|---------------------|-------------------|
| **许可** | 指纹、验签、试用计时、激活、会话令牌签发 | 展示剩余天数、激活 UI |
| **Hermes 会话** | ACP/`begin_turn` 编排状态机、续轮/回灌控制、取消与超时 | HTTP/WebSocket 进出站、SSE 推送 |
| **协议解析** | REMOTE_TOOL / 安全相关协议块解析与校验、危险参数剥离 | 展示用 Markdown 清洗（可复用开源） |
| **规划门闸** | 全能闭环：工具批调度决策、写工具允许条件、与 ExecTicket/策略令牌绑定 | 开源 L1 确认卡 UI；用户点允许后把结果回传 C++ |
| **记忆 / 技能（核心）** | 记忆检索索引结构、技能选择评分、上下文窗裁剪策略 | 大模型 HTTP 客户端（可薄封装）、提示词模板文件 |
| **专利 A/B/C** | 确认卡↔短票绑定、跨层链哈希、策略令牌派生 | 审计 JSONL 落盘（开源 audit 接口） |
| **防绕过** | 所有「调用 Hermes / 放行写工具」入口先 license_check + 令牌 | 无 |

**刻意不进 C++（或后置）**：完整 LLM SDK、前端、开源 TSM-A Demo、最小 SSH 后端——避免模块膨胀与跨平台编译地狱。

#### 3.3.2 信任边界与调用关系

```text
Python（assistant-pro / 终端 UI）
    │  FFI（ctypes / pybind11 / cffi）
    ▼
┌─────────────────────────────────────────────┐
│  pro_core.so / .dll / .pyd（闭源 C++）       │
│  ┌─────────────┐  ┌──────────────────────┐  │
│  │ license_*   │→│ omnipotent runtime   │  │
│  │ 试用/付费   │  │ Hermes turn 状态机   │  │
│  └─────────────┘  │ 协议解析 · 工具门闸  │  │
│                   │ patents A/B/C        │  │
│                   └──────────┬───────────┘  │
└──────────────────────────────┼──────────────┘
                               │ 仅持有效会话令牌时
                               ▼
                     Hermes Worker / 云端大脑
                               │
                               ▼
              开源 ops-bridge 执行器（SSH/WinRM）+ Pro connectors
`

关键点：

- **无有效许可 → omni_begin_turn 直接失败**，不存在「跳过 license 调 Hermes」的 Python 旁路。  
- 用户确认卡（开源 L1）批准后，Python 把 permission_id / 	typed_confirm 结果交给 C++；**短票与策略令牌由 C++ 签发**（专利 A/C）。  
- 审计事件由 C++ 产出规范记录，Python 调用开源 `audit.append` 落盘；链式字段（专利 B）仅 Pro 写入。

#### 3.3.3 试用期大脑放哪里（定稿建议：云端优先）

**结论**：**试用 7 天 → 运维中枢只在你们云端**；**付费后 → 可选继续云端，或下载本地闭源包（`pro_core` + 经许可的 Agent 运行时，含上游 MIT 声明）。**  
智能型与全能型共用（二者都依赖中枢；现网中枢可接 Hermes Agent）。

##### 方案对比

| | 方案一：试用云端（**推荐默认**） | 方案二：试用即本地二进制 |
|--|----------------------------------|---------------------------|
| 安装 | ops-bridge + Pro **空壳/薄客户端**（**不含**完整中枢/Agent 运行时） | 完整 Pro（`pro_core` + Agent 运行时 + 许可） |
| 试用时 | 智能型/全能型 → HTTPS 调**云端运维中枢** → 返回规划/工具意图 | 本地许可放行 7 天 |
| 试用结束 | 云端停响应 / 吊销试用 token | 本地 `license_check` 拒绝加载 |
| 付费后 | ①继续云端；或 ②下载本地闭源包并激活 | 输入激活码永久/年订解锁 |
| 大脑外泄风险 | **最低**（源码与大脑二进制不出机房） | 较高（二进制在客户机，靠混淆+许可） |
| 内网客户 | 试用须联网；付费后可转本地 | 试用即可离线 |
| 控制复杂度 | 云端开关即可，**试用阶段可不依赖坚固本地 DRM** | 必须做好指纹/反调试 |

##### 推荐路径（一句话）

> **试用期中枢放云端（中枢不出机房）；付费后可选继续云端，或下载闭源中枢包到本地（带着锁出门）。**

理由（采纳产品侧判断）：

1. 试用阶段信任最低、破解动机最高——云端最稳。  
2. 7 天联网成本可接受；连试用都不愿联网的，付费转化率通常也低。  
3. 付费后再给本地选项——内网客户有出路，且已付费，逆向动机下降。

##### 落地要点（避免空壳变后门）

1. **试用身份**：云端试用 token（账号或安装时领取的 trial_id），到期服务端拒绝，不只靠客户端诚实。  
2. **本地空壳职责**：TLS 调用、确认卡/执行走开源护栏；**不打包完整中枢/Agent Worker**。  
3. **付费转本地**：下发完整 `pro_core` + Agent 运行时（含上游 MIT NOTICE）+ 激活码绑指纹。  
4. **纯内网售前**：不走自助 7 天云试用，改为商务开通「离线试用包」（方案二特例）或现场 PoC。  
5. **C++ 模块分工**：试用默认路径以「云端大脑 + 薄客户端」为主；**完整 `pro_core`（本地 omni + license）是付费本地部署的主力**，不是试用必装件。

```text
试用（默认）：
  用户 → Pro 空壳 → HTTPS → 云端运维中枢（你们机房；可内含/调用 Hermes Agent）
                         → 工具意图回本地 → ops-bridge 执行 + 确认卡

付费-云端续用：同上，token 改为订阅

付费-本地部署：
  用户 → pro_core(C++) + 本地 Agent 运行时（许可解锁；上游 MIT 声明随包）
       → 可选连你们模型网关，或客户自备模型出口（商务条款另定）
```

#### 3.3.4 建议导出的 C API（示意）

```c
/* ---- 许可 ---- */
int         license_init(const char* license_file_path);
int         license_check(void);
int         license_trial_days_remaining(void);
int         license_activate(const char* activation_code);
const char* license_get_fingerprint(void);

/* ---- 全能型 / Hermes 交互核心 ---- */
/* 创建会话；内部先 license_check，失败返回空 */
void*       omni_session_open(const char* session_json, char* err, int err_len);
void        omni_session_close(void* session);

/* 推进一轮：入参含用户文本/确认结果/上轮工具结果；出参为助手增量+工具调用计划 JSON */
int         omni_begin_turn(void* session, const char* input_json,
                            char* out_json, int out_len);
int         omni_continue_turn(void* session, const char* tool_results_json,
                               char* out_json, int out_len);
int         omni_cancel_turn(void* session);

/* 许可通过后签发的短时令牌（供 Python 侧只读展示/缓存；真正校验在 C++） */
int         omni_export_session_token(void* session, char* token, int token_len);

/* 专利相关：确认批准 → 执行短票 */
int         omni_issue_exec_ticket(void* session, const char* confirm_json,
                                  char* ticket_out, int ticket_len);
`

Python 侧仅保留：

```python
# pro/python_shim — 无业务分支
from pro_core import omni_session_open, omni_begin_turn, license_trial_days_remaining
`

#### 3.3.5 与开源仓的边界

| 产品 | pro_core |
|------|------------|
| 开源版 | **完全不包含** C++ 二进制，也无 omni_* / license_* |
| Pro 版 | 随安装包分发对应平台的 pro_core（win-amd64 / linux-x64 等） |
| 升级路径 | 安装程序写入 pro/ + 本地库；启动时探测 pro_core 是否可加载 |

构建建议：CI 产出签名后的二进制；Python wheel 的 ssistant-pro **只含 shim + 数据**，核心 .pyd/.so 可同包或分平台包。

---

## 4. 闭源部分：掌上模式（SaaS）

### 4.1 定位

- **完全闭源**，SaaS 订阅运行  
- 手机 App / 微信小程序（及现网模拟 IM Demo 的产品化形态）  
- **不提供**与开源版捆绑的本地掌上安装包作为主交付  

### 4.2 架构

```text
用户手机 → App / 小程序
              ↓
         SaaS API 网关（HTTPS + WSS）
              ↓
     掌上模式后端（闭源）
     · 移动 API · IM（飞书/企微）· 多主机 Job
     · 企业策略 · 用户与计费 · 云端 Hermes 桥
              ↓
        目标主机集群（客户私网 / 经堡垒机）
```

### 4.3 与开源版 / Pro 的关系

| 维度 | 约定 |
|------|------|
| 代码 | **不共享**实现仓；不基于 ops-bridge 源码树交付 |
| 安装 | 用户无需在跳板机装掌上后端；SaaS 后台配置主机/堡垒通道 |
| 凭据 | SaaS **不落盘**客户主机密码；堡垒机或临时凭据通道 |
| 能力叙事 | TSM-A / 确认卡 / 审计等**概念**可与开源一致；实现与策略引擎闭源 |

> **现网说明**：当前 `Assistant/terminal/mobile/*` 是产品孵化与 Demo 单体。切仓后该树迁入 SaaS/Pro 侧，开源仓只保留边界评审规定的 Demo 编排薄层。

---

## 5. 依赖关系与版本管理

```text
终端开源版  ops-bridge v0.x
    ↑ pip 依赖
终端 Pro    assistant-pro v1.x
    ├── depends: ops-bridge >= 0.1, < 1.0
    ├── 闭源核心：pro_core（C++：运维中枢门闸 + 许可 + 专利）
    ├── 运行时依赖：上游 Hermes Agent（MIT，保留 NOTICE）或其他 Agent 后端
    ├── 闭源辅：tools_full / connectors / knowledge / fortress / python_shim
    └── 专利实现编译进 pro_core（源码不单独开源）

掌上 SaaS
    └── 独立代码库，不依赖 ops-bridge
```

| 规则 | 说明 |
|------|------|
| Major 锁定 | Pro 锁 ops-bridge major（如 `>=0.1,<1.0`） |
| 开源演进 | 开源 minor/patch 不破坏 Pro；破坏性变更走 major + Pro 适配 |
| SaaS | 独立迭代，不受开源版版本绑定 |

---

## 6. 三个专利申请点的归属

| 专利点 | 所属产品 | 开源版 | 说明 |
|--------|----------|--------|------|
| A：确认卡 → 短时凭据绑定 | 终端 Pro | ❌ 仅接口/流程开源 | 核心：`pro/patents/bind_confirm_to_token.*` |
| B：三层跨层联动审计链 | 终端 Pro | ❌ 仅基础 JSONL 开源 | 链式哈希：`pro/patents/cross_layer_chain.*` |
| C：运维命令级策略令牌 | 终端 Pro | ❌ 仅令牌结构开源 | 派生：`pro/patents/derive_constraints.*` |

开源用户可用确认卡、审计、密码保险箱；**密码学绑定与跨层链式审计**为 Pro 增值，保护新颖性。

现网已有能力对照（非专利实现本身）：

- 确认卡 + ExecTicket 短票 → 专利 A 的产品化前身  
- `turn_id`/`trace_id` + forensic → 专利 B 的可叙述底座（尚无链式哈希）  
- 策略/ACL + ticket 约束 → 专利 C 的产品化前身  

---

## 7. 参赛材料表述建议

### 7.1 开源 / 闭源策略

> 我们将终端模式基础能力（TSM-A 护栏 + 指令型 + 开源侧弱化分析 + 最小执行）以 **Apache 2.0** 开源为 **ops-bridge**。智能型/全能型所需的**运维中枢**作为商业 **Pro/SaaS**（试用默认云端中枢，付费可选本地 `pro_core` 包）。中枢可集成上游 **Hermes Agent（MIT）** 并保留声明——闭源的是中枢与许可，而非「独占开源 Hermes」。掌上模式完全闭源订阅。

### 7.2 商业模式

| 产品 | 模式 |
|------|------|
| 终端开源版 | 免费，社区支持；个人与小团队 |
| 终端 Pro | **试用默认云端运维中枢（7 天）**；付费后可选云订阅或本地 `pro_core` 包（指纹许可；上游 MIT 随包声明） |
| 掌上 SaaS | 按主机数或用户数订阅；企业 SLA |

### 7.3 不宜对外承诺的表述

- 「Hermes 整项目是我们的闭源大脑 / 我们收回了 Hermes」  
- 「开源版含智能型/全能型同款中枢 / 写工具全量」  
- 「Prompt Injection 已根治」（TSM-A：L1 软控制，L2/L3 兜底）  
- 「开源含专利链式哈希全文实现」  

---

## 8. 落地路线（相对当前单体仓）

| 阶段 | 动作 | 产出 |
|------|------|------|
| P0 | 按边界评审抽出 `packages/ops_bridge` + 开源 Demo 编排 | 可 clone 跑通的 ops-bridge |
| P1 | 定义 `pro_core` FFI；Python shim；先迁协议解析/门闸 | 可加载的 C++ 桩 |
| P2 | 云端试用 Hermes API + Pro 空壳；试用 token 到期关断 | 7 天云试用可演示 |
| P2b | 付费本地包：C++ `pro_core` + 本地 Hermes + `license_*` | 内网交付可演示 |
| P2.1 | 专利 A/B/C 编入同一二进制；写工具全量经 C++ 放行 | Pro 安全增值可讲 |
| P3 | 掌上后端独立仓 / SaaS 网关；本地 mobile Demo 降级为销售样机 | 掌上与终端安装解耦 |

细节冲突与文件清单以 [open-source-boundary-review.md](./open-source-boundary-review.md) 为准；本文件管 **产品分层与商业边界**。

---

## 9. 一句话收

整体架构分三层：**终端开源版（ops-bridge）→ 终端 Pro（闭源运维中枢 + 许可；可接上游 Hermes Agent·MIT；试用云端、付费可选本地）→ 掌上 SaaS**。握在手里的是中枢/专利/许可，不是「专有化整棵开源 Hermes」。

---

## 10. 修订历史

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-07-23 | v1.3 | 明确上游 Hermes=MIT；闭源口径=自有中枢/pro_core，禁「独占 Hermes」叙事 |
| 2026-07-23 | v1.1 | C++ pro_core：纳入全能型/Hermes 交互核心 + 许可 + 专利，不只做 license |
| 2026-07-23 | v1.0 | 首版：用户产品核设想结构化入库；对齐 TSM-A / 边界评审 / 现网模式名 |
