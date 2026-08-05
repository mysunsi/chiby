"""命令结束后：将闭环包交给 LLM 判定业务是否成功（非仅依赖 exit code）。"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

from chibycore.closure_service import ClosurePayload, success_for_closure
from chibycore.llm_providers import get_llm, strip_model_thinking_output
from chibycore.output_budget import CLOSURE_STDIO_TAIL_CHARS

logger = logging.getLogger(__name__)

JUDGE_SYSTEM = """你是运维执行结果审计员。根据一次命令的退出码与终端输出片段，判断**从运维意图角度是否可视为成功**。

注意：
- exit_code=0 仍可能语义失败（如删错文件前的空输出、或工具打印了错误到 stdout）。
- exit_code!=0 也可能在「预期失败探测」场景下算成功（较少）；请谨慎。

只输出合法 JSON，不要 markdown 围栏，不要其它文字：
{"success": true|false, "reason": "一句话中文" }
"""


def _parse_judge_json(text: str) -> Optional[Dict[str, Any]]:
    s = strip_model_thinking_output((text or "").strip())
    if not s:
        return None
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", s, re.I)
    if m:
        s = m.group(1).strip()
    try:
        o = json.loads(s)
        return o if isinstance(o, dict) else None
    except json.JSONDecodeError:
        pass
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        try:
            o = json.loads(s[i : j + 1])
            return o if isinstance(o, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def llm_judge_closure_outcome(cp: ClosurePayload) -> Tuple[bool, str]:
    """
    Returns:
        (success, reason). LLM 不可用时回退为 exit_code 启发。
    """
    from chibycore.closure_labels import humanize_judge_reason

    mgr = get_llm()
    if not mgr.is_available:
        ok = success_for_closure(cp)
        return ok, humanize_judge_reason("llm_unavailable_exit_code_fallback")

    user = json.dumps(
        {
            "raw_command": (cp.raw_command or "")[:1200],
            "effective_command": (cp.effective_command or "")[:1200],
            "transport": cp.transport,
            "risk_level": getattr(cp.risk_level, "value", str(cp.risk_level)),
            "exit_code": cp.exit_code,
            "stdout_tail": (cp.stdout or "")[-CLOSURE_STDIO_TAIL_CHARS:],
            "stderr_tail": (cp.stderr or "")[-CLOSURE_STDIO_TAIL_CHARS:],
            "nl_intent_hint": (cp.nl_intent_hint or "")[:800],
        },
        ensure_ascii=False,
    )
    try:
        text = mgr.chat(
            [
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=256,
        )
    except Exception as ex:  # pragma: no cover
        logger.warning("closure_judge: %s", ex)
        ok = success_for_closure(cp)
        return ok, humanize_judge_reason(f"llm_error_fallback:{ex}")

    obj = _parse_judge_json(text or "")
    if not obj or "success" not in obj:
        ok = success_for_closure(cp)
        return ok, humanize_judge_reason("llm_parse_fail_exit_code_fallback")
    succ = bool(obj.get("success"))
    reason = str(obj.get("reason") or "").strip() or "智能判定"
    return succ, reason
