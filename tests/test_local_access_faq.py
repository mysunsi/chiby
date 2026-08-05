"""本机访问能力 FAQ 快路径。"""

from __future__ import annotations

from terminal.mobile.hermes_protocol import (
    local_access_faq_reply,
    looks_like_local_access_faq,
)


def test_looks_like_local_access_faq():
    assert looks_like_local_access_faq("你能访问本机系统进行维护或读取操作吗？")
    assert looks_like_local_access_faq("可以操作本地系统吗")
    assert not looks_like_local_access_faq("本机磁盘还剩多少")  # 指目标机口语时也可能误伤；无「能/可以」
    assert not looks_like_local_access_faq("查一下内存")


def test_local_access_faq_reply_denies_local():
    text = local_access_faq_reply(host_id="5d418c8e")
    assert "不能" in text
    assert "5d418c8e" in text
    assert "本机" in text
