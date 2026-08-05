"""多机汇总一行摘要：free/df 不能只留表头。"""

from terminal.mobile.job_orchestrator import _one_line_result
from terminal.mobile.models import ExecResult


def test_one_line_free_h_uses_mem_row_not_header():
    er = ExecResult(
        ok=True,
        host_id="h1",
        command="free -h",
        exit_code=0,
        stdout_tail=(
            "              total        used        free      shared  buff/cache   available\n"
            "Mem:          3.7Gi       2.3Gi       112Mi        12Mi       1.3Gi       1.1Gi\n"
            "Swap:            0B          0B          0B\n"
        ),
    )
    line = _one_line_result(er)
    assert "total used free" not in line.lower()
    assert "1.1Gi" in line or "可用" in line
    assert "3.7Gi" in line or "总量" in line


def test_one_line_df_h_prefers_root_not_header():
    er = ExecResult(
        ok=True,
        host_id="h1",
        command="df -h",
        exit_code=0,
        stdout_tail=(
            "Filesystem      Size  Used Avail Use% Mounted on\n"
            "tmpfs           379M  1.2M  378M   1% /run\n"
            "/dev/vda3        40G   17G   21G  45% /\n"
            "tmpfs           1.9G     0  1.9G   0% /dev/shm\n"
        ),
    )
    line = _one_line_result(er)
    assert "Filesystem" not in line
    assert "21G" in line or "45%" in line
    assert "/" in line


def test_one_line_winrm_json_kept():
    er = ExecResult(
        ok=True,
        host_id="h2",
        command="… FreeGB …",
        exit_code=0,
        stdout_tail='{"TotalGB":4,"FreeGB":0.92}',
    )
    assert "0.92" in _one_line_result(er)


def test_one_line_top_mem_processes_json():
    er = ExecResult(
        ok=True,
        host_id="h2",
        command="Get-Process | Sort-Object WorkingSet64 …",
        exit_code=0,
        stdout_tail=(
            '[{"Name":"node","Id":7480,"MemoryMB":549.51},'
            '{"Name":"explorer","Id":6896,"MemoryMB":384.29}]'
        ),
    )
    line = _one_line_result(er)
    assert "node" in line
    assert "549" in line


def test_one_line_ps_aux_skips_header():
    er = ExecResult(
        ok=True,
        host_id="h1",
        command="ps aux --sort=-%mem | head -n 15",
        exit_code=0,
        stdout_tail=(
            "USER         PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND\n"
            "root        1234  0.1 12.3 999999 99999 ?        Ssl  Jan01   1:00 /usr/bin/java\n"
            "www-data    2222  0.0  5.0 111111 11111 ?        S    Jan01   0:10 nginx\n"
        ),
    )
    line = _one_line_result(er)
    assert "USER" not in line or "java" in line
    assert "java" in line
    assert "12.3" in line
