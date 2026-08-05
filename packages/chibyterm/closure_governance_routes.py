"""r72 闭环治理 REST：意图广播预检/派发、变更冻结待审批、回放包、人机共编 resume。

库层在 r72 已入库，但 ``terminal/main.py`` 当时未挂路由；由此集中注册，避免再丢接线。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from chibyterm.models import (
    ClosureInteractiveResumeBody,
    IntentBroadcastDispatchBody,
    IntentBroadcastPreviewBody,
)

logger = logging.getLogger(__name__)


class PendingRejectBody(BaseModel):
    reason: str = ""


def register_closure_governance_routes(
    app: FastAPI,
    *,
    host_store: Dict[str, Any],
    get_prompt_processor: Callable[[], Any],
) -> None:
    """挂载治理相关 API。``host_store`` 为 ``host_id -> Host``。"""

    def _hosts_map() -> Dict[str, Any]:
        return dict(host_store)

    @app.post("/api/closure-interactive/{trace_id}/resume")
    async def closure_interactive_resume(trace_id: str, body: ClosureInteractiveResumeBody):
        from chibycore.closure_interactive_pending import submit_interactive_resume

        action = (body.action or "").strip().lower()
        if action not in ("adopt", "rewrite", "abort"):
            raise HTTPException(400, "action 须为 adopt | rewrite | abort")
        if action == "rewrite" and not (body.command or "").strip():
            raise HTTPException(400, "rewrite 时必须提供 command")
        ok = submit_interactive_resume(
            trace_id,
            {
                "action": action,
                "command": (body.command or "").strip() or None,
            },
        )
        if not ok:
            raise HTTPException(404, "无待确认的人机共编会话（trace_id 无效或已超时）")
        return {"ok": True, "trace_id": trace_id, "action": action}

    @app.get("/api/pending-change-control")
    async def list_pending_change_control(
        session_id: Optional[str] = Query(default=None),
    ):
        from chibycore.pending_change_control import list_pending_change

        return {"items": list_pending_change(session_id=session_id)}

    @app.get("/api/pending-change-control/{pending_id}")
    async def get_pending_change_control(pending_id: str):
        from chibycore.pending_change_control import get_pending_change

        row = get_pending_change(pending_id)
        if not row:
            raise HTTPException(404, "待审批项不存在或已处理")
        return row

    @app.post("/api/pending-change-control/{pending_id}/reject")
    async def reject_pending_change_control(
        pending_id: str,
        body: PendingRejectBody = PendingRejectBody(),
    ):
        from chibycore.pending_change_control import mark_rejected

        if not mark_rejected(pending_id):
            raise HTTPException(404, "待审批项不存在或已处理")
        return {"ok": True, "pending_id": pending_id, "status": "rejected", "reason": body.reason}

    @app.post("/api/pending-change-control/{pending_id}/approve")
    async def approve_pending_change_control(pending_id: str):
        """批准后以 change_window_bypass 在目标主机 oneshot 执行原命令。"""
        from chibycore.execution_gateway import ExecutionRequest, gateway_evaluate
        from chibycore.pending_change_control import pop_pending_change
        from chibycore.unified_executor_factory import build_oneshot_from_pydantic_host

        row = pop_pending_change(pending_id)
        if not row:
            raise HTTPException(404, "待审批项不存在或已处理")
        host_id = (row.get("host_id") or "").strip()
        cmd = (row.get("command_line") or "").strip()
        if not host_id or host_id not in host_store:
            raise HTTPException(400, f"主机不可用: {host_id or '(空)'}")
        if not cmd:
            raise HTTPException(400, "待审批命令为空")
        host = host_store[host_id]
        gate = gateway_evaluate(
            ExecutionRequest(
                trace_id=str(row.get("trace_id") or ("pc_" + uuid.uuid4().hex[:12])),
                session_id=str(row.get("session_id") or f"pending:{pending_id}"),
                command_line=cmd,
                source="pending_change_approve",
                conn_type=getattr(getattr(host, "conn_type", None), "value", None)
                or str(getattr(host, "conn_type", "ssh")),
                host_id=host_id,
                plan_id=row.get("plan_id"),
                change_window_bypass=True,
            )
        )
        if not gate.allowed:
            raise HTTPException(
                403,
                detail={
                    "message": gate.reason or "网关拒绝",
                    "gateway_detail": {
                        "denial_category": getattr(gate, "denial_category", None),
                        "rule_kind": getattr(gate, "rule_kind", None),
                    },
                },
            )

        def _run():
            ex = build_oneshot_from_pydantic_host(host)
            ex.connect()
            try:
                return ex.run_command(cmd)
            finally:
                ex.close()

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, _run)
        except Exception as exc:
            raise HTTPException(500, f"批准后执行失败: {exc}") from exc
        return {
            "ok": True,
            "pending_id": pending_id,
            "host_id": host_id,
            "command": cmd,
            "exit_code": getattr(result, "exit_code", None),
            "stdout_tail": (getattr(result, "stdout", None) or "")[-2000:],
            "stderr_tail": (getattr(result, "stderr", None) or "")[-2000:],
        }

    @app.get("/api/replay-bundles")
    async def list_replay_bundles(limit: int = Query(default=50, ge=1, le=500)):
        from chibycore.replay_bundle import list_replay_bundle_summaries

        return {"items": list_replay_bundle_summaries(limit=limit)}

    @app.get("/api/replay-bundles/{trace_id}")
    async def get_replay_bundle(trace_id: str):
        from chibycore.replay_bundle import load_replay_bundle

        data = load_replay_bundle(trace_id)
        if not data:
            raise HTTPException(404, "回放包不存在")
        return data

    def _preview_core(body: IntentBroadcastPreviewBody) -> Dict[str, Any]:
        from chibycore.intent_broadcast import (
            analyze_static_conflicts,
            conflicts_to_jsonable,
            resolve_hosts_union,
            segment_hosts,
            segments_to_jsonable,
        )

        hosts = resolve_hosts_union(
            _hosts_map(),
            body.tag,
            body.host_ids,
        )
        segments = segment_hosts(hosts)
        conflicts, allowed = analyze_static_conflicts(hosts, segments, body.nl_intent)
        return {
            "nl_intent": body.nl_intent,
            "tag": body.tag,
            "host_ids": [getattr(h, "id", "") for h in hosts],
            "host_count": len(hosts),
            "segments": segments_to_jsonable(segments),
            "conflicts": conflicts_to_jsonable(conflicts),
            "dispatch_allowed": bool(allowed),
        }

    @app.post("/api/intent-broadcast/preview")
    async def intent_broadcast_preview(body: IntentBroadcastPreviewBody):
        return _preview_core(body)

    @app.post("/api/intent-broadcast/dispatch")
    async def intent_broadcast_dispatch(body: IntentBroadcastDispatchBody):
        from chibycore.execution_gateway import ExecutionRequest, gateway_evaluate
        from chibycore.intent_broadcast import (
            analyze_static_conflicts,
            conflicts_to_jsonable,
            resolve_hosts_union,
            segment_hosts,
            segments_to_jsonable,
        )
        from chibycore.unified_executor_factory import build_oneshot_from_pydantic_host

        hosts = resolve_hosts_union(_hosts_map(), body.tag, body.host_ids)
        segments = segment_hosts(hosts)
        conflicts, allowed = analyze_static_conflicts(hosts, segments, body.nl_intent)
        has_warn = any(c.severity == "warning" for c in conflicts)
        if not allowed:
            raise HTTPException(
                409,
                detail={
                    "message": "静态冲突阻止派发",
                    "conflicts": conflicts_to_jsonable(conflicts),
                    "segments": segments_to_jsonable(segments),
                },
            )
        if has_warn and not body.ignore_warnings:
            raise HTTPException(
                409,
                detail={
                    "message": "存在 warning 级冲突；确认后请设 ignore_warnings=true",
                    "conflicts": conflicts_to_jsonable(conflicts),
                    "segments": segments_to_jsonable(segments),
                },
            )

        pp = get_prompt_processor()
        if pp is None:
            raise HTTPException(503, "LLM PromptProcessor 未就绪，无法翻译意图")

        trace_group = "ib_" + uuid.uuid4().hex[:16]
        host_by_id = {getattr(h, "id", ""): h for h in hosts}
        segment_results: List[Dict[str, Any]] = []

        for seg in segments:
            try:
                pr = pp.process(body.nl_intent, shell_profile=seg.shell_profile)
                cmd = str(getattr(pr, "command", None) or "").strip()
                llm_meta = {
                    "ok": bool(cmd) and bool(getattr(pr, "should_execute", True)),
                    "command": cmd,
                    "explanation": str(getattr(pr, "explanation", "") or "")[:500],
                    "warning": str(getattr(pr, "warning", "") or "")[:300],
                }
            except Exception as exc:
                logger.warning("intent-broadcast 分段翻译失败 seg=%s: %s", seg.segment_id, exc)
                segment_results.append(
                    {
                        "segment_id": seg.segment_id,
                        "adapter_label": seg.adapter_label,
                        "llm": {"ok": False, "error": str(exc)[:300]},
                        "per_host": [],
                    }
                )
                continue

            if not cmd:
                segment_results.append(
                    {
                        "segment_id": seg.segment_id,
                        "adapter_label": seg.adapter_label,
                        "llm": llm_meta,
                        "per_host": [],
                        "error": "翻译结果无命令",
                    }
                )
                continue

            def _exec_one(hid: str) -> Dict[str, Any]:
                h = host_by_id.get(hid)
                if not h:
                    return {"host_id": hid, "ok": False, "error": "host missing"}
                gate = gateway_evaluate(
                    ExecutionRequest(
                        trace_id=f"{trace_group}_{hid[:8]}",
                        session_id=f"intent_broadcast:{trace_group}",
                        command_line=cmd,
                        source="intent_broadcast",
                        conn_type=seg.conn_type,
                        host_id=hid,
                        plan_id=None,
                    )
                )
                if not gate.allowed:
                    return {
                        "host_id": hid,
                        "ok": False,
                        "gateway_allowed": False,
                        "reason": gate.reason,
                        "pending_change_control": bool(
                            getattr(gate, "pending_change_control", False)
                        ),
                        "change_control_pending_id": getattr(gate, "pending_id", "")
                        or "",
                    }
                ex = build_oneshot_from_pydantic_host(h)
                ex.connect()
                try:
                    r = ex.run_command(cmd)
                finally:
                    ex.close()
                return {
                    "host_id": hid,
                    "ok": True,
                    "gateway_allowed": True,
                    "exit_code": getattr(r, "exit_code", None),
                    "stdout_tail": (getattr(r, "stdout", None) or "")[-1500:],
                    "stderr_tail": (getattr(r, "stderr", None) or "")[-1500:],
                }

            per_host: List[Dict[str, Any]] = []
            ids = list(seg.host_ids)
            if body.parallel and len(ids) > 1:
                workers = max(1, min(int(body.max_concurrency or 8), len(ids)))
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futs = {pool.submit(_exec_one, hid): hid for hid in ids}
                    for fut in as_completed(futs):
                        try:
                            per_host.append(fut.result())
                        except Exception as exc:
                            per_host.append(
                                {"host_id": futs[fut], "ok": False, "error": str(exc)[:300]}
                            )
            else:
                for hid in ids:
                    try:
                        per_host.append(_exec_one(hid))
                    except Exception as exc:
                        per_host.append({"host_id": hid, "ok": False, "error": str(exc)[:300]})

            segment_results.append(
                {
                    "segment_id": seg.segment_id,
                    "adapter_label": seg.adapter_label,
                    "conn_type": seg.conn_type,
                    "shell_profile": seg.shell_profile,
                    "llm": llm_meta,
                    "per_host": per_host,
                }
            )

        return {
            "ok": True,
            "trace_group": trace_group,
            "nl_intent": body.nl_intent,
            "conflicts": conflicts_to_jsonable(conflicts),
            "segments": segment_results,
        }
