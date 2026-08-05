"""自然语言解析器：将用户输入解析为结构化动作和参数。"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from .schemas import ActionType


# ─── 动作关键词映射（按优先级排序）────────────────────────────────────────

# 中文字符范围（匹配中文词语之间的任意汉字序列）
_CN = r"[\u4e00-\u9fff]"
_CN_MULTI = r"[\u4e00-\u9fff]*"

ACTION_PATTERNS: List[Tuple[ActionType, List[str]]] = [
    (ActionType.CREATE_USER, [
        rf"创建{_CN_MULTI}账号", rf"创建{_CN_MULTI}用户",
        rf"新建{_CN_MULTI}账号", rf"新增{_CN_MULTI}用户",
        r"adduser", r"useradd",
    ]),
    (ActionType.DELETE_USER, [
        rf"删除{_CN_MULTI}账号", rf"删除{_CN_MULTI}用户",
        r"deluser", r"userdel",
    ]),
    (ActionType.CHANGE_PASSWORD, [
        rf"修改{_CN_MULTI}密码", rf"更改{_CN_MULTI}密码",
        rf"设置{_CN_MULTI}密码", r"passwd",
    ]),
    (ActionType.CHECK_USER, [
        rf"查看{_CN_MULTI}账号", rf"查询{_CN_MULTI}用户",
        rf"验证{_CN_MULTI}账号", rf"确认{_CN_MULTI}账号",
    ]),
    # 资源监控类（放在 SYSTEM_INFO 前面，避免被"主机信息"等通用词拦截）
    (ActionType.DISK_USAGE, [
        rf"磁盘{_CN_MULTI}", rf"硬盘{_CN_MULTI}", rf"存储{_CN_MULTI}",
        r"df", rf"文件系统{_CN_MULTI}", r"disk",
    ]),
    (ActionType.MEMORY_USAGE, [
        rf"内存{_CN_MULTI}", r"RAM", r"mem", r"free",
        rf"使用情况", r"运作状态",
    ]),
    (ActionType.CPU_USAGE, [
        r"CPU", rf"处理器{_CN_MULTI}", r"cpu", rf"负载{_CN_MULTI}",
        rf"使用率{_CN_MULTI}",
    ]),
    (ActionType.PROCESS_LIST, [
        rf"进程{_CN_MULTI}", r"ps\s*", rf"任务{_CN_MULTI}列表",
    ]),
    (ActionType.SYSTEM_INFO, [
        rf"系统{_CN_MULTI}信息", rf"系统{_CN_MULTI}概况",
        rf"主机{_CN_MULTI}信息", r"uname", r"hostname",
        r"基本信息",
    ]),
    (ActionType.SERVICE_STATUS, [
        rf"服务{_CN_MULTI}状态", r"systemctl\s*status", r"service\s*status",
    ]),
    (ActionType.SERVICE_START, [
        rf"启动{_CN_MULTI}服务", rf"开启{_CN_MULTI}服务",
        r"systemctl\s*start", r"service\s*start",
    ]),
    (ActionType.SERVICE_STOP, [
        rf"停止{_CN_MULTI}服务", rf"关闭{_CN_MULTI}服务",
        r"systemctl\s*stop", r"service\s*stop",
    ]),
    (ActionType.SERVICE_RESTART, [
        rf"重启{_CN_MULTI}服务", r"systemctl\s*restart", r"service\s*restart",
    ]),
    (ActionType.DOCKER_PS, [
        r"docker\s*容器", r"docker\s*ps", rf"容器{_CN_MULTI}列表",
    ]),
    (ActionType.DOCKER_LOGS, [
        r"docker\s*日志", rf"容器{_CN_MULTI}日志", r"docker\s*logs",
    ]),
    (ActionType.PACKAGE_INSTALL, [
        r"安装([a-zA-Z][a-zA-Z0-9._-]+)",
        r"apt\s*install", r"yum\s*install", r"dnf\s*install",
        r"pip\s*install",
    ]),
    (ActionType.PACKAGE_REMOVE, [
        rf"卸载{_CN_MULTI}", r"apt\s*remove", r"yum\s*remove",
    ]),
    (ActionType.FILE_READ, [
        rf"查看{_CN_MULTI}文件", r"cat\s*", rf"文件{_CN_MULTI}内容",
    ]),
    (ActionType.FILE_WRITE, [
        rf"写入{_CN_MULTI}文件", rf"创建{_CN_MULTI}文件",
    ]),
    (ActionType.NETSTAT, [
        rf"网络{_CN_MULTI}连接", rf"端口{_CN_MULTI}监听",
        r"netstat", r"ss\s*-tln",
    ]),
    (ActionType.PING, [
        r"ping", rf"连通性{_CN_MULTI}测试",
    ]),
]


# ─── 参数提取正则 ────────────────────────────────────────────────────────────

USERNAME_PATTERNS = [
    r"(?:用户名|账号|用户|user(?:name)?)\s*[:：]?\s*([a-zA-Z][a-zA-Z0-9_-]{0,31})",
    r"(?:创建|添加|新建|给)\s*(?:我|一个)?(?:账号|用户)?\s*([a-zA-Z][a-zA-Z0-9_-]{0,31})",
    r"username\s*=\s*([^\s]+)",
]

PASSWORD_PATTERNS = [
    r"密码\s*[:：]\s*([a-zA-Z0-9@#$%^&*!]{6,32})",
    r"password\s*[:：]\s*([a-zA-Z0-9@#$%^&*!]{6,32})",
    r"passwd\s*[:：]\s*([a-zA-Z0-9@#$%^&*!]{6,32})",
]

HOST_PATTERNS = [
    r"(?:主机|host|服务器)\s*[:：]?\s*([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})",
    r"([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})",
]

SERVICE_PATTERNS = [
    r"(?:服务|service)\s*[:：]?\s*([a-zA-Z][a-zA-Z0-9_-]+)",
]

FILE_PATTERNS = [
    r"(?:文件|path)\s*[:：]?\s*(/[^\s]+)",
    r"(?:读取|查看|写入)\s+([/\w.-]+)",
]

PACKAGE_PATTERNS = [
    r"(?:安装|卸载)([a-zA-Z][a-zA-Z0-9._-]+)",
]


def parse_command(command: str) -> Tuple[ActionType, Dict]:
    """解析自然语言命令，返回动作类型和参数字典。"""
    command_lower = command.lower().strip()

    # 1. 识别动作类型
    action = ActionType.UNKNOWN
    for action_type, patterns in ACTION_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, command_lower):
                action = action_type
                break
        if action != ActionType.UNKNOWN:
            break

    # 2. 提取参数
    params = {}

    # 密码（优先提取，避免被其他正则贪心匹配）
    for pattern in PASSWORD_PATTERNS:
        m = re.search(pattern, command)
        if m:
            params["password"] = m.group(1).strip()
            break

    # 用户名
    for pattern in USERNAME_PATTERNS:
        m = re.search(pattern, command)
        if m:
            params["username"] = m.group(1).strip()
            break

    # 主机
    for pattern in HOST_PATTERNS:
        m = re.search(pattern, command)
        if m:
            params["host"] = m.group(1).strip()
            break

    # 服务名
    if action in (ActionType.SERVICE_STATUS, ActionType.SERVICE_START,
                  ActionType.SERVICE_STOP, ActionType.SERVICE_RESTART,
                  ActionType.DOCKER_LOGS):
        for pattern in SERVICE_PATTERNS:
            m = re.search(pattern, command)
            if m:
                params["service"] = m.group(1).strip()
                break
        if "service" not in params:
            for svc in ["ssh", "nginx", "apache2", "docker", "mysql",
                        "postgres", "redis", "firewalld", "ufw"]:
                if svc in command_lower:
                    params["service"] = svc
                    break

    # 文件路径
    for pattern in FILE_PATTERNS:
        m = re.search(pattern, command)
        if m:
            params["filepath"] = m.group(1).strip()
            break

    # 包名
    if action in (ActionType.PACKAGE_INSTALL, ActionType.PACKAGE_REMOVE):
        for pattern in PACKAGE_PATTERNS:
            m = re.search(pattern, command)
            if m and m.lastindex:
                params["package"] = m.group(1).strip()
                break

    # 资源汇总请求
    if any(w in command_lower for w in ["资源", "使用情况", "运作状态", "统计", "资源使用"]):
        if any(w in command_lower for w in ["磁盘", "硬盘", "df"]):
            params["report_type"] = "disk"
        if any(w in command_lower for w in ["内存", "RAM", "mem"]):
            params["report_type"] = "memory"
        if any(w in command_lower for w in ["CPU", "处理器", "cpu", "负载"]):
            params["report_type"] = "cpu"
        if "资源" in command_lower and ("使用情况" in command_lower or "使用" in command_lower):
            params["report_type"] = "all"

    return action, params


def describe_action(action: ActionType, params: Dict) -> str:
    """生成人类可读的动作描述。"""
    if action == ActionType.CREATE_USER:
        u = params.get("username", "?")
        return f"在目标主机创建用户账号「{u}」"
    if action == ActionType.DELETE_USER:
        return f"删除用户「{params.get('username', '?')}」"
    if action == ActionType.CHANGE_PASSWORD:
        return f"修改用户「{params.get('username', '?')}」的密码"
    if action == ActionType.CHECK_USER:
        return f"验证用户「{params.get('username', '?')}」是否存在"
    if action == ActionType.SYSTEM_INFO:
        return "获取系统基本信息"
    if action == ActionType.DISK_USAGE:
        return "获取磁盘使用情况"
    if action == ActionType.MEMORY_USAGE:
        return "获取内存使用情况"
    if action == ActionType.CPU_USAGE:
        return "获取 CPU 使用率"
    if action == ActionType.PROCESS_LIST:
        return "列出运行中的进程"
    if action == ActionType.SERVICE_STATUS:
        return f"查看服务「{params.get('service', '?')}」状态"
    if action == ActionType.SERVICE_START:
        return f"启动服务「{params.get('service', '?')}」"
    if action == ActionType.SERVICE_STOP:
        return f"停止服务「{params.get('service', '?')}」"
    if action == ActionType.SERVICE_RESTART:
        return f"重启服务「{params.get('service', '?')}」"
    if action == ActionType.DOCKER_PS:
        return "列出 Docker 容器"
    if action == ActionType.DOCKER_LOGS:
        return f"查看容器「{params.get('service', '?')}」日志"
    if action == ActionType.PACKAGE_INSTALL:
        return f"安装软件包「{params.get('package', '?')}」"
    if action == ActionType.NETSTAT:
        return "查看网络监听端口"
    return f"执行动作: {action.value}"
