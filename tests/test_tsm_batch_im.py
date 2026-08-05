"""批量确认 + IM 富卡三层对齐。"""

from terminal.mobile.confirm_card_meta import (
    apply_selected_indices,
    build_command_items,
    build_confirm_card_meta,
)
from terminal.mobile.im.feishu_card import build_permission_card_json, extract_card_action
from terminal.mobile.im.wecom import try_parse_wecom_permission_reply
from terminal.mobile.models import PermissionCard


def test_build_command_items_and_filter():
    items = build_command_items(
        commands=["uptime", "systemctl restart nginx", "rm -rf /tmp/x"]
    )
    assert len(items) == 3
    assert items[0]["index"] == 0
    assert apply_selected_indices(["a", "b", "c"], None) == ["a", "b", "c"]
    assert apply_selected_indices(["a", "b", "c"], [1]) == ["b"]
    assert apply_selected_indices(["a", "b", "c"], []) == []


def test_confirm_meta_includes_command_items():
    meta = build_confirm_card_meta(
        commands=["echo 1", "systemctl restart nginx"]
    )
    cf = meta.as_card_fields()
    assert len(cf["command_items"]) == 2
    assert "共 2 条" in meta.summary_line


def test_feishu_card_three_layers_and_inputs():
    card = PermissionCard(
        permission_id="perm_t",
        title="待确认变更",
        command_preview="rm -rf /tmp/x",
        host_id="h1",
        host_label="prod-1",
        risk_level="high",
        risk_label="高风险",
        operation_label="删除",
        summary_line="将删除临时目录",
        detail_command="rm -rf /tmp/x",
        impact_hint="不可逆",
        require_typed_confirm=True,
        require_otp=True,
        command_items=[
            {
                "index": 0,
                "preview": "rm -rf /tmp/x",
                "risk_level": "high",
                "risk_label": "高风险",
            }
        ],
    )
    body = build_permission_card_json(
        card, conversation_id="feishu:oc1", secret="abc", requester="ou_1"
    )
    assert body["header"]["template"] == "red"
    md = body["body"]["elements"][0]["content"]
    assert "摘要" in md
    assert "详情" in md
    assert "高风险" in md
    names = {
        e.get("name")
        for e in body["body"]["elements"]
        if e.get("tag") == "input"
    }
    assert "typed_confirm" in names
    assert "otp_code" in names


def test_feishu_extract_form_value():
    body = {
        "event": {
            "operator": {"open_id": "ou_x"},
            "context": {"open_chat_id": "oc_1"},
            "action": {
                "value": {
                    "mobile_im": "1",
                    "conversation_id": "feishu:oc_1",
                    "permission_id": "perm_1",
                    "choice": "allow_once",
                },
                "form_value": {"typed_confirm": "YES", "otp_code": "123456"},
            },
        }
    }
    ex = extract_card_action(body)
    assert ex is not None
    assert ex["typed_confirm"] == "YES"
    assert ex["otp_code"] == "123456"


def test_wecom_parse_yes_otp():
    p = try_parse_wecom_permission_reply("允许 perm_abc YES 654321")
    assert p is not None
    assert p["permission_id"] == "perm_abc"
    assert p["typed_confirm"] == "YES"
    assert p["otp_code"] == "654321"
    d = try_parse_wecom_permission_reply("拒绝 perm_abc")
    assert d["choice"] == "deny"
