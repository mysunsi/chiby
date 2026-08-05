"""飞书审批卡片细化：签名、过期、回调响应。"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from terminal.mobile.im.feishu_card import (
    build_card_callback_response,
    build_permission_card_json,
    build_resolved_card_json,
    build_signed_action_value,
    extract_card_action,
    mark_permission_processed,
    verify_signed_action_value,
    was_permission_processed,
)
from terminal.mobile.models import PermissionCard


def test_sign_and_verify():
    secret = "s3cret"
    v = build_signed_action_value(
        secret=secret,
        conversation_id="feishu:oc1",
        permission_id="perm_1",
        choice="allow_once",
        host_id="h1",
        command="systemctl restart nginx",
        ttl_sec=300,
    )
    ok, reason = verify_signed_action_value(secret, v)
    assert ok and reason == ""

    bad = dict(v)
    bad["choice"] = "deny"
    ok2, reason2 = verify_signed_action_value(secret, bad)
    assert not ok2
    assert "签名" in reason2 or "篡改" in reason2


def test_expired_card():
    secret = "k"
    v = build_signed_action_value(
        secret=secret,
        conversation_id="feishu:oc1",
        permission_id="perm_x",
        choice="deny",
        ttl_sec=60,
    )
    v["exp"] = int(time.time()) - 10
    v["sig"] = __import__("terminal.mobile.im.feishu_card", fromlist=["sign_card_value"]).sign_card_value(
        secret, v,
    )
    ok, reason = verify_signed_action_value(secret, v)
    assert not ok
    assert "过期" in reason


def test_permission_card_has_behaviors_and_sig():
    card = PermissionCard(
        permission_id="perm_a",
        title="待确认",
        command_preview="df -h",
        host_id="h1",
    )
    body = build_permission_card_json(
        card,
        conversation_id="feishu:oc",
        secret="abc",
        requester="ou_x",
    )
    assert body["header"]["template"] == "orange"
    actions = body["elements"][1]["actions"]
    assert len(actions) == 2
    assert actions[0]["behaviors"][0]["type"] == "callback"
    assert actions[0]["value"]["sig"]
    assert actions[0]["value"]["command"] == "df -h"


def test_resolved_card_and_toast():
    card = build_resolved_card_json(
        choice="allow_once",
        host_id="h1",
        permission_id="perm_1",
        command="uptime",
        result_text="ok",
        ok=True,
    )
    assert "已允许" in card["header"]["title"]["content"]
    resp = build_card_callback_response(
        toast_type="success",
        toast_content="已允许并执行",
        card=card,
    )
    assert resp["toast"]["type"] == "success"
    assert resp["card"]["type"] == "raw"


def test_extract_card_action_from_trigger():
    body = {
        "schema": "2.0",
        "header": {"event_type": "card.action.trigger"},
        "event": {
            "operator": {"open_id": "ou_op"},
            "context": {"open_chat_id": "oc_chat"},
            "action": {
                "value": {
                    "mobile_im": "1",
                    "conversation_id": "feishu:oc_chat",
                    "permission_id": "perm_z",
                    "choice": "allow_once",
                    "host_id": "h1",
                    "command": "df -h",
                    "exp": int(time.time()) + 100,
                    "sig": "x",
                },
            },
        },
    }
    ex = extract_card_action(body)
    assert ex is not None
    assert ex["external_user_id"] == "ou_op"
    assert ex["chat_id"] == "oc_chat"
    assert ex["permission_id"] == "perm_z"


def test_idempotent_mark():
    pid = "perm_idem_" + str(time.time())
    assert mark_permission_processed(pid) is True
    assert was_permission_processed(pid) is True
    assert mark_permission_processed(pid) is False


@pytest.mark.asyncio
async def test_card_action_webhook_rejects_bad_sig(monkeypatch):
    from fastapi import FastAPI

    from terminal.mobile.im import routes as routes_mod
    from terminal.mobile.im.config import FeishuConfig, MobileImConfig, WecomConfig

    monkeypatch.setattr(
        routes_mod,
        "load_mobile_im_config",
        lambda: MobileImConfig(
            feishu=FeishuConfig(
                enabled=True,
                verification_token="vt",
                card_secret="card-secret",
                card_require_acl=False,
            ),
            wecom=WecomConfig(enabled=False),
        ),
    )
    app = FastAPI()
    routes_mod.register_mobile_im_routes(app)
    client = TestClient(app)
    r = client.post(
        "/api/mobile/im/feishu/webhook",
        json={
            "schema": "2.0",
            "header": {"event_type": "card.action.trigger", "token": "vt"},
            "event": {
                "operator": {"open_id": "ou_1"},
                "context": {"open_chat_id": "oc_1"},
                "action": {
                    "value": {
                        "mobile_im": "1",
                        "conversation_id": "feishu:oc_1",
                        "permission_id": "perm_bad",
                        "choice": "allow_once",
                        "exp": int(time.time()) + 100,
                        "sig": "deadbeef",
                    },
                },
            },
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data.get("toast", {}).get("type") == "error"
    assert "签名" in (data.get("toast", {}).get("content") or "") or "篡改" in (
        data.get("toast", {}).get("content") or ""
    )
