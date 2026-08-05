"""开源原生：命令输出 → Markdown 结果说明。

供 Web 终端 ``explain-output`` / LLM 执行后梳理使用；不依赖闭源编排包。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)

_EXPLAIN_SYSTEM = """你是运维助手，面向不太懂技术的用户写结果说明。
根据「用户问题」和「命令执行结果」，输出简洁中文 Markdown（不要代码围栏包裹全文）。
不要写「结果梳理」之类标题；直接从结论开始。
结构：
1. 第一行用粗体给直接结论（回答用户问题）
2. 关键发现：能用表格汇总的优先用 Markdown 表格；只有一两句时用 2～5 条列表
3. **后续建议（可选）**：仅当确有异常/风险/信息不足时给 1～2 条；一切正常则不要写
硬性要求：
- 只依据给定结果，禁止编造
- 若用户意图与实际命令无关，必须明确「本次未执行该操作」
- 不要粘贴大段原始输出；不要输出 OPS_PLAN / JSON
- 全文一般不超过 420 字；多段巡检可到约 700 字
"""


@dataclass
class CommandExecSnapshot:
    """单次命令执行快照（开源契约，不依赖闭源 ExecResult）。"""

    ok: bool
    command: str = ""
    exit_code: int = 0
    stdout_tail: str = ""
    stderr_tail: str = ""
    error: str = ""
    host_id: str = ""


def _sanitize_text(text: str) -> str:
    # 开源路径不依赖闭源 text_clean；仅做基础 strip
    return (text or "").strip()


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][0-9A-Za-z]")


def _strip_ansi(text: str) -> str:
    """结果说明用：去掉颜色/标题序列，避免污染规则解析与 LLM 输入。"""
    if not text:
        return ""
    return _ANSI_RE.sub("", text)


def _trim_incomplete_md(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    bad_tails = (
        "本次命令输出",
        "命令输出",
        "原始输出",
        "完整输出",
        "如下所示",
        "如下：",
        "如下:",
    )
    lines = s.splitlines()
    while lines:
        last = lines[-1].strip()
        if not last:
            lines.pop()
            continue
        plain = re.sub(r"^#+\s*", "", last)
        plain = re.sub(r"^\*\*|^\*+|^\-\s*|^\+\s*", "", plain).strip(" ：:.-*")
        if plain in bad_tails or last.rstrip("：:") in bad_tails:
            lines.pop()
            continue
        if re.match(r"^[-*+]\s*$", last) or last in ("-", "*", "·", "…", "..."):
            lines.pop()
            continue
        break
    return "\n".join(lines).strip()


def _parse_free_h_md(tail: str, *, user_question: str = "") -> str:
    """从 ``free -h`` 输出提炼内存结论（无 LLM 时的可用回退）。"""
    text = _strip_ansi(tail or "")
    # Mem: total used free shared buff/cache available
    m = re.search(
        r"(?im)^\s*Mem:\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$",
        text,
    )
    if not m:
        return ""
    total, used, free, _shared, buff, avail = m.groups()
    sw = re.search(r"(?im)^\s*Swap:\s+(\S+)\s+(\S+)\s+(\S+)\s*$", text)
    swap_bits = ""
    if sw:
        st, su, sf = sw.groups()
        swap_bits = f"- Swap：总量 {st}，已用 {su}，剩余 {sf}"

    def _to_mib(token: str) -> Optional[float]:
        t = (token or "").strip().lower().replace(",", "")
        mm = re.match(r"^([0-9]*\.?[0-9]+)\s*([kmgt]i?b?|b)?$", t)
        if not mm:
            return None
        try:
            n = float(mm.group(1))
        except ValueError:
            return None
        u = (mm.group(2) or "b").lower()
        mult = 1.0
        if u.startswith("k"):
            mult = 1.0 / 1024.0
        elif u.startswith("m"):
            mult = 1.0
        elif u.startswith("g"):
            mult = 1024.0
        elif u.startswith("t"):
            mult = 1024.0 * 1024.0
        return n * mult

    avail_m = _to_mib(avail)
    total_m = _to_mib(total)
    pressure = "正常"
    if avail_m is not None and total_m and total_m > 0:
        ratio = avail_m / total_m
        if ratio < 0.12:
            pressure = "偏紧"
        elif ratio < 0.25:
            pressure = "略紧"
        elif ratio > 0.5:
            pressure = "充裕"

    q = (user_question or "").strip()
    if any(k in q for k in ("内存", "記憶體", "memory", "mem", "RAM", "ram")):
        conclusion = f"**结论：内存{pressure}——可用约 {avail}（总量 {total}）。**"
    else:
        conclusion = f"**结论：内存{pressure}（可用 {avail} / 总量 {total}）。**"

    parts = [
        conclusion,
        "",
        f"- 物理内存：总量 {total}，已用 {used}，空闲 {free}，缓存/缓冲 {buff}，**可用 {avail}**",
    ]
    if swap_bits:
        parts.append(swap_bits)
    if pressure in ("偏紧", "略紧"):
        parts.append("- **建议**：关注占用较高的进程；必要时释放缓存或扩容，避免 OOM。")
    return "\n".join(parts)


def _rule_domain_explain(
    *,
    command: str,
    output_tail: str,
    user_question: str = "",
) -> str:
    """常见运维命令的规则解读；无法识别时返回空串。"""
    cmd = (command or "").strip().lower()
    tail = _strip_ansi(output_tail or "")
    if not tail.strip():
        return ""
    if re.search(r"(^|[;&|\s])free(\s|$)", cmd) or re.search(
        r"(?im)^\s*Mem:\s+\S+\s+\S+", tail
    ):
        md = _parse_free_h_md(tail, user_question=user_question)
        if md:
            return md
    return ""


def rule_explain_fallback(
    *,
    command: str = "",
    output_tail: str = "",
    status: str = "unknown",
    exit_code: Optional[int] = None,
    user_question: str = "",
) -> str:
    """无 LLM 时的结构化 Markdown 说明。"""
    cmd = (command or "").strip()
    tail = _strip_ansi(output_tail or "").strip()[:4000]
    # 去掉末尾 shell 提示符行，减少噪音
    tail = re.sub(
        r"(?m)^.*[@].*[:$#]\s*$",
        "",
        tail,
    ).strip()
    q = (user_question or "").strip()
    st = (status or "unknown").strip().lower()

    domain = _rule_domain_explain(command=cmd, output_tail=tail, user_question=q)
    if domain and st in ("pass", "unknown", ""):
        return domain

    if st == "pass":
        conclusion = "**结论：命令已成功执行。**"
    elif st == "fail":
        conclusion = "**结论：命令执行未成功。**"
    else:
        conclusion = "**结论：命令已执行完毕。**"
    parts = [conclusion, "", f"- **命令**：`{cmd or '(空)'}`", f"- **状态**：{status}"]
    if exit_code is not None:
        parts.append(f"- **退出码**：{exit_code}")
    if q:
        parts.append(f"- **问题**：{q}")
    if tail:
        parts.append("")
        parts.append("**输出摘要**：")
        parts.append("```")
        parts.append(tail[:1200])
        parts.append("```")
    else:
        parts.append("- 无控制台输出可展示。")
    return "\n".join(parts)


def rule_explain_snapshots(results: Sequence[CommandExecSnapshot]) -> str:
    bits: List[str] = []
    for er in results or []:
        if er.ok:
            bits.append(f"结论：`{(er.command or '')[:80]}` 已成功执行")
        else:
            bits.append(
                f"结论：`{(er.command or '')[:80]}` 执行失败"
                + (f"（{er.error}）" if er.error else "")
            )
    if not bits:
        return (
            "**结论：命令已执行完毕。**\n\n"
            "- 请展开上方「命令输出」查看原始内容。"
        )
    if len(bits) == 1:
        msg = bits[0]
        return f"**{msg}**" if msg.startswith("结论") else f"**结论：{msg}**"
    return "**结论：已完成多项检查。**\n\n" + "\n".join(f"- {b}" for b in bits)


def call_llm_explain(
    *,
    user_question: str,
    host_label: str,
    results: Sequence[CommandExecSnapshot],
    ui_locale: str = "zh-CN",
) -> str:
    """LLM 梳理；不可用时返回空串。"""
    if not results:
        return ""
    try:
        from chibycore.llm_config import get_effective_llm_settings
        from chibycore.llm_providers import get_llm
        from chibyterm.ui_locale import ai_language_instruction, normalize_ui_locale

        llm = get_llm()
        if llm is None or not getattr(llm, "is_available", False):
            logger.info("llm explain skipped: no available provider")
            return ""
        ui_locale = normalize_ui_locale(ui_locale)
        no_think = bool(get_effective_llm_settings().get("no_think", True))
    except Exception as exc:
        logger.warning("llm explain init failed: %s", exc)
        return ""

    blocks: List[str] = []
    for i, er in enumerate(list(results)[:8], 1):
        if ui_locale == "en":
            status = (
                "Succeeded"
                if er.ok and int(er.exit_code or 0) == 0
                else f"Failed ({er.exit_code})"
            )
            empty_ok, empty_bad = "(no console output)", (er.error or "(no output)")
            hdr = f"### Command {i}"
            lab_cmd, lab_st, lab_out = "Command", "Status", "Output"
        elif ui_locale == "zh-TW":
            status = (
                "已成功執行"
                if er.ok and int(er.exit_code or 0) == 0
                else f"執行失敗({er.exit_code})"
            )
            empty_ok, empty_bad = "（無控制台輸出）", (er.error or "（無輸出）")
            hdr = f"### 命令 {i}"
            lab_cmd, lab_st, lab_out = "命令", "狀態", "輸出"
        else:
            status = (
                "已成功执行"
                if er.ok and int(er.exit_code or 0) == 0
                else f"执行失败({er.exit_code})"
            )
            empty_ok, empty_bad = "（无控制台输出）", (er.error or "（无输出）")
            hdr = f"### 命令 {i}"
            lab_cmd, lab_st, lab_out = "命令", "状态", "输出"
        body = _strip_ansi(er.stdout_tail or "").strip()[:4200]
        if not body and er.stderr_tail:
            body = _strip_ansi(er.stderr_tail or "")[:2000]
        if not body:
            body = empty_ok if er.ok else empty_bad
        # 去掉尾部提示符行
        body = re.sub(r"(?m)^.*[@].*[:$#]\s*$", "", body).strip() or body
        blocks.append(
            f"{hdr}\n"
            f"- {lab_cmd}：`{(er.command or '').strip()[:300]}`\n"
            f"- {lab_st}：{status}\n"
            f"- {lab_out}：\n```\n{body}\n```"
        )
    if ui_locale == "en":
        user_msg = (
            f"User question: {(user_question or '').strip() or '(none; summarize from results)'}\n"
            f"Target host: {host_label or '(unknown)'}\n\n"
            + "\n\n".join(blocks)
        )
    elif ui_locale == "zh-TW":
        user_msg = (
            f"使用者問題：{(user_question or '').strip() or '（未提供原問，請按命令結果概括）'}\n"
            f"目標主機：{host_label or '（未知）'}\n\n"
            + "\n\n".join(blocks)
        )
    else:
        user_msg = (
            f"用户问题：{(user_question or '').strip() or '（未提供原问，请按命令结果概括）'}\n"
            f"目标主机：{host_label or '（未知）'}\n\n"
            + "\n\n".join(blocks)
        )
    try:
        text = llm.chat(
            [
                {
                    "role": "system",
                    "content": _EXPLAIN_SYSTEM + ai_language_instruction(ui_locale),
                },
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
            max_tokens=1024,
            no_think=no_think,
        )
    except Exception as exc:
        logger.warning("llm explain failed: %s", exc)
        return ""
    s = _sanitize_text(text or "").strip()
    if not s:
        logger.warning(
            "llm explain empty reply (provider=%s)",
            getattr(llm, "active_name", "?"),
        )
        return ""
    fence = re.match(r"^```(?:markdown|md)?\s*([\s\S]*?)```\s*$", s, re.I)
    if fence:
        s = fence.group(1).strip()
    # 去掉偶发协议块（不依赖闭源协议解析器）
    s = re.sub(
        r"<<<OPS_PLAN>>>[\s\S]*?<<<END_OPS_PLAN>>>",
        "",
        s,
        flags=re.I,
    ).strip()
    return _trim_incomplete_md(s[:4000])


async def explain_command_output_md(
    *,
    command: str,
    output_tail: str,
    status: str = "unknown",
    exit_code: Optional[int] = None,
    user_question: str = "",
    host_label: str = "当前终端",
    host_id: str = "",
    ui_locale: str = "zh-CN",
) -> str:
    """异步入口：优先 LLM，失败则规则说明。"""
    import asyncio

    from chibyterm.ui_locale import normalize_ui_locale

    ui_locale = normalize_ui_locale(ui_locale)
    ok = (status or "").strip().lower() == "pass"
    ec = 0
    if exit_code is not None:
        try:
            ec = int(exit_code)
        except (TypeError, ValueError):
            ec = 0 if ok else 1
    elif (status or "").strip().lower() == "fail":
        ec = 1

    if ui_locale == "en":
        fail_err = "Command appears to have failed" if (status or "").lower() == "fail" else ""
    elif ui_locale == "zh-TW":
        fail_err = "命令疑似失敗" if (status or "").lower() == "fail" else ""
    else:
        fail_err = "命令疑似失败" if (status or "").lower() == "fail" else ""

    clean_out = _strip_ansi(output_tail or "")
    snap = CommandExecSnapshot(
        ok=ok,
        host_id=host_id or "terminal",
        command=(command or "").strip(),
        exit_code=ec,
        stdout_tail=clean_out[:14000],
        stderr_tail="" if ok else clean_out[:2000],
        error="" if ok else fail_err,
    )
    try:
        md = await asyncio.to_thread(
            call_llm_explain,
            user_question=user_question,
            host_label=host_label,
            results=[snap],
            ui_locale=ui_locale,
        )
        if (md or "").strip():
            return md.strip()
    except Exception as exc:
        logger.warning("explain_command_output_md llm path failed: %s", exc)

    # LLM 不可用/失败：优先领域规则（如 free -h），再退回通用模板
    return rule_explain_fallback(
        command=command,
        output_tail=clean_out,
        status=status,
        exit_code=exit_code,
        user_question=user_question,
    )
