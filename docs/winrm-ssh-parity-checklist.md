# WinRM 与 SSH 体验对齐清单（闭环 / 网关 / 时间线）

目的：在 **`conn_type=winrm`** 与 **`conn_type=ssh`** 两条路径上，对 **闭环镜像、危险确认、修复时间线（SSE + WS）** 保持同一套产品语义，避免长期「Linux 一等、Windows 二等」的技术债。

**依赖分工（勿混淆）：**

| 组件 | 用途 |
|------|------|
| **`pywinpty`** | 仅 **本机 Windows** 交互终端（ConPTY）；与 WinRM 传输无关。 |
| **`chibycore/winrm_oneshot.py`** | **远端 WinRM** 单次执行（基于 **pypsrp / PSRP**），供闭环 REST/SSE 与计划步骤 oneshot。 |
| **`chibycore/ssh_oneshot.py`** | 远端 SSH 单次 exec。 |

---

## 1. 闭环镜像（capture mirror）

| 能力 | SSH | WinRM | 说明 |
|------|-----|-------|------|
| 每步写入 `SessionManager.append_output_capture` | ✅ 一致 | ✅ 一致 | `mirror_closure_step_to_session` / `mirror_closure_step_after_streaming` |
| 网关拒绝文案与 ANSI | ✅ 一致 | ✅ 一致 | `format_gateway_denial_capture`；与传输无关 |
| 页脚展示 `exit` / `transport` | ✅ `transport=ssh` | ✅ `transport=winrm` | `ClosurePayload` 来自 `ExecResult.transport`（见 `closure_capture_mirror.py`） |
| 流式镜像（SSE）io 上色 | ✅ 一致 | ✅ 一致 | `format_mirror_io_fragment`；stderr 黄色 |
| 流式页脚「本步输出见上文实时流」 | ✅ 一致 | ✅ 一致 | `format_mirror_step_footer_streaming` |
| LLM 修复管线 `shell_profile` | `unix` | `powershell` | **`terminal.shell_context.closure_shell_profile_for_remote_host`**（与 WinRM 会话上 `resolve_shell_profile` 结论一致） |

**验收要点：** 镜像正文中应出现 **`transport=winrm`** 或 **`transport=ssh`**，与执行器一致；WinRM 下闭环禁止默默退回「unix」修复提示。

**已知差异（非缺陷）：** WinRM oneshot 将用户脚本包进 Base64 + `__OPS_EXIT_CODE__` 行；镜像展示的是 PowerShell/包装后的有效输出，与 SSH 的裸 shell 输出格式可能不同，但 **镜像管线与事件类型应对齐**。

---

## 2. 执行网关（危险分层 / 拒绝）

| 能力 | SSH | WinRM | 说明 |
|------|-----|-------|------|
| `ExecutionRequest.conn_type` | `"ssh"` | `"winrm"` | `closure_rest` / 计划 oneshot 等路径使用 **`host.conn_type.value`** |
| 策略与审计 | ✅ 同一套 | ✅ 同一套 | `gateway_evaluate`；按命令行内容而非 OS 分支 |
| 高危二次确认（若产品已启用） | ✅ | ✅ | 计划状态机（如 `plan_danger`）与 `conn_type` 解耦；需在 WinRM 主机上回归「确认后才执行」 |

---

## 3. 修复时间线（repair timeline）

| 场景 | SSH | WinRM | 说明 |
|------|-----|-------|------|
| Host REST 闭环 | `POST .../hosts/{id}/closure-execute` | 同上 | `build_oneshot_from_pydantic_host` → Paramiko / WinRM |
| Host SSE 流 | `.../closure-execute/stream` | 同上 | `stream_chunk` + 镜像 queue；`cancel_check` 与 SSH 一致 |
| WS 计划步骤 oneshot（远端） | ✅ | ✅ | `_execute_plan_step_via_remote_closure`；`max_fix_attempts=0` 时仍为「网关 + 一步闭环」语义 |
| ReplayBundle `conn_type` 字段 | ✅ | ✅ | `ReplayBundleMeta.conn_type=host.conn_type.value` |

**配置差异（行为开关，非对齐缺口）：**

| 字段 | 影响 |
|------|------|
| `Host.winrm_shell_mode` | **交互式 WebSocket 终端**（`interactive` vs `psrp_line`）；不影响 **oneshot 闭环** 使用的 `WinRMOneShotExecutor`，但回归 WinRM 时应两种模式各测一轮交互体验。 |

---

## 4. 维护约定

1. **新增「远端 Host 闭环」入口时**：必须设置 **`shell_profile=closure_shell_profile_for_remote_host(host.conn_type)`**，且网关 **`conn_type=host.conn_type.value`**。
2. **新增镜像/SSE 事件类型时**：不得硬编码仅 SSH；格式函数放在 **`chibycore/closure_capture_mirror.py`** 等与传输无关处。
3. **CI**：保留无网络的契约测试（工厂、`shell_profile`、镜像页脚含 `transport`）；真实 WinRM 集成测可选另建 job。

---

## 5. 代码锚点（便于评审）

| 主题 | 路径 |
|------|------|
| oneshot 工厂 | `chibycore/unified_executor_factory.py` |
| 远端闭环 shell_profile | `terminal/shell_context.py` → `closure_shell_profile_for_remote_host` |
| Host closure / SSE | `terminal/main.py`（`closure-execute`、`closure-execute/stream`、计划 WS） |
| 镜像格式化 | `chibycore/closure_capture_mirror.py` |
| WinRM 单次执行 | `chibycore/winrm_oneshot.py` |
