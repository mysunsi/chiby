"""LLM 编排器 — Phase 2 核心。

三层决策：
  1. 意图路由：链匹配优先，LLM 作为复杂指令的第二选择
  2. 任务分解：LLM 将复杂指令拆解为多个可执行步骤
  3. 调试循环：执行失败 → LLM 分析错误 → 修正 → 重试（最多2轮）

当没有可用 LLM 时，所有功能优雅降级到规则引擎。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .chains import ChainPlanner, TaskChain, TASK_CHAINS
from .config import MAX_RETRIES
from .llm_providers import get_llm
from .parser import ActionType

logger = logging.getLogger(__name__)

# ─── 系统提示词 ───────────────────────────────────────────────────────────────

SYS_INTENT = """你是一个运维助手命令解析器。用户输入一句自然语言运维指令，你需要判断它的意图并返回 JSON。

## 判断规则
- 如果指令可以用**单个**运维操作完成（如：查看内存、创建用户、安装软件），返回 type="single"
- 如果指令包含**多个独立操作**或**顺序步骤**（如：先安装 nginx 再启动、再检查），返回 type="decompose"
- 如果指令涉及**复杂编排**（跨主机、多服务协同、依赖判断），返回 type="decompose"

## 支持的操作类型
{action_types}

## 指令示例
输入：帮我看看内存占用
输出：{{"type": "single", "action": "memory_usage"}}

输入：帮我创建一个运维账号 testop，密码 Test@123
输出：{{"type": "single", "action": "create_user", "params": {{"username": "testop", "password": "Test@123"}}}}

输入：帮我部署 nginx，然后启动它
输出：{{"type": "decompose", "steps": [{{"action": "package_install", "params": {{"package_name": "nginx"}}}}, {{"action": "service_start", "params": {{"service_name": "nginx"}}}}]}}

输入：检查服务器负载情况，如果 CPU 超过 80% 就报警
输出：{{"type": "decompose", "steps": [{{"action": "cpu_usage"}}, {{"action": "netstat"}}]}}
"""


SYS_STEP_DECOMPOSER = """你是一个运维命令分解器。用户输入一条复杂运维指令，需要拆解成多个**顺序执行**的步骤。

## 规则
- 每个步骤必须是 {action_types} 之一
- 步骤之间有依赖关系时，用 depends_on 指明
- 同组操作无依赖时，parallel_group 设为相同值
- 如果指令简单直接，返回单步骤

## 输入
{user_command}

## 输出
如果单步骤：
{{"type": "single", "action": "...", "params": {{}}}}
如果需要多步骤：
{{"type": "decompose", "steps": [{{"action": "...", "params": {{}}, "depends_on": [], "parallel_group": null}}]}}
"""


SYS_ERROR_ANALYZER = """你是一个运维故障诊断专家。运维命令执行失败了，请分析原因并给出修正方案。

## 失败信息
- command: {failed_command}
- stderr: {stderr}
- exit_code: {exit_code}

## 支持的操作
{action_types}

