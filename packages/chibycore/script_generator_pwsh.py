"""PowerShell 单行命令生成（WinRM 会话下的只读巡检类动作）。"""
from __future__ import annotations

from typing import Optional

from .schemas import ActionType


def build_powershell_command(
    action: ActionType,
    params: dict,
    _ssh_password: Optional[str] = None,
) -> Optional[str]:
    """返回可经 WinRM 执行的 PowerShell 片段；不支持的动作返回 None。"""
    if action == ActionType.SYSTEM_INFO:
        return (
            "Write-Output '=== OS ==='; "
            "Get-CimInstance Win32_OperatingSystem | "
            "Select-Object Caption,Version,OSArchitecture | Format-List"
        )
    if action == ActionType.DISK_USAGE:
        return "Get-PSDrive -PSProvider FileSystem | Where-Object Used -gt 0 | Format-Table Name,Used,Free,@{L='Use%';E={[math]::Round(100*$_.Used/($_.Used+$_.Free),1)}} -AutoSize"
    if action == ActionType.MEMORY_USAGE:
        return (
            "$os = Get-CimInstance Win32_OperatingSystem; "
            "Write-Output ('TotalMB=' + [math]::Round($os.TotalVisibleMemorySize/1024,1)); "
            "Write-Output ('FreeMB=' + [math]::Round($os.FreePhysicalMemory/1024,1))"
        )
    if action == ActionType.CPU_USAGE:
        return "Get-Counter '\\Processor(_Total)\\% Processor Time' -SampleInterval 1 -MaxSamples 1 | Select-Object -ExpandProperty CounterSamples | Format-List CookedValue"
    if action == ActionType.PROCESS_LIST:
        return "Get-Process | Sort-Object WS -Descending | Select-Object -First 15 Name,Id,@{N='WS(MB)';E={[math]::Round($_.WS/1MB,1)}} | Format-Table -AutoSize"
    if action == ActionType.NETSTAT:
        return "netstat -ano | Select-Object -First 40"
    if action == ActionType.DOCKER_PS:
        return "docker ps --format 'table {{.ID}}\\t{{.Image}}\\t{{.Status}}\\t{{.Names}}' 2>&1"
    if action == ActionType.SERVICE_STATUS:
        svc = params.get("service", "ssh")
        return f"Get-Service -Name '{svc}' -ErrorAction SilentlyContinue | Format-List *"
    if action == ActionType.PING:
        target = params.get("host", "8.8.8.8")
        return f"Test-Connection -ComputerName {target} -Count 2 -ErrorAction SilentlyContinue | Format-Table"
    return None


def build_powershell_verify_command(action: ActionType, params: dict) -> Optional[str]:
    if action == ActionType.DISK_USAGE:
        return "if (Get-PSDrive -PSProvider FileSystem) { 'OK' } else { 'FAIL' }"
    if action == ActionType.MEMORY_USAGE:
        return "$os = Get-CimInstance Win32_OperatingSystem; if ($os.TotalVisibleMemorySize -gt 0) { 'OK' } else { 'FAIL' }"
    if action == ActionType.SYSTEM_INFO:
        return "if (Get-CimInstance Win32_OperatingSystem) { 'OK' } else { 'FAIL' }"
    if action == ActionType.SERVICE_STATUS:
        svc = params.get("service", "")
        return f"(Get-Service -Name '{svc}' -ErrorAction SilentlyContinue) -ne $null"
    return None
