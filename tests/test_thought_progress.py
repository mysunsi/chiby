"""思维链进度单元测试。"""

from __future__ import annotations

import pytest

from terminal.mobile.thought_progress import ThoughtChainEmitter, build_planning_steps


def test_build_steps_for_error_compare():
    steps = build_planning_steps("对比昨天同一时刻，今天的 error 量是增加了还是减少了？")
    ids = [s.id for s in steps]
    assert "understand" in ids
    assert "baseline" in ids
    assert "source" in ids
    assert "emit_plan" in ids


def test_build_steps_for_nginx_multi():
    steps = build_planning_steps("检测主机 A、B、C 的 nginx 运行情况")
    ids = [s.id for s in steps]
    assert "targets" in ids
    assert "signal" in ids


@pytest.mark.asyncio
async def test_emitter_advances_and_finishes():
    events = []

    async def on_event(ev):
        events.append(ev)

    chain = ThoughtChainEmitter(
        on_event,
        user_text="磁盘还剩多少",
        tick_sec=0.05,
    )
    await chain.start()
    await chain.note_progress("掌上AI大脑 内部思考中（已 1 段）")
    await chain.finish(ok=True, detail="计划已产出")
    kinds = [e["type"] for e in events]
    assert "thought_step" in kinds
    assert any(e.get("state") == "done" for e in events if e.get("type") == "thought_step")