## 输出
{{
  "analysis": "失败原因分析（50字以内）",
  "suggestion": "修正建议",
  "corrected_command": "修正后的命令（如果命令问题）",
  "action_hint": "建议尝试的操作类型（如果操作类型错误）"
}}
"""


def _get_action_types_str() -> str:
    types = [f'"{a.value}": {a.name}' for a in ActionType]
    return "\n".join(types)


# ─── 意图路由 ─────────────────────────────────────────────────────────────────

async def route_intent(user_command: str) -> Dict[str, Any]:
    """判断用户意图，决定使用规则链还是 LLM 分解。

    返回格式：
      type="chain":  {chain_name, chain, params}
      type="single": {action, params}
      type="decompose": {steps: [{action, params, depends_on, parallel_group}]}
    """
    command_lower = user_command.lower()

    # ── Step 1: 规则链匹配（最快）───────────────────────────────────────────
    planner = ChainPlanner()
    chain, params = planner.match_chain(user_command)
    if chain is not None:
        logger.info(f"意图路由 → 任务链: {chain.name}")
        return {"type": "chain", "chain_name": chain.name, "chain": chain, "params": params}

    # ── Step 2: 单步规则匹配（parser）────────────────────────────────────────
    from .parser import parse_command
    action, params2 = parse_command(user_command)
    if action != ActionType.UNKNOWN:
        logger.info(f"意图路由 → 单步: {action.value}")
        return {"type": "single", "action": action, "params": params2}

    # ── Step 3: LLM 意图判断（无规则匹配时）─────────────────────────────────
    llm = get_llm()
    if not llm.is_available:
        logger.warning("意图路由: 无 LLM 可用，返回 unknown")
        return {"type": "unknown", "action": ActionType.UNKNOWN, "params": {}}

    messages = [
        {"role": "system", "content": SYS_INTENT.format(action_types=_get_action_types_str())},
        {"role": "user", "content": user_command},
    ]

    try:
        result = llm.chat_json(messages, temperature=0.1, max_tokens=512)
    except Exception as e:
        logger.warning("意图路由 LLM 调用失败: %s", e)
        return {"type": "unknown", "action": ActionType.UNKNOWN, "params": {}}
    if not result:
        return {"type": "unknown", "action": ActionType.UNKNOWN, "params": {}}

    intent_type = result.get("type", "unknown")
    if intent_type == "single":
        action_str = result.get("action", "unknown")
        try:
            action_enum = ActionType(action_str)
        except ValueError:
            action_enum = ActionType.UNKNOWN
        params3 = result.get("params", {})
        logger.info(f"意图路由 → LLM单步: {action_str}")
        return {"type": "single", "action": action_enum, "params": params3}

    if intent_type == "decompose":
        steps = result.get("steps", [])
        parsed_steps = []
        for s in steps:
            act_str = s.get("action", "unknown")
            try:
                act_enum = ActionType(act_str)
            except ValueError:
                act_enum = ActionType.UNKNOWN
            parsed_steps.append({
                "action": act_enum,
                "params": s.get("params", {}),
                "depends_on": s.get("depends_on", []),
                "parallel_group": s.get("parallel_group"),
            })
        logger.info(f"意图路由 → LLM分解: {len(parsed_steps)} 步")
        return {"type": "decompose", "steps": parsed_steps}

    return {"type": "unknown", "action": ActionType.UNKNOWN, "params": {}}


# ─── 调试循环（LLM 分析失败 → 修正 → 重试）────────────────────────────────────

async def analyze_failure(
    failed_command: str,
    stderr: str,
    exit_code: int,
) -> Dict[str, Any]:
    """执行失败后，LLM 分析原因并给出修正建议。"""
    llm = get_llm()
    if not llm.is_available:
        return {
            "analysis": "（无 LLM 可用，无法自动分析）",
            "suggestion": "请手动检查命令和错误信息",
            "corrected_command": None,
            "action_hint": None,
        }

    messages = [
        {
            "role": "system",
            "content": SYS_ERROR_ANALYZER.format(
                failed_command=failed_command,
                stderr=stderr[:500],
                exit_code=exit_code,
                action_types=_get_action_types_str(),
            ),
        },
        {
            "role": "user",
            "content": f"命令 '{failed_command}' 执行失败，stderr: {stderr[:300]}，exit_code={exit_code}。"
                        "请分析原因并给出修正方案。"
        },
    ]

    try:
        result = llm.chat_json(messages, temperature=0.2, max_tokens=512)
    except Exception as e:
        logger.warning("失败分析 LLM 调用失败: %s", e)
        return {
            "analysis": f"LLM 调用失败: {e}",
            "suggestion": "请手动检查命令和错误信息",
            "corrected_command": None,
            "action_hint": None,
        }
    return {
        "analysis": result.get("analysis", "未知原因"),
        "suggestion": result.get("suggestion", ""),
        "corrected_command": result.get("corrected_command"),
        "action_hint": result.get("action_hint"),
    }


# ─── 执行结果结构 ───────────────────────────────────────────────────────────────

class ExecutionResult:
    """执行结果（链和单步通用）。"""

    def __init__(
        self,
        success: bool,
        action: ActionType,
        params: Dict[str, Any],
        steps: Optional[List[Dict]] = None,
        error_analysis: Optional[Dict] = None,
        retry_count: int = 0,
        output: str = "",
    ):
        self.success = success
        self.action = action
        self.params = params
        self.steps = steps or []
        self.error_analysis = error_analysis
        self.retry_count = retry_count
        self.output = output

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "action": self.action.value,
            "params": self.params,
            "steps": self.steps,
            "error_analysis": self.error_analysis,
            "retry_count": self.retry_count,
            "output": self.output[:1000],
        }
