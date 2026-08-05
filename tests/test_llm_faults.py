"""大模型配置故障分类：前端应看到人话而非「空响应」。"""
from __future__ import annotations

from terminal.hermes_bridge.llm_faults import (
    classify_llm_config_fault,
    llm_config_preflight_error,
)


def test_classify_no_llm_provider():
    msg = classify_llm_config_fault(
        "ACP RPC session/new 错误: -32603 | No LLM provider configured."
    )
    assert msg is not None
    assert "大模型" in msg
    assert "API Key" in msg or "密钥" in msg or "未配置" in msg


def test_classify_minimax_401():
    stderr = (
        "Non-retryable client error: Error code: 401 - "
        "{'type': 'error', 'error': {'type': 'authorized_error', "
        "'message': \"login fail: Please carry the API secret key "
        "in the 'Authorization' field of the request header (1004)\", "
        "'http_code': '401'}}"
    )
    msg = classify_llm_config_fault(stderr)
    assert msg is not None
    assert "鉴权" in msg or "API Key" in msg


def test_classify_unrelated_is_none():
    assert classify_llm_config_fault("timeout waiting for session/prompt") is None
    assert classify_llm_config_fault("") is None


def test_preflight_custom_missing_key_remote():
    err = llm_config_preflight_error(
        {
            "mode": "custom",
            "model": "MiniMax-M2.5-highspeed",
            "base_url": "https://api.minimaxi.com/v1",
            "api_key": "",
        }
    )
    assert err is not None
    assert "API Key" in err


def test_preflight_local_ollama_allows_empty_key():
    err = llm_config_preflight_error(
        {
            "mode": "custom",
            "model": "qwen2.5:0.5b",
            "base_url": "http://localhost:11434/v1",
            "api_key": "",
        }
    )
    assert err is None


def test_preflight_ok_with_key():
    err = llm_config_preflight_error(
        {
            "mode": "custom",
            "model": "MiniMax-M2.5-highspeed",
            "base_url": "https://api.minimaxi.com/v1",
            "api_key": "sk-test",
        }
    )
    assert err is None


def test_transport_error_excludes_llm_auth():
    from terminal.hermes_bridge.acp_session import _looks_like_acp_transport_error

    auth = "大模型鉴权失败（API Key 缺失或无效）：请到 LLM 设置检查密钥后重试"
    assert _looks_like_acp_transport_error(auth) is False
    assert _looks_like_acp_transport_error(
        "Chiby ACP stdout 已结束（子进程退出、崩溃或关闭了 stdout）。"
    )
