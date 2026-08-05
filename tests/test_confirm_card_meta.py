"""确认卡 v2：风险分级、摘要、本地 AI 解读、二次确认。"""

from __future__ import annotations

from terminal.mobile.confirm_card_meta import (
    AI_DISCLAIMER,
    build_ai_explanation,
    build_confirm_card_meta,
    explain_change_from_context,
    infer_operation_type,
    infer_risk_level,
    typed_confirm_ok,
)
from terminal.mobile.models import PermissionCard


def test_awk_nr_not_modify_file():
    cmd = "ps aux | awk 'NR==1{print} NR>1{print $1}'"
    assert infer_operation_type(command=cmd) == "shell"
    # 只读探测不应抬到 high
    assert infer_risk_level(command=cmd) in ("low", "medium")


def test_remove_is_high_typed():
    meta = build_confirm_card_meta(commands=["rm -rf /tmp/old_backup.sql"])
    assert meta.operation_type == "remove"
    assert meta.risk_level == "high"
    assert meta.require_typed_confirm is True
    # 挂卡不预生成解读、不默认展开；点击后再实时生成
    assert meta.explain_default_open is False
    assert meta.ai_explanation == ""
    assert meta.can_explain is True
    fields = meta.as_card_fields()
    assert fields["ai_explanation"] == ""
    assert fields["explain_default_open"] is False
    assert fields["can_explain"] is True


def test_write_file_tool_medium():
    meta = build_confirm_card_meta(
        pending_tool_calls=[
            {
                "tool": "remote_write_file",
                "path": "/etc/nginx/nginx.conf",
                "content": "worker_connections 1024;\n",
                "preview": "remote_write_file /etc/nginx/nginx.conf",
            }
        ],
    )
    assert meta.operation_type == "modify_file"
    assert meta.risk_level == "medium"
    assert meta.require_typed_confirm is False
    assert "/etc/nginx/nginx.conf" in meta.summary_line or "修改文件" in meta.summary_line
    assert meta.diff_preview


def test_restart_medium():
    meta = build_confirm_card_meta(commands=["systemctl restart nginx"])
    assert meta.operation_type == "restart_service"
    assert meta.risk_level == "medium"
    assert typed_confirm_ok(meta.require_typed_confirm, "") is True


def test_typed_confirm_gate():
    assert typed_confirm_ok(False, "") is True
    assert typed_confirm_ok(True, "YES") is True
    assert typed_confirm_ok(True, "yes") is True
    assert typed_confirm_ok(True, "DELETE") is False
    assert typed_confirm_ok(True, "") is False


def test_ai_explanation_no_pushy_advice():
    text = build_ai_explanation(
        operation_type="remove",
        risk_level="high",
        command="rm /tmp/x",
        path="/tmp/x",
    )
    assert "强烈建议" not in text
    assert "风险提示" in text
    assert "仅作参考" in text or "专业判断" in text


def test_explain_on_demand_falls_back_template():
    out = explain_change_from_context(
        operation_type="remove",
        risk_level="high",
        command="rm /tmp/x",
        path="/tmp/x",
        prefer_llm=False,
    )
    assert out["source"] == "template"
    assert "风险提示" in out["text"]
    assert AI_DISCLAIMER.split("。")[0] in out["text"] or "仅作参考" in out["text"]


def test_permission_card_accepts_v2_fields():
    card = PermissionCard(
        permission_id="advm_test",
        command_preview="rm /tmp/x",
        host_id="h1",
        risk_level="high",
        operation_type="remove",
        summary_line="删除 · /tmp/x",
        require_typed_confirm=True,
        ai_explanation="test",
    )
    assert card.require_typed_confirm is True
    dumped = card.model_dump()
    assert dumped["risk_level"] == "high"
