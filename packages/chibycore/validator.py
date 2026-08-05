"""验证器：校验命令执行结果是否符合预期。"""
from __future__ import annotations

import re
from typing import Optional

from .schemas import ActionType
from .ssh_executor import CmdResult


class ValidationResult:
    def __init__(self, passed: bool, reason: str = "", details: str = ""):
        self.passed = passed
        self.reason = reason
        self.details = details

    def __bool__(self) -> bool:
        return self.passed

    def __repr__(self) -> str:
        status = "✅ PASS" if self.passed else "❌ FAIL"
        return f"{status} | {self.reason}"


def validate(action: ActionType, result: CmdResult, params: dict) -> ValidationResult:
    """根据动作类型和执行结果，判断是否成功。"""
    if not result.success and result.exit_code != 0:
        return ValidationResult(
            passed=False,
            reason="命令执行失败",
            details=f"exit_code={result.exit_code}\n{result.stderr[:200]}",
        )

    output = result.stdout + result.stderr

    if action == ActionType.CREATE_USER:
        username = params.get("username", "")
        if not username:
            return ValidationResult(False, "缺少用户名参数")
        if re.search(rf"^{username}:", output, re.MULTILINE):
            return ValidationResult(True, f"用户 {username} 创建成功并验证通过", result.stdout)
        if "already exists" in output.lower() or "用户已存在" in output:
            return ValidationResult(True, f"用户 {username} 已存在（无需重复创建）", result.stdout)
        return ValidationResult(False, f"未在 /etc/passwd 找到用户 {username}", output)

    if action == ActionType.DELETE_USER:
        username = params.get("username", "")
        if not username:
            return ValidationResult(False, "缺少用户名")
        if f"no such user" in output.lower() or "不存在" in output or "not found" in output.lower():
            return ValidationResult(True, f"用户 {username} 已删除或不存在", output)
        if result.exit_code == 0:
            return ValidationResult(True, f"用户 {username} 已删除", output)
        return ValidationResult(False, f"删除用户失败", output)

    if action == ActionType.CHANGE_PASSWORD:
        if "success" in output.lower() or "成功" in output or result.exit_code == 0:
            return ValidationResult(True, "密码修改成功", output)
        return ValidationResult(False, "密码修改可能失败", output)

    if action == ActionType.CHECK_USER:
        username = params.get("username", "")
        if re.search(rf"uid=\d+\({username}\)", output):
            return ValidationResult(True, f"用户 {username} 存在", output)
        if "no such user" in output.lower() or "not found" in output.lower():
            return ValidationResult(False, f"用户 {username} 不存在", output)
        return ValidationResult(False, f"无法确认用户 {username} 状态", output)

    if action == ActionType.SERVICE_STATUS:
        if "active (running)" in output or "Active: active" in output:
            return ValidationResult(True, "服务运行中", output[:300])
        if "inactive" in output.lower():
            return ValidationResult(False, "服务未运行", output[:300])
        return ValidationResult(False, "无法确定服务状态", output[:300])

    if action == ActionType.SERVICE_START:
        if result.exit_code == 0:
            return ValidationResult(True, "服务启动命令执行成功", output)
        return ValidationResult(False, "服务启动失败", output)

    if action == ActionType.SERVICE_STOP:
        if result.exit_code == 0:
            return ValidationResult(True, "服务停止命令执行成功", output)
        return ValidationResult(False, "服务停止失败", output)

    if action == ActionType.SERVICE_RESTART:
        if result.exit_code == 0:
            return ValidationResult(True, "服务重启命令执行成功", output)
        return ValidationResult(False, "服务重启失败", output)

    if action == ActionType.DISK_USAGE:
        lines = [l for l in output.split("\n") if l.strip()]
        if len(lines) >= 3:
            return ValidationResult(True, f"获取到 {len(lines)} 行磁盘数据", "\n".join(lines[:5]))
        return ValidationResult(False, "磁盘数据为空或异常", output)

    if action == ActionType.MEMORY_USAGE:
        if "Mem" in output or "memory" in output.lower():
            return ValidationResult(True, "内存数据获取成功", output[:300])
        return ValidationResult(False, "内存数据获取失败", output)

    if action == ActionType.CPU_USAGE:
        if "Cpu" in output or "cpu" in output.lower() or "load" in output.lower():
            return ValidationResult(True, "CPU 数据获取成功", output[:300])
        return ValidationResult(False, "CPU 数据获取失败", output)

    if action == ActionType.DOCKER_PS:
        lines = output.strip().split("\n")
        return ValidationResult(True, f"Docker 容器数据获取成功（{len(lines)} 行）", output[:300])

    if action == ActionType.PACKAGE_INSTALL:
        package = params.get("package", "")
        if f"ii  {package}" in output or result.exit_code == 0:
            return ValidationResult(True, f"软件包 {package} 安装成功", output[:300])
        return ValidationResult(False, f"软件包 {package} 安装可能失败", output[:300])

    if action == ActionType.NETSTAT:
        lines = [l for l in output.split("\n") if l.strip()]
        if len(lines) >= 2:
            return ValidationResult(True, f"网络数据获取成功（{len(lines)} 行）", "\n".join(lines[:5]))
        return ValidationResult(False, "网络数据获取失败", output)

    if action == ActionType.SYSTEM_INFO:
        if "linux" in output.lower() or "ubuntu" in output.lower() or result.exit_code == 0:
            return ValidationResult(True, "系统信息获取成功", output[:300])
        return ValidationResult(False, "系统信息获取失败", output)

    # 默认：exit_code == 0 即认为成功
    if result.exit_code == 0:
        return ValidationResult(True, "命令执行成功", output[:200])
    return ValidationResult(False, "命令执行失败", output[:200])


def format_resource_report(output: str, action: ActionType) -> str:
    """将原始命令输出格式化为可读的 Markdown 表格。"""
    lines = [l.strip() for l in output.split("\n") if l.strip()]

    if action == ActionType.DISK_USAGE:
        headers = ["文件系统", "总大小", "已用", "可用", "使用率", "挂载点"]
        rows = []
        for line in lines:
            parts = re.split(r"\s+", line)
            if len(parts) >= 6 and not line.startswith("Filesystem"):
                rows.append(parts[:6])
        if not rows:
            return f"```\n{output}\n```"
        md = "| " + " | ".join(headers) + " |\n"
        md += "| " + " | ".join(["---"] * len(headers)) + " |\n"
        for r in rows:
            md += "| " + " | ".join(r[:6]) + " |\n"
        return md

    if action in (ActionType.MEMORY_USAGE, ActionType.CPU_USAGE):
        return f"```\n{output}\n```"

    return f"```\n{output}\n```"
