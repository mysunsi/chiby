"""短确认：严格整句白名单，避免新任务问法误判。"""

from terminal.mobile.orchestrator import _is_confirm_phrase


def test_confirm_whitelist_exact():
    assert _is_confirm_phrase("执行")
    assert _is_confirm_phrase("执行。")
    assert _is_confirm_phrase("执行检查")
    assert _is_confirm_phrase("检查吧")
    assert _is_confirm_phrase("好的")
    assert _is_confirm_phrase("继续")
    assert _is_confirm_phrase("可以执行")
    assert _is_confirm_phrase("确认")
    assert _is_confirm_phrase("开始执行")
    assert _is_confirm_phrase("ok")


def test_ambiguous_or_new_task_not_confirm():
    # 曾误伤：后续建议整句
    assert not _is_confirm_phrase("检查一下这台机器的应用日志或硬件告警")
    assert not _is_confirm_phrase("检查一下应用日志")
    assert not _is_confirm_phrase("这台机器最近有什么告警吗")
    assert not _is_confirm_phrase("当前主机名")
    assert not _is_confirm_phrase("删除文件 abc.dat")
    assert not _is_confirm_phrase("nginx 状态")
    # 含任务对象，即使带「执行/开始」
    assert not _is_confirm_phrase("执行磁盘检查")
    assert not _is_confirm_phrase("开始检查内存")
    assert not _is_confirm_phrase("请执行 nginx 重启")
    # 过宽旧词：不再单独认
    assert not _is_confirm_phrase("可以")
    assert not _is_confirm_phrase("行")
    assert not _is_confirm_phrase("对")
    assert not _is_confirm_phrase("是的")
    assert not _is_confirm_phrase("要")
    assert not _is_confirm_phrase("开始")
    assert not _is_confirm_phrase("检查")
    assert not _is_confirm_phrase("y")  # 单字母过宽，已移出白名单
