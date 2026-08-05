"""大模型代理：通过 litellm 调用，强制 JSON 输出（根因 / 修正命令 / 风险）。"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Optional

from .knowledge_base import RemediationKnowledgeBase
from .models import (
    EnvironmentSnapshot,
    LLMRemediationJSON,
    RemediationHistory,
    StructuredError,
)

logger = logging.getLogger(__name__)


def _litellm_completion(**kwargs: Any) -> Any:
    """延迟导入 litellm：避免仅 import remediator 就触发新版 litellm 在 Python 3.8 下的 pydantic 兼容问题。"""
    try:
        from litellm import completion as litellm_completion
    except Exception as e:
        raise RuntimeError(
            "无法加载 litellm（常见于 Python 3.8 + 新版 litellm/pydantic 不兼容，例如 proxy 模型里 InputAudio 等前向引用）。"
            "建议：① 使用 Python 3.10+；② 或尝试降级：pip install \"litellm>=1.40,<1.53\"；③ 升级/对齐 pydantic 版本。"
            f" 原始错误: {e!r}"
        ) from e
    return litellm_completion(**kwargs)


def _httpx_openai_compat_completion(**kwargs: Any) -> Any:
    """OpenAI 风格 ``/chat/completions``，不依赖 litellm（旧版 Python / litellm 无法导入时回退）。"""
    import httpx

    model = (kwargs.get("model") or "").strip() or "gpt-4o-mini"
    messages = kwargs.get("messages") or []
    temperature = float(kwargs.get("temperature", 0.2))
    api_key = (kwargs.get("api_key") or "").strip()
    api_base = (kwargs.get("api_base") or "").strip()

    mlow = model.lower()
    api_model = model

    if api_base:
        url = f"{api_base.rstrip('/')}/chat/completions"
    elif mlow.startswith("deepseek/"):
        url = "https://api.deepseek.com/chat/completions"
        api_model = model.split("/", 1)[1].strip()
    elif mlow.startswith("openai/"):
        url = "https://api.openai.com/v1/chat/completions"
        api_model = model.split("/", 1)[1].strip()
    elif mlow.startswith("gpt-"):
        url = "https://api.openai.com/v1/chat/completions"
    else:
        raise RuntimeError(
            "litellm 不可用时仅支持：自定义 api_base，或以 deepseek/、openai/、gpt- 开头的 model。"
            f" 当前 model={model!r}"
        )

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": api_model,
        "messages": messages,
        "temperature": temperature,
    }

    to = httpx.Timeout(connect=30.0, read=120.0, write=60.0, pool=30.0)
    with httpx.Client(timeout=to) as client:
        r = client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        return r.json()


def _chat_completions_response(**kwargs: Any) -> Any:
    """优先 litellm；仅在「无法导入 litellm」时用 httpx。"""
    try:
        return _litellm_completion(**kwargs)
    except RuntimeError as e:
        err = str(e)
        if "无法加载 litellm" in err:
            logger.warning("litellm 未加载，回退 httpx 直连: %s", e)
            return _httpx_openai_compat_completion(**kwargs)
        raise


# 与 LLMRemediationJSON 字段一一对应；禁止新增键。
_SCHEMA_KEYS = (
    "root_cause",
    "fixed_command",
    "risk_warning",
    "requires_precheck_script",
    "notes",
    "confidence_score",
)

SYSTEM_PROMPT = """你是资深 Linux/Unix 运维工程师，根据「当前错误 + 环境 + 修正历史」给出可执行的修正方案。

【输出格式 — 强制】
1. 仅输出一个 JSON 对象；禁止 Markdown、代码围栏、注释、中文解释段落、前后缀文字。
2. JSON 键必须且只能包含这 5 个键（与 LLMRemediationJSON 完全一致）：
   root_cause, fixed_command, risk_warning, requires_precheck_script, notes
3. 键名使用英文双引号；字符串内的换行写为 \\n；布尔值为 true/false。
4. fixed_command 可以是单行 shell，或多行脚本（行与行之间用 \\n 连接在同一字符串内）。

【场景 A — 环境动态变化：前置检查 — 必须遵守】
若修正涉及以下任一操作，必须用条件判断包裹（bash），禁止裸执行：
- 删除文件或目录（rm、unlink、shred 等）
- 覆盖写入已有文件（>、tee、mv 覆盖目标）
- kill / killall / systemctl stop / service stop / docker stop 等停止或杀进程

规范示例（删除文件）：
{"fixed_command":"if [ -f /tmp/app.log ]; then\\n  sudo rm -f /tmp/app.log\\nelse\\n  echo \"file not exists\"\\nfi","requires_precheck_script":true,...}

目录用 [ -d ... ]，任意路径存在性优先用 [ -e ... ]。必须在 risk_warning 中说明破坏性风险。
若「重试间隙目标可能已被其他进程创建/删除」，必须设置 requires_precheck_script=true，并在 fixed_command 内体现存在性检查后再动作。

【场景 B — 多命令依赖：联动修复 — 必须遵守】
若用户原始命令或 metadata 中的命令链包含链式运算符：&& 、分号 ; 、管道 | ：
- 结合 stderr 与历史，定位「第一个失败点」对应的子命令。
- fixed_command 必须输出「整条修正后的命令链」（从链首到链尾完整一串），不能只修最后一个片段；其余未失败段保持合理原样或做必要同步调整。
- 在 root_cause 中简要说明第一个失败点及联动修改思路。

【requires_package 优先 — 必须遵守】
若 Current Error 中 Requires Package 非「(none)」：
- fixed_command 必须先安排安装（按 OS 选择 apt-get/apt、yum、dnf、apk、brew 等之一），再执行原任务命令或链。
- 示例：Requires Package 为 maven 时，应包含类似：
  sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y maven
  或（RHEL 系）sudo yum install -y maven
  再用 && 衔接后续原任务；安装与任务必须在同一 fixed_command 字符串内用 \\n 或 && 组织。

