from terminal.mobile.remote_tools import (
    RemoteToolCall,
    call_needs_confirmation,
    parse_remote_tool_calls,
)


def test_remote_run_shell_text_uses_command_not_host():
    text = (
        "<<<REMOTE_TOOL>>>\n"
        '{"tool":"remote_run","host":"5d418c8e","command":"sudo userdel -r sunsi2026"}\n'
        "<<<END_REMOTE_TOOL>>>"
    )
    calls = parse_remote_tool_calls(text)
    assert len(calls) == 1
    c = calls[0]
    assert c.shell_text == "sudo userdel -r sunsi2026"
    assert "5d418c8e" not in c.shell_text
    assert call_needs_confirmation(c, confirm_changes=False) is True


def test_constructed_remote_run_userdel_needs_card():
    c = RemoteToolCall(
        tool="remote_run",
        host="h1",
        command="sudo userdel -r sunsi2026",
        raw={"tool": "remote_run", "host": "h1", "command": "sudo userdel -r sunsi2026"},
    )
    assert call_needs_confirmation(c, confirm_changes=False) is True
