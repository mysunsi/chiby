"""agent_thought_chunk：保留词间空格分片。"""

from __future__ import annotations

import pytest

from terminal.hermes_bridge.acp_session import HermesTabBridge


@pytest.mark.asyncio
async def test_thought_chunk_preserves_space_tokens():
    sent: list[dict] = []
    bridge = object.__new__(HermesTabBridge)

    async def _send_ws(payload):  # type: ignore[no-untyped-def]
        sent.append(payload)

    bridge._send_ws = _send_ws  # type: ignore[method-assign]

    for piece in ("The", " ", "user", " ", "is", " ", "asking"):
        await bridge._on_session_update(
            {
                "params": {
                    "update": {
                        "sessionUpdate": "agent_thought_chunk",
                        "content": {"text": piece},
                    },
                },
            },
        )

    deltas = [
        p["delta"]
        for p in sent
        if p.get("type") == "hermes.chunk" and p.get("stream_id") == "hermes-thought"
    ]
    assert "".join(deltas) == "The user is asking"


@pytest.mark.asyncio
async def test_thought_chunk_no_forced_newline_on_word():
    sent: list[dict] = []
    bridge = object.__new__(HermesTabBridge)

    async def _send_ws(payload):  # type: ignore[no-untyped-def]
        sent.append(payload)

    bridge._send_ws = _send_ws  # type: ignore[method-assign]

    await bridge._on_session_update(
        {
            "params": {
                "update": {
                    "sessionUpdate": "agent_thought_chunk",
                    "content": {"text": "Hello"},
                },
            },
        },
    )
    assert sent[0]["delta"] == "Hello"