【风险】
凡 sudo、rm -rf、修改 /etc、覆盖生产文件，须在 risk_warning 明确警示。

【历史】
必须阅读 History，不得重复已失败过的同一修正命令或等价脚本。

【自检】
输出前自查：只有一个 JSON 对象；五个键齐全；fixed_command 可被 bash -c 或粘贴进终端执行。
"""


def _extract_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("空响应")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    dec = json.JSONDecoder()
    try:
        obj, _ = dec.raw_decode(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise
        obj, _ = dec.raw_decode(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("JSON 顶层必须是对象")
    return obj


def _build_user_prompt(
    structured_error: StructuredError,
    history: RemediationHistory,
    env: EnvironmentSnapshot,
) -> str:
    """Phase 3：显式 Environment / Current Error / History 文本块。"""
    err_type = structured_error.error_category.value
    req_pkg = structured_error.requires_package
    req_pkg_line = req_pkg if req_pkg else "(none)"
    stderr_text = (structured_error.stderr_snippet or structured_error.raw_stderr or "").strip()
    if len(stderr_text) > 6000:
        stderr_text = stderr_text[:5997] + "..."
    hist_text = history.to_prompt_string()
    if not hist_text.strip():
        hist_text = "(none)"
    orig_cmd = (structured_error.metadata.get("command") or "").strip()

    blocks = [
        "Environment:",
        f"- OS: {env.os_name} {env.os_version}".strip(),
        f"- CWD: {env.cwd}",
        f"- USER: {env.current_user}",
        "",
        "Current Error:",
        f"- Type: {err_type}",
        f"- Requires Package: {req_pkg_line}",
        f"- Stderr: {stderr_text}",
    ]
    if orig_cmd:
        blocks.extend(["", f"- Original command: {orig_cmd}"])
    if orig_cmd and any(op in orig_cmd for op in ("&&", ";", "|")):
        blocks.append(
            "Note: command chain detected (&& / ; / |) — fix from first failing segment; "
            "output the full corrected chain in fixed_command."
        )
    blocks.extend(["", "History:", hist_text])
    return "\n".join(blocks).strip()


def propose_remediation(
    structured_error: StructuredError,
    history: RemediationHistory,
    env: EnvironmentSnapshot,
    *,
    model: str = "gpt-4o-mini",
    api_base: Optional[str] = None,
    api_key: Optional[str] = None,
    extra_litellm_kwargs: Optional[dict[str, Any]] = None,
    knowledge_base: Optional[RemediationKnowledgeBase] = None,
    prompt_suffix: Optional[str] = None,
) -> LLMRemediationJSON:
    """
    第三步：调用大模型，返回校验后的 LLMRemediationJSON。

    ``knowledge_base``：若提供，则对「模型 + system + user prompt」做 SHA256 缓存（24h TTL），
    仅影响单次 LLM 调用的输入输出，不改变 Controller 历史链语义。

    ``prompt_suffix``：追加在 user prompt 末尾（如 few-shot / 向量 KB 摘要）；参与缓存 key。
    """
    user_text = _build_user_prompt(structured_error, history, env)
    suf = (prompt_suffix or "").strip()
    if suf:
        user_text = user_text + "\n\n" + suf

    cache_material = json.dumps(
        {"model": model, "system": SYSTEM_PROMPT, "user": user_text},
        ensure_ascii=False,
        sort_keys=True,
    )
    prompt_hash = hashlib.sha256(cache_material.encode("utf-8")).hexdigest()

    if knowledge_base is not None:
        cached_raw = knowledge_base.get_llm_response_cache(prompt_hash)
        if cached_raw:
            try:
                raw = json.loads(cached_raw)
                merged = {
                    "root_cause": raw.get("root_cause") or "",
                    "fixed_command": raw.get("fixed_command") or "",
                    "risk_warning": raw.get("risk_warning") or "",
                    "requires_precheck_script": bool(raw.get("requires_precheck_script")),
                    "notes": raw.get("notes") or "",
                }
                return LLMRemediationJSON.model_validate(merged)
            except Exception as e:
                logger.warning("LLM 缓存命中但解析失败，回退实时调用: %s", e)

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0.2,
    }
    if api_base:
        kwargs["api_base"] = api_base
    if api_key:
        kwargs["api_key"] = api_key
    if extra_litellm_kwargs:
        kwargs.update(extra_litellm_kwargs)

    resp = _chat_completions_response(**kwargs)
    content = ""
    try:
        content = resp["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        logger.exception("litellm 响应结构异常: %s", e)
        raise RuntimeError("LLM 响应无效") from e

    raw = _extract_json_object(content)
    extra_keys = set(raw.keys()) - set(_SCHEMA_KEYS)
    if extra_keys:
        logger.warning("LLM 返回多余 JSON 键（已忽略）: %s", extra_keys)
    merged = {
        "root_cause": raw.get("root_cause") or "",
        "fixed_command": raw.get("fixed_command") or "",
        "risk_warning": raw.get("risk_warning") or "",
        "requires_precheck_script": bool(raw.get("requires_precheck_script")),
        "notes": raw.get("notes") or "",
        "confidence_score": float(raw["confidence_score"])
        if raw.get("confidence_score") is not None
        else 0.5,
    }
    if knowledge_base is not None:
        try:
            knowledge_base.put_llm_response_cache(
                prompt_hash, json.dumps(merged, ensure_ascii=False)
            )
        except Exception as e:
            logger.warning("LLM 缓存写入失败（已忽略）: %s", e)
    return LLMRemediationJSON.model_validate(merged)
