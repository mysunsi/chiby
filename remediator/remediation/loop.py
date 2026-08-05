"""主循环控制器：确认与重试、历史链累积、终止条件、知识库写入。"""
from __future__ import annotations

import difflib
import logging
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from .knowledge_base import RemediationKnowledgeBase
from remediator.core.confidence import confidence_for_remediation_source

# Healing 统一检索（可选，懒加载）
_HEALING_RETRIEVER: Optional["HealingKnowledgeRetriever"] = None  # noqa: F821

from .llm_agent import propose_remediation
from .models import (
    CommandExecutionOutcome,
    EnvironmentSnapshot,
    KnowledgeRecord,
    LLMRemediationJSON,
    RemediationHistory,
    RemediationSessionResult,
    RemediationTerminationReason,
    StructuredError,
    compute_error_fingerprint,
    normalize_command_for_fingerprint,
    os_fingerprint_key,
)
from .parser import assess_fixability, parse_execution_error

logger = logging.getLogger(__name__)


# ── 懒加载 Healing 统一检索器 ────────────────────────────────────────────


def _get_healing_retriever():
    """懒加载 HealingKnowledgeRetriever（从 KnowledgeHub + RemediationKB 统一检索）。"""
    global _HEALING_RETRIEVER
    if _HEALING_RETRIEVER is None:
        try:
            from chibycore.healing.knowledge_retriever import HealingKnowledgeRetriever
            _HEALING_RETRIEVER = HealingKnowledgeRetriever()
        except Exception as ex:
            logger.debug("HealingKnowledgeRetriever 不可用（非致命）: %s", ex)
            _HEALING_RETRIEVER = None
    return _HEALING_RETRIEVER


def command_similarity(a: str, b: str) -> float:
    """SequenceMatcher 与上一轮的相似度（0~1）。"""
    x, y = (a or "").strip(), (b or "").strip()
    if not x and not y:
        return 1.0
    if not x or not y:
        return 0.0
    return difflib.SequenceMatcher(None, x, y).ratio()


def levenshtein_distance(a: str, b: str) -> int:
    """经典编辑距离（插入/删除/替换代价均为 1）；命令字符串较短，用完整 DP 矩阵。"""
    a, b = a or "", b or ""
    if a == b:
        return 0
    n, m = len(a), len(b)
    if n == 0:
        return m
    if m == 0:
        return n
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost,
            )
    return dp[n][m]


def levenshtein_ratio(a: str, b: str) -> float:
    """
    归一化 Levenshtein 相似度：1 - dist / max(len(a),len(b))。
    全空串视为 1.0，一侧空串 0.0。
    """
    x, y = (a or "").strip(), (b or "").strip()
    if not x and not y:
        return 1.0
    if not x or not y:
        return 0.0
    d = levenshtein_distance(x, y)
    return 1.0 - d / max(len(x), len(y), 1)


