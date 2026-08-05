"""全能型 TASK_STATUS 契约与结案判定。"""

from terminal.mobile.remote_tools import remote_tools_preamble_addon, strip_remote_tool_protocol
from terminal.mobile.task_status import (
    classify_omnipotent_solo_turn,
    footer_label,
    parse_task_status,
    strip_task_status_protocol,
)


def test_parse_task_status_ended():
    text = (
        "安装完成。\n"
        "<<<TASK_STATUS>>>\n"
        '{"phase":"ended","reason":"completed","summary":"nvm 已装"}\n'
        "<<<END_TASK_STATUS>>>"
    )
    ts = parse_task_status(text)
    assert ts is not None
    assert ts.phase == "ended"
    assert ts.reason == "completed"
    assert "nvm" in ts.summary


def test_strip_task_status_and_remote_tool():
    text = (
        "说明\n"
        "<<<TASK_STATUS>>>{\"phase\":\"running\"}<<<END_TASK_STATUS>>>\n"
        "<<<REMOTE_TOOL>>>{\"tool\":\"host_list\"}<<<END_REMOTE_TOOL>>>\n"
    )
    assert "TASK_STATUS" not in strip_task_status_protocol(text)
    cleaned = strip_remote_tool_protocol(text)
    assert "REMOTE_TOOL" not in cleaned
    assert "TASK_STATUS" not in cleaned
    assert "说明" in cleaned


def test_classify_action_without_tool_is_incomplete():
    plan = "好，继续安装。先装 nvm，需要确认后再执行。"
    phase, reason, ts = classify_omnipotent_solo_turn(
        assistant_text=plan,
        has_remote_tools=False,
        intends_action=True,
        user_wants_continue=True,
    )
    assert phase == "ended"
    assert reason == "protocol_incomplete"
    assert ts is not None and ts.source == "inferred"


def test_classify_explicit_end_no_incomplete():
    text = (
        "本轮无需远端操作。\n"
        "<<<TASK_STATUS>>>\n"
        '{"phase":"ended","reason":"completed","summary":"无需操作"}\n'
        "<<<END_TASK_STATUS>>>"
    )
    phase, reason, ts = classify_omnipotent_solo_turn(
        assistant_text=text,
        has_remote_tools=False,
        intends_action=False,
        user_wants_continue=True,
    )
    assert phase == "ended"
    assert reason == "completed"
    assert ts is not None and ts.source == "protocol"


def test_classify_pure_answer():
    phase, reason, _ = classify_omnipotent_solo_turn(
        assistant_text="当前负载正常。",
        has_remote_tools=False,
        intends_action=False,
        user_wants_continue=False,
    )
    assert phase == "ended"
    assert reason == "answer"


def test_footer_incomplete_not_completed():
    assert "未完成" in footer_label(
        task_phase="ended", end_reason="protocol_incomplete"
    )
    assert "已完成" in footer_label(task_phase="ended", end_reason="completed")


def test_preamble_mentions_task_status():
    addon = remote_tools_preamble_addon(
        enabled=True, allowed_tools=["ssh_execute", "host_list"]
    )
    assert "TASK_STATUS" in addon
    assert "phase" in addon
