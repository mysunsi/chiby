"""Healing — 自适应自愈引擎核心编排器。

工作流：
1. 分析失败信号（命令+stderr/stdout）
2. Retrieval：从 knowledge_hub + remediation_kb 检索历史修复方案
3. Scoring：置信度评分器对每條结果评分
4. Decision：
   - HIGH  (≥0.7) → 直接使用知识库修复方案
   - MEDIUM (0.4~0.7) → 验证后使用
   - LOW   (<0.4)  → 退回到 LLM 生成修复方案
5. Execution：执行修复命令
6. Verification：验证修复是否成功
7. Archive：成功→归档到 KnowledgeHub
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional, Tuple

from chibycore.healing.confidence import (
    ConfidenceLevel,
    HealingConfidence,
    score_confidence,
)
from chibycore.healing.knowledge_retriever import (
    HealingKnowledgeRetriever,
    RetrievedKnowledge,
)

logger = logging.getLogger(__name__)


# ── 反馈类型 ─────────────────────────────────────────────────────────────


class HealDecision(str, Enum):
    """修复方案的决策来源。"""
    KNOWLEDGE_DIRECT = "knowledge_direct"               # 知识库高置信度直接使用
    KNOWLEDGE_VERIFIED = "knowledge_verified"           # 知识库中置信度，验证后使用
    LLM_GENERATED = "llm_generated"                     # LLM 生成
    NO_FIX_AVAILABLE = "no_fix_available"               # 无法生成修复方案
    CANCELLED = "cancelled"                             # 用户取消


@dataclass
class FixProvenance:
    """单条修复方案的来源追踪。"""
    decision: HealDecision
    source: str                                          # knowledge_hub / remediation_kb / llm
    source_id: Optional[str] = None                      # 知识库条目 ID
    confidence: Optional[HealingConfidence] = None       # 置信度详情
    retrieved_command: Optional[str] = None              # 原始命令（知识库中的）
    root_cause: Optional[str] = None                     # 根因
    llm_reasoning: Optional[str] = None                  # LLM 推理摘要


@dataclass
class HealingResult:
    """一次自愈尝试的完整结果。"""
    ok: bool
    final_command: str
    provenance: FixProvenance
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    stop_reason: str = ""
    attempts: int = 0                                      # 修复尝试轮次


# ── 编排器 ───────────────────────────────────────────────────────────────


ExecuteFn = Callable[[str], "ExecResult"]
GatewayFn = Callable[[str], Tuple[bool, str]]
LlmFixFn = Callable[[List], List[str]]


class HealingEngine:
    """自适应自愈引擎。

    使用方式：
        engine = HealingEngine()
        result = engine.heal(
            command="apt install nginx",
            stderr="E: Package nginx not found",
            execute=my_execute_fn,
            gateway=my_gateway_fn,
        )
        if result.ok:
            print(f"修复成功: {result.final_command}")
            print(f"来源: {result.provenance.source}")
    """

    def __init__(
        self,
        retriever: Optional[HealingKnowledgeRetriever] = None,
    ) -> None:
        self._retriever = retriever or HealingKnowledgeRetriever()
        self._llm_fix_fn: Optional[LlmFixFn] = None

    # ── 核心公共方法 ────────────────────────────────────────────────────

    def heal(
        self,
        *,
        command: str,
        stderr: str = "",
        stdout: str = "",
        execute: ExecuteFn,
        gateway: GatewayFn,
        error_category: Optional[str] = None,
        llm_fix_fn: Optional[LlmFixFn] = None,
        max_attempts: int = 3,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> HealingResult:
        """
        完整自愈流程：检索 → 决策 → 执行 → 归档。

        参数：
            command:        原始失败命令
            stderr:         错误输出
            stdout:         标准输出
            execute:        命令执行回调
            gateway:        网关允许回调
            error_category: 可选错误类别
            llm_fix_fn:     可选的 LLM 修复生成器（默认使用 call_fix_pipeline）
            max_attempts:   最大重试次数
            cancel_check:   取消检查

        返回：
            HealingResult
        """
        self._llm_fix_fn = llm_fix_fn

        # 1. 检索
        knowledge = self._retriever.retrieve(
            command=command,
            stderr=stderr,
            stdout=stdout,
            error_category=error_category,
            limit=5,
        )

        # 2. 决策 → 生成候选修复命令
        candidates = self._decide_fixes(
            command=command,
            stderr=stderr,
            knowledge=knowledge,
        )

        if not candidates:
            return HealingResult(
                ok=False,
                final_command=command,
                provenance=FixProvenance(
                    decision=HealDecision.NO_FIX_AVAILABLE,
                    source="none",
                ),
                stop_reason="no_fix_candidates",
            )

        # 3. 逐条尝试
        for idx, (fix_cmd, prov) in enumerate(candidates):
            if cancel_check and cancel_check():
                return HealingResult(
                    ok=False,
                    final_command=fix_cmd,
                    provenance=prov,
                    stop_reason="cancelled",
                    attempts=idx,
                )

            # 网关检查
            gok, greason = gateway(fix_cmd)
            if not gok:
                logger.info("网关拒绝修复命令: %s (%s)", fix_cmd[:80], greason)
                prov.source = f"{prov.source}|gateway_denied"
                continue

            # 执行
            try:
                res = execute(fix_cmd)
            except Exception as ex:
                logger.warning("修复命令执行失败: %s (%s)", fix_cmd[:80], ex)
                continue

            exit_code = getattr(res, "exit_code", getattr(res, "returncode", -1))
            # 成功判定（exit_code=0）
            if exit_code == 0:
                # 归档到 KnowledgeHub
                self._archive_success(
                    original_command=command,
                    effective_command=fix_cmd,
                    stderr=stderr,
                    stdout=stdout,
                    provenance=prov,
                )
                return HealingResult(
                    ok=True,
                    final_command=fix_cmd,
                    provenance=prov,
                    exit_code=exit_code,
                    stdout=getattr(res, "stdout", "") or "",
                    stderr=getattr(res, "stderr", "") or "",
                    stop_reason="success",
                    attempts=idx + 1,
                )

        # 所有候选均失败
        return HealingResult(
            ok=False,
            final_command=candidates[-1][0] if candidates else command,
            provenance=candidates[-1][1] if candidates else FixProvenance(
                decision=HealDecision.NO_FIX_AVAILABLE,
                source="none",
            ),
            stop_reason="all_candidates_failed",
            attempts=len(candidates),
        )

    def generate_fixes(
        self,
        *,
        command: str,
        stderr: str = "",
        stdout: str = "",
        error_category: Optional[str] = None,
        history: Optional[List] = None,
        shell_profile: str = "unix",
        llm_fix_fn: Optional[LlmFixFn] = None,
    ) -> Tuple[List[str], List[FixProvenance]]:
        """
        生成修复方案列表（不执行），可替代 closure_retry_runner._pick_fixes()。

        返回 (commands, provenances) 元组。
        """
        self._llm_fix_fn = llm_fix_fn

        knowledge = self._retriever.retrieve(
            command=command,
            stderr=stderr,
            stdout=stdout,
            error_category=error_category,
            limit=5,
        )

        candidates = self._decide_fixes(
            command=command,
            stderr=stderr,
            knowledge=knowledge,
            history=history,
            shell_profile=shell_profile,
        )

        commands = [c[0] for c in candidates]
        provenances = [c[1] for c in candidates]
        return commands, provenances

    # ── 内部方法 ────────────────────────────────────────────────────────

    def _decide_fixes(
        self,
        *,
        command: str,
        stderr: str,
        knowledge: List[RetrievedKnowledge],
        history: Optional[List] = None,
        shell_profile: str = "unix",
    ) -> List[Tuple[str, FixProvenance]]:
        """
        根据知识库命中结果 + 置信度，生成修复候选命令列表。

        策略：
        - HIGH（≥0.7） → 直接使用，排在最前
        - MEDIUM（0.4~0.7） → 验证后使用，排在第二
        - LOW（<0.4） → 退回到 LLM，排在最后
        """
        candidates: List[Tuple[str, FixProvenance]] = []
        high_sources: set[str] = set()

        # 高置信度 → 知识库直接修复
        for k in knowledge:
            if k.confidence.level == ConfidenceLevel.HIGH:
                candidates.append((
                    k.remediation,
                    FixProvenance(
                        decision=HealDecision.KNOWLEDGE_DIRECT,
                        source=k.source,
                        source_id=k.source_id,
                        confidence=k.confidence,
                        retrieved_command=k.original_command,
                        root_cause=k.root_cause,
                    ),
                ))
                high_sources.add(k.remediation)

        # 中置信度 → 知识库验证后修复
        for k in knowledge:
            if k.confidence.level == ConfidenceLevel.MEDIUM:
                if k.remediation in high_sources:
                    continue
                candidates.append((
                    k.remediation,
                    FixProvenance(
                        decision=HealDecision.KNOWLEDGE_VERIFIED,
                        source=k.source,
                        source_id=k.source_id,
                        confidence=k.confidence,
                        retrieved_command=k.original_command,
                        root_cause=k.root_cause,
                    ),
                ))

        # 低置信度或无匹配 → LLM 生成
        has_high_or_medium = any(
            c[1].decision in (HealDecision.KNOWLEDGE_DIRECT, HealDecision.KNOWLEDGE_VERIFIED)
            for c in candidates
        )
        if not has_high_or_medium:
            llm_fixes = self._generate_llm_fixes(
                command=command,
                stderr=stderr,
                history=history,
                shell_profile=shell_profile,
                knowledge=knowledge,  # 传递检索结果给 LLM 作为参考
            )
            for fix in llm_fixes:
                candidates.append((
                    fix,
                    FixProvenance(
                        decision=HealDecision.LLM_GENERATED,
                        source="llm",
                        llm_reasoning="LLM 修复流水线生成",
                    ),
                ))

        return candidates

    def _generate_llm_fixes(
        self,
        *,
        command: str,
        stderr: str,
        history: Optional[List] = None,
        shell_profile: str = "unix",
        knowledge: Optional[List[RetrievedKnowledge]] = None,
    ) -> List[str]:
        """生成 LLM 修复方案。如果提供了知识库命中的上下文，注入到 LLM prompt 中。"""
        try:
            from chibycore.closure_llm_fix import call_fix_pipeline
        except ImportError:
            logger.warning("call_fix_pipeline 不可用，返回空方案列表")
            return []

        if self._llm_fix_fn:
            payload_history = history or []
            return self._llm_fix_fn(payload_history)

        # 构造包含知识库上下文的 history
        payload_history = list(history or [])
        if knowledge and not payload_history:
            # 如果 history 为空，用检索结果构造占位 payload 提供上下文
            try:
                from chibycore.executor_contract import ClosurePayload, ExecResult
                for k in knowledge[:2]:
                    payload_history.append(
                        self._make_placeholder_payload(command, stderr, k)
                    )
            except ImportError:
                pass

        return call_fix_pipeline(payload_history, shell_profile=shell_profile)

    def _make_placeholder_payload(
        self,
        command: str,
        stderr: str,
        knowledge: RetrievedKnowledge,
    ) -> Any:
        """用检索结果构造一个占位 ClosurePayload 供 LLM 修复流水线参考。"""
        try:
            from chibycore.executor_contract import ClosurePayload, ExecResult
            from datetime import datetime, timezone
            return ClosurePayload(
                trace_id="healing_ctx",
                raw_command=command,
                effective_command=command,
                transport="local",
                exit_code=1,
                stdout=knowledge.root_cause or "",
                stderr=stderr,
                session_id="",
                plan_id="",
                nl_intent_hint=f"参考历史修复: {knowledge.remediation}",
                ts=datetime.now(timezone.utc).isoformat(),
                risk_level=None,
            )
        except ImportError:
            return command

    def _archive_success(
        self,
        original_command: str,
        effective_command: str,
        stderr: str,
        stdout: str,
        provenance: FixProvenance,
    ) -> None:
        """修复成功后归档到 KnowledgeHub。"""
        try:
            from chibycore.knowledge_hub.models import KBEntry, KBCategory, KBConfidence
            from chibycore.knowledge_hub.storage import KnowledgeHubStorage

            # 推断类别
            from chibycore.kb_closure_archive import _infer_category, _auto_extract_title
            category = _infer_category(effective_command)
            title = _auto_extract_title(effective_command, stderr, stdout)

            entry = KBEntry(
                title=title,
                category=category,
                symptom=(
                    f"stderr: {(stderr[:1500]).replace(chr(10), '; ')}" if stderr
                    else f"自动修复成功: {original_command}"
                ),
                root_cause=provenance.root_cause or provenance.llm_reasoning or "Healing Engine 自动修复",
                remediation=effective_command,
                verify_method="exit_code=0 (Healing Engine 验证)",
                tags=_auto_tags(effective_command, stderr, provenance),
                original_command=original_command,
                confidence=KBConfidence.MEDIUM,
                source=provenance.source,
                source_id=provenance.source_id or "healing_engine",
                success_count=1,
            )
            storage = KnowledgeHubStorage.get_instance()
            storage.save_kb_entry(entry)
            logger.info(
                "Healing Engine 归档成功 id=%s cmd=%s source=%s",
                entry.id, effective_command[:60], provenance.source,
            )
        except Exception as ex:
            logger.warning("Healing Engine 归档失败（非致命）: %s", ex)


def _auto_tags(cmd: str, stderr: str, provenance: FixProvenance) -> list[str]:
    """自动提取标签。"""
    tags: list[str] = ["healing_engine"]
    if provenance.source != "llm":
        tags.append("kb_sourced")
    if provenance.decision in (HealDecision.KNOWLEDGE_DIRECT, HealDecision.KNOWLEDGE_VERIFIED):
        tags.append("proven_fix")
    cmd_lower = cmd.lower()
    for tool in ("apt", "yum", "dnf", "pip", "npm", "docker", "kubectl",
                 "systemctl", "service", "curl", "ssh", "nginx", "mysql",
                 "python", "node", "git", "redis", "chmod", "chown"):
        if tool in cmd_lower:
            tags.append(tool)
    err_lower = (stderr or "").lower()
    for err_type in ("timeout", "not found", "permission denied",
                     "no such", "cannot", "failed", "connection refused"):
        if err_type in err_lower:
            tags.append(err_type.replace(" ", "_"))
    return tags
