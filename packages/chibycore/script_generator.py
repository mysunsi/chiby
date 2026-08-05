"""脚本生成器：将结构化动作转换为 shell 命令。"""
from __future__ import annotations

from typing import Optional

from .schemas import ActionType


def _sudo(ssh_password: str) -> str:
    """生成 sudo -S 管道前缀（带密码输入）。"""
    return f"echo '{ssh_password}' | sudo -S " if ssh_password else "sudo "


def build_command(action: ActionType, params: dict, ssh_password: Optional[str] = None) -> str:
    """根据动作类型和参数，生成可执行的 shell 命令。"""
    pw = ssh_password or ""
    s = _sudo(pw)

    if action == ActionType.CREATE_USER:
        username = params.get("username", "")
        password = params.get("password", "")
        # 1. 创建用户（或忽略已存在错误）
        # 2. 设置密码（echo user:pass | sudo chpasswd）
        # 3. 验证（id username）
        return (
            f"{s} bash -c \"useradd -m -s /bin/bash {username} 2>/dev/null || echo '用户已存在'; "
            f"echo '{username}:{password}' | {s} chpasswd; "
            f"{s} id {username}\""
        )

    if action == ActionType.DELETE_USER:
        username = params.get("username", "")
        return f"{s} userdel -r {username} 2>&1"

    if action == ActionType.CHANGE_PASSWORD:
        username = params.get("username", "")
        password = params.get("password", "")
        return f"echo '{username}:{password}' | {s} chpasswd 2>&1"

    if action == ActionType.CHECK_USER:
        username = params.get("username", "")
        return f"id {username} 2>&1"

    if action == ActionType.SYSTEM_INFO:
        return (
            "echo '=== 系统信息 ===' && uname -a && "
            "echo '=== 主机名 ===' && hostname && "
            "echo '=== 运行时间 ===' && uptime && "
            "echo '=== 当前用户 ===' && whoami && "
            "echo '=== OS 版本 ===' && grep PRETTY_NAME /etc/os-release"
        )

    if action == ActionType.DISK_USAGE:
        return (
            "echo '=== 磁盘使用情况 ===' && "
            "df -h --output=source,size,used,avail,pcent,target -x tmpfs -x devtmpfs -x squashfs 2>/dev/null || df -h && "
            "echo '=== inode 使用 ===' && df -i && "
            "echo '=== 挂载点 ===' && mount | grep '^/dev'"
        )

    if action == ActionType.MEMORY_USAGE:
        return (
            "echo '=== 内存使用情况 ===' && free -h && "
            "echo '=== 内存详情 ===' && "
            "cat /proc/meminfo | grep -E '^(MemTotal|MemFree|MemAvailable|Cached|Buffers|SwapTotal|SwapFree):' && "
            "echo '=== 内存使用 Top10 ===' && ps aux --sort=-%mem | awk 'NR==1{print} NR>1&&NR<=11{print}'"
        )

    if action == ActionType.CPU_USAGE:
        return (
            "echo '=== CPU 使用情况 ===' && top -bn1 | head -10 && "
            "echo '=== CPU 核心数 ===' && nproc && "
            "echo '=== 负载平均值 ===' && uptime && "
            "echo '=== CPU 温度 ===' && "
            "(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null && echo ' °C') || echo 'N/A'"
        )

    if action == ActionType.PROCESS_LIST:
        return (
            "echo '=== 进程 Top15（内存）===' && ps aux --sort=-%mem | awk 'NR==1{print} NR>1&&NR<=16{print}' && "
            "echo '=== 进程 Top15（CPU）===' && ps aux --sort=-%cpu | awk 'NR==1{print} NR>1&&NR<=16{print}'"
        )

    if action == ActionType.SERVICE_STATUS:
        svc = params.get("service", "ssh")
        return f"{s} systemctl status {svc} 2>&1 | head -20"

    if action == ActionType.SERVICE_START:
        svc = params.get("service", "ssh")
        return f"{s} systemctl start {svc} 2>&1"

    if action == ActionType.SERVICE_STOP:
        svc = params.get("service", "ssh")
        return f"{s} systemctl stop {svc} 2>&1"

    if action == ActionType.SERVICE_RESTART:
        svc = params.get("service", "ssh")
        return f"{s} systemctl restart {svc} 2>&1"

    if action == ActionType.DOCKER_PS:
        return "docker ps --format 'table {{.ID}}\\t{{.Image}}\\t{{.Status}}\\t{{.Names}}'"

    if action == ActionType.DOCKER_LOGS:
        container = params.get("container", "")
        lines = params.get("lines", 50)
        return f"docker logs --tail {lines} {container} 2>&1"

    if action == ActionType.PACKAGE_INSTALL:
        pkg = params.get("package", "")
        return (
            f"{s} bash -c 'apt-get update -qq 2>/dev/null; "
            f"DEBIAN_FRONTEND=noninteractive apt-get install -y {pkg} 2>&1 | tail -5'"
        )

    if action == ActionType.PACKAGE_REMOVE:
        pkg = params.get("package", "")
        return f"{s} apt-get remove -y {pkg} 2>&1 | tail -5"

    if action == ActionType.NETSTAT:
        return (
            "echo '=== TCP 监听端口 ===' && ss -tlnp && "
            "echo '=== 网络连接统计 ===' && ss -s"
        )

    if action == ActionType.PING:
        target = params.get("host", "8.8.8.8")
        return f"ping -c 4 {target} 2>&1"

    return f"echo '未知动作: {action.value}'"


def build_verify_command(action: ActionType, params: dict) -> Optional[str]:
    """生成验证命令，确认操作是否成功。"""
    if action == ActionType.CREATE_USER:
        username = params.get("username", "")
        return f"id {username} 2>&1"
    if action == ActionType.DELETE_USER:
        username = params.get("username", "")
        return f"id {username} 2>&1 || echo '用户已删除'"
    if action == ActionType.CHANGE_PASSWORD:
        username = params.get("username", "")
        return f"echo '{username}:*' | {params.get('username', 'id')} chpasswd -e 2>&1 || true"
    if action == ActionType.CHECK_USER:
        username = params.get("username", "")
        return f"id {username} 2>&1"
    if action == ActionType.SERVICE_STATUS:
        svc = params.get("service", "")
        return f"systemctl is-active {svc} 2>&1"
    if action == ActionType.PACKAGE_INSTALL:
        pkg = params.get("package", "")
        return f"dpkg -l {pkg} 2>&1 | grep '^ii'"
    if action == ActionType.DISK_USAGE:
        return "df -h | head -3"
    if action == ActionType.MEMORY_USAGE:
        return "free -h | head -3"
    return None


def build_rollback_command(action: ActionType, params: dict) -> Optional[str]:
    """生成回滚命令，撤销操作。"""
    if action == ActionType.CREATE_USER:
        username = params.get("username", "")
        return f"sudo userdel -r {username} 2>&1"
    if action == ActionType.PACKAGE_INSTALL:
        pkg = params.get("package", "")
        return f"sudo apt-get remove -y {pkg} 2>&1"
    return None