class RemediationController:
    """
    错误迭代修正主循环。

    execute_fn: 接受命令字符串，返回 CommandExecutionOutcome（由包装器提供，勿改 core/executor.py）。
    """

    def __init__(
        self,
        execute_fn: Callable[[str], CommandExecutionOutcome],
        knowledge_base: RemediationKnowledgeBase,
        *,
        env: Optional[EnvironmentSnapshot] = None,
        max_retries: int = 3,
        similarity_stop: Optional[float] = None,
        loop_exact_sequence_ratio: float = 0.98,
        loop_exact_levenshtein_ratio: float = 0.98,
        loop_semantic_sequence_ratio: float = 0.85,
        loop_semantic_levenshtein_ratio: float = 0.80,
        llm_model: str = "gpt-4o-mini",
        litellm_api_key: Optional[str] = None,
        litellm_api_base: Optional[str] = None,
        console: Optional[Console] = None,
        interactive: bool = True,
        confidence_execute_threshold: float = 0.4,
    ) -> None:
        self._execute = execute_fn
        self._kb = knowledge_base
        self._env = env or EnvironmentSnapshot()
        self._confidence_threshold = float(confidence_execute_threshold)
        self._max_retries = max(1, max_retries)
        # 兼容旧参数 similarity_stop：若传入则作为「完全雷同」序列阈值
        self._exact_seq = (
            float(similarity_stop)
            if similarity_stop is not None
            else loop_exact_sequence_ratio
        )
        self._exact_lev = loop_exact_levenshtein_ratio
        self._sem_seq = loop_semantic_sequence_ratio
        self._sem_lev = loop_semantic_levenshtein_ratio
        self._llm_model = llm_model
        self._api_key = litellm_api_key
        self._api_base = litellm_api_base
        self._console = console or Console()
        self._interactive = interactive

    def _detect_spurious_fix_loop(
        self, prev_fix: str, new_fix: str
    ) -> Tuple[bool, Optional[RemediationTerminationReason], str]:
        """
        Task 1.2：双重相似度 — SequenceMatcher + Levenshtein 比率。
        - 二者均达到「完全雷同」阈值 → SIMILAR_FIX_LOOP
        - 否则若 seq > sem_seq 且 lev > sem_lev → LOOP_DETECTED_SEMANTIC（语义假循环）
        """
        seq = command_similarity(prev_fix, new_fix)
        lev = levenshtein_ratio(prev_fix, new_fix)
        if seq >= self._exact_seq and lev >= self._exact_lev:
            return True, RemediationTerminationReason.SIMILAR_FIX_LOOP, (
                f"SequenceMatcher={seq:.3f}、Levenshtein 比率={lev:.3f}，"
                f"均 ≥ 完全雷同阈值（{self._exact_seq:.2f}/{self._exact_lev:.2f}），终止机械重复。"
            )
        if seq > self._sem_seq and lev > self._sem_lev:
            return True, RemediationTerminationReason.LOOP_DETECTED_SEMANTIC, (
                f"双重相似度判定语义高度雷同（seq={seq:.3f} > {self._sem_seq}, "
                f"lev={lev:.3f} > {self._sem_lev}），疑似无效迭代，已终止。"
            )
        return False, None, ""

    def run(self, initial_command: str) -> RemediationSessionResult:
        """从初始命令开始完整闭环。"""
        history = RemediationHistory()
        history.append("original_command", initial_command)

        first = self._execute(initial_command)
        if first.return_code == 0:
            return RemediationSessionResult(
                termination=RemediationTerminationReason.SUCCESS,
                message="命令已成功执行，无需修正。",
                history=history,
            )

        structured = parse_execution_error(
            command=initial_command,
            return_code=first.return_code,
            stdout=first.stdout,
            stderr=first.stderr,
        )
        history.append("error", self._error_summary(structured))

        fixable, why = assess_fixability(structured)
        if not fixable:
            return self._not_fixable_outcome(structured, why, history)

        seed_structured = structured

        kb_best = self._kb.query_best_match(seed_structured, self._env)
        kb_hits: List[KnowledgeRecord] = [kb_best] if kb_best else []

        # 统一检索增强：查询 KnowledgeHub（通过 HealingKnowledgeRetriever）
        # 当 legacy KB 未命中时，尝试语义检索 KnowledgeHub
        if not kb_hits:
            healing_results = self._query_healing_knowledge_hub(
                initial_command, first.stderr, first.stdout,
            )
            for hr in healing_results:
                # 转换为 KnowledgeRecord 供 _next_proposal 使用
                convert = KnowledgeRecord(
                    error_category=seed_structured.error_category,
                    env_os=self._env.os_name,
                    env_privilege=(
                        "root" if self._env.is_root_or_sudo else self._env.current_user
                    ),
                    original_command=initial_command,
                    fixed_command=hr.remediation,
                    root_cause=hr.root_cause or "KnowledgeHub 命中",
                    stderr_snippet=(first.stderr or "")[:800],
                    environment_fingerprint="",
                    requires_package=None,
                )
                convert._healing_confidence = hr.confidence  # 附加置信度
                kb_hits.append(convert)

        prev_fix: Optional[str] = None
        attempts = 0

        while attempts < self._max_retries:
            proposal, source = self._next_proposal(
                structured,
                history,
                kb_hits,
                iteration=attempts,
            )
            if proposal is None:
                return RemediationSessionResult(
                    termination=RemediationTerminationReason.LLM_FAILURE,
                    message="大模型修正不可用（调用失败或返回无效）。",
                    history=history,
                )

            if prev_fix is not None:
                abort, term, msg = self._detect_spurious_fix_loop(
                    prev_fix, proposal.fixed_command
                )
                if abort and term is not None:
                    return RemediationSessionResult(
                        termination=term,
                        message=msg,
                        history=history,
                        final_command=proposal.fixed_command,
                    )

            action = self._confirm_step(initial_command, proposal)
            if action == "abort":
                return RemediationSessionResult(
                    termination=RemediationTerminationReason.USER_ABORT,
                    message="用户放弃修正。",
                    history=history,
                )
            if action == "manual":
                manual_cmd = self._ask_manual_command()
                if not manual_cmd.strip():
                    return RemediationSessionResult(
                        termination=RemediationTerminationReason.USER_ABORT,
                        message="未输入有效命令，已中止。",
                        history=history,
                    )
                proposal = LLMRemediationJSON(
                    root_cause="用户手动调整命令",
                    fixed_command=manual_cmd.strip(),
                    risk_warning="手动输入，请自负风险。",
                    requires_precheck_script=False,
                    notes="user_manual",
                    confidence_score=1.0,
                )

            if (
                proposal.notes != "user_manual"
                and proposal.confidence_score < self._confidence_threshold
            ):
                return RemediationSessionResult(
                    termination=RemediationTerminationReason.LOW_CONFIDENCE,
                    message=(
                        f"修复置信度 {proposal.confidence_score:.3f} 低于阈值 "
                        f"{self._confidence_threshold}；已阻止自动执行（等同 DRY_RUN），请人工审核。"
                    ),
                    history=history,
                    final_command=proposal.fixed_command,
                    confidence_score=proposal.confidence_score,
                )

            history.append("fix_command", proposal.fixed_command)

            out = self._execute(proposal.fixed_command)
            attempts += 1
            prev_fix = proposal.fixed_command

            if out.return_code == 0:
                self._persist_success(seed_structured, initial_command, proposal, first.stderr)
                return RemediationSessionResult(
                    termination=RemediationTerminationReason.SUCCESS,
                    message="修正命令执行成功，并已沉淀案例（若可用）。",
                    history=history,
                    final_command=proposal.fixed_command,
                    knowledge_saved=True,
                    confidence_score=proposal.confidence_score,
                )

            structured = parse_execution_error(
                command=proposal.fixed_command,
                return_code=out.return_code,
                stdout=out.stdout,
                stderr=out.stderr,
            )
            history.append("error", self._error_summary(structured))
            fixable, why = assess_fixability(structured)
            if not fixable:
                return RemediationSessionResult(
                    termination=RemediationTerminationReason.NOT_FIXABLE,
                    message=f"修正后仍失败且判定不可继续自动修正：{why}",
                    history=history,
                    final_command=proposal.fixed_command,
                )

        return RemediationSessionResult(
            termination=RemediationTerminationReason.MAX_RETRIES,
            message=f"已达最大重试次数 {self._max_retries}。",
            history=history,
            final_command=prev_fix,
        )

    def _next_proposal(
        self,
        structured: StructuredError,
        history: RemediationHistory,
        kb_hits: List[KnowledgeRecord],
        *,
        iteration: int,
    ):
        # 第五步：同类错误优先走知识库；仅首轮尝试 KB，其后一律 LLM（带完整历史）
        if iteration == 0 and kb_hits:
            # 按置信度排序（Healing 检索结果拥有 _healing_confidence，优先使用）
            def _hit_score(kr: KnowledgeRecord) -> float:
                conf = getattr(kr, "_healing_confidence", None)
                if conf is not None:
                    return conf.score
                return 1.0  # legacy KB 精确指纹命中视为高分

            best = max(kb_hits, key=_hit_score)
            rw = "该命令来自本地知识库，目标环境可能不同，执行前请确认。"
            cs = confidence_for_remediation_source("kb", best.fixed_command, rw)
            llm_like = LLMRemediationJSON(
                root_cause=best.root_cause or "知识库命中同类错误案例",
                fixed_command=best.fixed_command,
                risk_warning=rw,
                requires_precheck_script=False,
                notes="kb_hit",
                confidence_score=cs,
            )
            return llm_like, "kb"

        try:
            prop = propose_remediation(
                structured,
                history,
                self._env,
                model=self._llm_model,
                api_key=self._api_key,
                api_base=self._api_base,
                knowledge_base=self._kb,
            )
            cs_llm = confidence_for_remediation_source(
                "llm", prop.fixed_command, prop.risk_warning or ""
            )
            prop = prop.model_copy(update={"confidence_score": cs_llm})
            return prop, "llm"
        except Exception as e:
            logger.exception("LLM 修正失败: %s", e)
            return None, "error"

    def _confirm_step(self, original: str, proposal: LLMRemediationJSON) -> str:
        """返回 execute | manual | abort"""
        c = self._console
        c.print(
            Panel.fit(
                f"[bold]根因[/bold]\n{proposal.root_cause}\n\n"
                f"[bold]风险提示[/bold]\n{proposal.risk_warning or '（无）'}\n\n"
                f"[bold]置信度[/bold]\n{proposal.confidence_score:.4f} "
                f"(阈值 {self._confidence_threshold})\n",
                title="第三步 · 修正策略",
            )
        )
        c.print(Panel(f"[red]{original}[/red]", title="原始命令"))
        c.print(Panel(f"[green]{proposal.fixed_command}[/green]", title="修正命令"))

        if not self._interactive:
            return "execute"

        choice = Prompt.ask(
            "请选择",
            choices=["立即执行", "手动调整", "放弃"],
            default="立即执行",
        )
        if choice == "立即执行":
            return "execute"
        if choice == "手动调整":
            return "manual"
        return "abort"

    def _ask_manual_command(self) -> str:
        if not self._interactive:
            return ""
        return Prompt.ask("请输入替换执行的命令")

    @staticmethod
    def _error_summary(s: StructuredError) -> str:
        base = (
            f"{s.display_type_cn} | rc={s.return_code} | {s.reason}"
            + (f" | path={s.path}" if s.path else "")
        )
        if s.requires_package:
            base += f" | package_hint={s.requires_package}"
        return base

    def _not_fixable_outcome(
        self,
        structured: StructuredError,
        why: str,
        history: RemediationHistory,
    ) -> RemediationSessionResult:
        msg = (
            f"需人工介入：{why}\n"
            f"错误摘要：{self._error_summary(structured)}"
        )
        hits = self._kb.find_similar(
            structured.error_category,
            structured.metadata.get("command", ""),
            structured.stderr_snippet,
            limit=2,
        )
        if hits:
            msg += "\n知识库中的类似修复（仅供参考）：\n"
            for h in hits:
                msg += f"  - 曾用修正：{h.fixed_command}\n"

        return RemediationSessionResult(
            termination=RemediationTerminationReason.NOT_FIXABLE,
            message=msg,
            history=history,
        )

    def _query_healing_knowledge_hub(
        self,
        command: str,
        stderr: str,
        stdout: str,
    ) -> list:
        """通过 HealingKnowledgeRetriever 统一检索 KnowledgeHub + RemediationKB。

        仅返回高置信度（≥0.3）且有修复命令的结果，按置信度降序排列。
        """
        try:
            retriever = _get_healing_retriever()
            if retriever is None:
                return []
            results = retriever.retrieve(
                command=command,
                stderr=stderr or "",
                stdout=stdout or "",
                limit=5,
            )
            filtered = [
                r for r in results
                if r.remediation and r.remediation.strip() and r.confidence.score >= 0.3
            ]
            if filtered:
                logger.info(
                    "统一检索命中 %d 条 KnowledgeHub 结果（最高置信度 %.2f）",
                    len(filtered), filtered[0].confidence.score,
                )
            return filtered
        except Exception as ex:
            logger.debug("统一检索 KnowledgeHub 失败（非致命）: %s", ex)
            return []

    def _persist_success(
        self,
        structured: StructuredError,
        original_command: str,
        proposal: LLMRemediationJSON,
        first_stderr: str,
    ) -> None:
        try:
            fp = compute_error_fingerprint(
                structured.error_category.value,
                normalize_command_for_fingerprint(original_command),
                os_fingerprint_key(self._env),
            )
            rec = KnowledgeRecord(
                error_category=structured.error_category,
                env_os=self._env.os_name,
                env_privilege=(
                    "root" if self._env.is_root_or_sudo else self._env.current_user
                ),
                original_command=original_command,
                fixed_command=proposal.fixed_command,
                root_cause=proposal.root_cause,
                stderr_snippet=(first_stderr or "")[:800],
                fingerprint=fp,
                requires_package=structured.requires_package,
            )
            self._kb.save_success(rec)
        except Exception as e:
            logger.warning("知识库写入失败（忽略）: %s", e)


def build_default_kb_path(project_root: Optional[Path] = None) -> Path:
    root = project_root or Path.cwd()
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    return data / "remediation_kb.sqlite"
