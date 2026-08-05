"""Gate 健康检查机制 - 从 AiOps 迁移并增强.

支持四种 Gate 类型:
- HTTP: curl 检查 URL 返回 2xx/3xx
- Port: /dev/tcp 探测 TCP 端口连通性
- Process: pgrep 检查进程是否存在
- PromQL: 查询 Prometheus 并比较阈值

典型用途: 灰度发布时的健康检查, 确保新版本服务正常后再继续.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple
from enum import Enum


def _posix_shell_subprocess_kw() -> Dict[str, Any]:
    """POSIX 下 shell=True 子进程使用独立会话，便于超时回收进程组。"""
    if os.name != "posix":
        return {}
    return {"start_new_session": True}


class GateKind(str, Enum):
    """Gate 检查类型"""
    HTTP = "http"
    PORT = "port"
    PROCESS = "process"
    PROMQL = "promql"
    CMD = "cmd"  # 自定义命令检查


@dataclass(frozen=True)
class GateConfig:
    """Gate 配置"""
    kind: GateKind
    
    # HTTP 配置
    url: Optional[str] = None
    
    # Port 配置
    port: Optional[int] = None
    host: Optional[str] = None  # 默认 127.0.0.1
    
    # Process 配置
    process_name: Optional[str] = None
    
    # PromQL 配置
    prom_url: Optional[str] = None
    prom_query: Optional[str] = None
    prom_op: Optional[Literal[">", ">=", "<", "<=", "==", "!="]] = None
    prom_threshold: Optional[float] = None
    
    # 自定义命令配置
    cmd: Optional[str] = None
    
    # 通用配置
    timeout_s: int = 5
    
    def validate(self) -> None:
        """验证配置完整性"""
        if self.kind == GateKind.HTTP:
            if not self.url:
                raise ValueError("HTTP Gate 需要配置 url")
            if not (self.url.startswith("http://") or self.url.startswith("https://")):
                raise ValueError("Gate HTTP 仅支持 http/https URL")
        
        elif self.kind == GateKind.PORT:
            if not self.port:
                raise ValueError("Port Gate 需要配置 port")
            if not (1 <= self.port <= 65535):
                raise ValueError("Gate port 范围应为 1-65535")
        
        elif self.kind == GateKind.PROCESS:
            if not self.process_name:
                raise ValueError("Process Gate 需要配置 process_name")
            # 验证进程名安全性（仅允许字母数字和 _-.)
            for ch in self.process_name:
                if not (ch.isalnum() or ch in ("_", "-", ".")):
                    raise ValueError("Gate process 仅允许字母数字与 _-.")
        
        elif self.kind == GateKind.PROMQL:
            if not self.prom_url:
                raise ValueError("PromQL Gate 需要配置 prom_url")
            if not self.prom_query:
                raise ValueError("PromQL Gate 需要配置 prom_query")
            if not self.prom_op:
                raise ValueError("PromQL Gate 需要配置 prom_op")
            if self.prom_threshold is None:
                raise ValueError("PromQL Gate 需要配置 prom_threshold")
            if self.prom_op not in (">", ">=", "<", "<=", "==", "!="):
                raise ValueError(f"不支持的 prom_op: {self.prom_op}")
        
        elif self.kind == GateKind.CMD:
            if not self.cmd:
                raise ValueError("CMD Gate 需要配置 cmd")


@dataclass
class GateResult:
    """Gate 检查结果"""
    ok: bool
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_ms: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "message": self.message,
            "details": self.details,
            "checked_at": self.checked_at,
            "duration_ms": self.duration_ms,
        }


class GateChecker:
    """
    Gate 健康检查器.
    
    支持对单主机或多主机执行健康检查.
    多主机检查: 并发执行, 任一失败则整体失败.
    """
    
    def __init__(self, config: GateConfig):
        self.config = config
        self.config.validate()
    
    def check_single(self, host: str) -> GateResult:
        """
        对单主机执行 Gate 检查.
        
        Args:
            host: 目标主机 IP 或主机名
            
        Returns:
            GateResult: 检查结果
        """
        import time
        t0 = time.time()
        
        try:
            if self.config.kind == GateKind.HTTP:
                return self._check_http(host, t0)
            elif self.config.kind == GateKind.PORT:
                return self._check_port(host, t0)
            elif self.config.kind == GateKind.PROCESS:
                return self._check_process(host, t0)
            elif self.config.kind == GateKind.PROMQL:
                return self._check_promql(host, t0)
            elif self.config.kind == GateKind.CMD:
                return self._check_cmd(host, t0)
            else:
                return GateResult(
                    ok=False,
                    message=f"未知的 Gate 类型: {self.config.kind}",
                    duration_ms=int((time.time() - t0) * 1000),
                )
        except Exception as e:
            return GateResult(
                ok=False,
                message=f"Gate 检查异常: {str(e)}",
                details={"exception": str(e)},
                duration_ms=int((time.time() - t0) * 1000),
            )
    
    def check_hosts(
        self,
        hosts: List[str],
        ssh_user: Optional[str] = None,
        ssh_password: Optional[str] = None,
        progress_callback: Optional[Callable[[str, GateResult], None]] = None,
    ) -> GateResult:
        """
        对多主机执行 Gate 检查.
        
        Args:
            hosts: 目标主机列表
            ssh_user: SSH 用户名 (用于远程检查)
            ssh_password: SSH 密码
            progress_callback: 进度回调 (host, result)
            
        Returns:
            GateResult: 整体结果 (任一失败则 ok=False)
        """
        import time
        import concurrent.futures
        
        t0 = time.time()
        results: Dict[str, GateResult] = {}
        
        def check_one(host: str) -> Tuple[str, GateResult]:
            result = self.check_single(host)
            if progress_callback:
                progress_callback(host, result)
            return host, result
        
        # 并发检查所有主机
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(hosts)) as executor:
            futures = {executor.submit(check_one, h): h for h in hosts}
            for fut in concurrent.futures.as_completed(futures):
                host, result = fut.result()
                results[host] = result
        
        # 汇总结果
        all_ok = all(r.ok for r in results.values())
        failed_hosts = [h for h, r in results.items() if not r.ok]
        
        if all_ok:
            message = f"所有 {len(hosts)} 台主机 Gate 检查通过"
        else:
            message = f"Gate 检查失败: {', '.join(failed_hosts)}"
        
        return GateResult(
            ok=all_ok,
            message=message,
            details={
                "hosts": hosts,
                "results": {h: r.to_dict() for h, r in results.items()},
                "failed_hosts": failed_hosts,
            },
            duration_ms=int((time.time() - t0) * 1000),
        )
    
    def _check_http(self, host: str, t0: float) -> GateResult:
        """HTTP Gate: curl 检查 URL"""
        url = self.config.url or f"http://{host}"
        
        cmd = f'curl -fsS --max-time {self.config.timeout_s} {shlex.quote(url)} >NUL'
        
        try:
            # 本地执行 curl 检查
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                timeout=self.config.timeout_s + 2,
                **_posix_shell_subprocess_kw(),
            )
            
            ok = result.returncode == 0
            return GateResult(
                ok=ok,
                message="HTTP 检查通过" if ok else f"HTTP 检查失败 (exit={result.returncode})",
                details={
                    "url": url,
                    "cmd": cmd,
                    "exit_code": result.returncode,
                },
                duration_ms=int((time.time() - t0) * 1000),
            )
        except subprocess.TimeoutExpired:
            return GateResult(
                ok=False,
                message=f"HTTP 检查超时 ({self.config.timeout_s}s)",
                details={"url": url},
                duration_ms=int((time.time() - t0) * 1000),
            )
        except Exception as e:
            return GateResult(
                ok=False,
                message=f"HTTP 检查异常: {str(e)}",
                details={"url": url},
                duration_ms=int((time.time() - t0) * 1000),
            )
    
    def _check_port(self, host: str, t0: float) -> GateResult:
        """Port Gate: /dev/tcp 探测 TCP 端口"""
        target_host = self.config.host or host
        port = self.config.port
        
        # 使用 bash 的 /dev/tcp 探测 (Linux/macOS)
        # Windows 环境下使用 PowerShell 的 Test-NetConnection
        cmd = f'bash -lc "echo > /dev/tcp/{target_host}/{port}" 2>/dev/null'
        
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                timeout=self.config.timeout_s + 2,
                **_posix_shell_subprocess_kw(),
            )
            
            ok = result.returncode == 0
            return GateResult(
                ok=ok,
                message=f"端口 {port} 可达" if ok else f"端口 {port} 不可达",
                details={
                    "host": target_host,
                    "port": port,
                    "cmd": cmd,
                },
                duration_ms=int((time.time() - t0) * 1000),
            )
        except subprocess.TimeoutExpired:
            return GateResult(
                ok=False,
                message=f"端口检查超时 ({self.config.timeout_s}s)",
                details={"host": target_host, "port": port},
                duration_ms=int((time.time() - t0) * 1000),
            )
        except Exception as e:
            # Windows fallback: 尝试 PowerShell
            return self._check_port_windows(target_host, port, t0)
    
    def _check_port_windows(self, host: str, port: int, t0: float) -> GateResult:
        """Windows Port Gate: 使用 PowerShell Test-NetConnection"""
        cmd = f'powershell -Command "Test-NetConnection -ComputerName {host} -Port {port} -InformationLevel Quiet"'
        
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                timeout=self.config.timeout_s + 2,
                **_posix_shell_subprocess_kw(),
            )
            
            # PowerShell True/False 输出
            output = result.stdout.decode("utf-8", errors="replace").strip().lower()
            ok = output == "true"
            
            return GateResult(
                ok=ok,
                message=f"端口 {port} 可达" if ok else f"端口 {port} 不可达",
                details={"host": host, "port": port},
                duration_ms=int((time.time() - t0) * 1000),
            )
        except Exception as e:
            return GateResult(
                ok=False,
                message=f"端口检查异常: {str(e)}",
                details={"host": host, "port": port},
                duration_ms=int((time.time() - t0) * 1000),
            )
    
    def _check_process(self, host: str, t0: float) -> GateResult:
        """Process Gate: pgrep 检查进程"""
        process_name = self.config.process_name
        
        cmd = f"pgrep -x {shlex.quote(process_name)} >/dev/null 2>&1"
        
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                timeout=self.config.timeout_s + 2,
                **_posix_shell_subprocess_kw(),
            )
            
            ok = result.returncode == 0
            return GateResult(
                ok=ok,
                message=f"进程 {process_name} 运行中" if ok else f"进程 {process_name} 未运行",
                details={
                    "process_name": process_name,
                    "cmd": cmd,
                },
                duration_ms=int((time.time() - t0) * 1000),
            )
        except Exception as e:
            return GateResult(
                ok=False,
                message=f"进程检查异常: {str(e)}",
                details={"process_name": process_name},
                duration_ms=int((time.time() - t0) * 1000),
            )
    
    def _check_promql(self, host: str, t0: float) -> GateResult:
        """PromQL Gate: 查询 Prometheus 并比较阈值"""
        prom_url = self.config.prom_url
        query = self.config.prom_query
        op = self.config.prom_op
        threshold = self.config.prom_threshold
        
        query_url = f"{prom_url.rstrip('/')}/api/v1/query?{urllib.parse.urlencode({'query': query})}"
        
        try:
            req = urllib.request.Request(
                query_url,
                headers={"Accept": "application/json"},
                timeout=self.config.timeout_s,
            )
            
            with urllib.request.urlopen(req) as resp:
                body = resp.read().decode("utf-8", "replace")
            
            data = json.loads(body)
            
            if data.get("status") != "success":
                return GateResult(
                    ok=False,
                    message=f"Prometheus 查询失败: status={data.get('status')}",
                    details={"query": query, "url": query_url},
                    duration_ms=int((time.time() - t0) * 1000),
                )
            
            result_list = (data.get("data", {}) or {}).get("result") or []
            
            if not result_list:
                return GateResult(
                    ok=False,
                    message="Prometheus 返回空结果",
                    details={"query": query, "url": query_url},
                    duration_ms=int((time.time() - t0) * 1000),
                )
            
            # 取第一个结果的值
            value = result_list[0].get("value")
            if not (isinstance(value, list) and len(value) >= 2):
                return GateResult(
                    ok=False,
                    message="Prometheus 返回值格式错误",
                    details={"value": value},
                    duration_ms=int((time.time() - t0) * 1000),
                )
            
            v = float(value[1])
            ok = self._compare(v, op, threshold)
            
            return GateResult(
                ok=ok,
                message=f"PromQL 检查 {'通过' if ok else '失败'}: {v} {op} {threshold}",
                details={
                    "query": query,
                    "value": v,
                    "op": op,
                    "threshold": threshold,
                    "url": query_url,
                },
                duration_ms=int((time.time() - t0) * 1000),
            )
            
        except urllib.error.URLError as e:
            return GateResult(
                ok=False,
                message=f"Prometheus 连接失败: {str(e)}",
                details={"url": query_url},
                duration_ms=int((time.time() - t0) * 1000),
            )
        except Exception as e:
            return GateResult(
                ok=False,
                message=f"PromQL 检查异常: {str(e)}",
                details={"query": query},
                duration_ms=int((time.time() - t0) * 1000),
            )
    
    def _check_cmd(self, host: str, t0: float) -> GateResult:
        """CMD Gate: 执行自定义命令检查"""
        cmd = self.config.cmd
        
        if not cmd:
            return GateResult(
                ok=False,
                message="CMD Gate 未配置命令",
                duration_ms=int((time.time() - t0) * 1000),
            )
        
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                timeout=self.config.timeout_s + 2,
                **_posix_shell_subprocess_kw(),
            )
            
            ok = result.returncode == 0
            return GateResult(
                ok=ok,
                message="命令检查通过" if ok else f"命令检查失败 (exit={result.returncode})",
                details={
                    "cmd": cmd,
                    "exit_code": result.returncode,
                    "stdout": result.stdout.decode("utf-8", errors="replace")[:500],
                    "stderr": result.stderr.decode("utf-8", errors="replace")[:500],
                },
                duration_ms=int((time.time() - t0) * 1000),
            )
        except subprocess.TimeoutExpired:
            return GateResult(
                ok=False,
                message=f"命令检查超时 ({self.config.timeout_s}s)",
                details={"cmd": cmd},
                duration_ms=int((time.time() - t0) * 1000),
            )
        except Exception as e:
            return GateResult(
                ok=False,
                message=f"命令检查异常: {str(e)}",
                details={"cmd": cmd},
                duration_ms=int((time.time() - t0) * 1000),
            )
    
    @staticmethod
    def _compare(v: float, op: str, threshold: float) -> bool:
        """比较数值"""
        if op == ">":
            return v > threshold
        elif op == ">=":
            return v >= threshold
        elif op == "<":
            return v < threshold
        elif op == "<=":
            return v <= threshold
        elif op == "==":
            return v == threshold
        elif op == "!=":
            return v != threshold
        else:
            raise ValueError(f"不支持的操作符: {op}")


# ─── 便捷工厂函数 ───────────────────────────────────────────────────────────

def create_http_gate(url: str, timeout_s: int = 5) -> GateChecker:
    """创建 HTTP Gate"""
    config = GateConfig(kind=GateKind.HTTP, url=url, timeout_s=timeout_s)
    return GateChecker(config)


def create_port_gate(port: int, host: str = "127.0.0.1", timeout_s: int = 5) -> GateChecker:
    """创建端口 Gate"""
    config = GateConfig(kind=GateKind.PORT, port=port, host=host, timeout_s=timeout_s)
    return GateChecker(config)


def create_process_gate(process_name: str, timeout_s: int = 5) -> GateChecker:
    """创建进程 Gate"""
    config = GateConfig(kind=GateKind.PROCESS, process_name=process_name, timeout_s=timeout_s)
    return GateChecker(config)


def create_promql_gate(
    prom_url: str,
    query: str,
    op: str,
    threshold: float,
    timeout_s: int = 5,
) -> GateChecker:
    """创建 PromQL Gate"""
    config = GateConfig(
        kind=GateKind.PROMQL,
        prom_url=prom_url,
        prom_query=query,
        prom_op=op,
        prom_threshold=threshold,
        timeout_s=timeout_s,
    )
    return GateChecker(config)


def create_cmd_gate(cmd: str, timeout_s: int = 5) -> GateChecker:
    """创建自定义命令 Gate"""
    config = GateConfig(kind=GateKind.CMD, cmd=cmd, timeout_s=timeout_s)
    return GateChecker(config)


# ─── Gate 策略 ─────────────────────────────────────────────────────────────

class GateStrategy:
    """
    Gate 策略: 决定何时使用何种 Gate.
    
    根据场景自动推荐合适的 Gate 类型.
    """
    
    # 从自然语言推断 Gate 类型
    KEYWORD_GATE_MAP = {
        "http": GateKind.HTTP,
        "web": GateKind.HTTP,
        "url": GateKind.HTTP,
        "80": GateKind.PORT,
        "443": GateKind.PORT,
        "8080": GateKind.PORT,
        "端口": GateKind.PORT,
        "port": GateKind.PORT,
        "进程": GateKind.PROCESS,
        "process": GateKind.PROCESS,
        "pgrep": GateKind.PROCESS,
        "prometheus": GateKind.PROMQL,
        "promql": GateKind.PROMQL,
        "metric": GateKind.PROMQL,
    }
    
    @classmethod
    def infer_gate(cls, user_text: str) -> Optional[Tuple[GateKind, Dict[str, Any]]]:
        """
        从用户文本推断 Gate 类型和配置.
        
        Returns:
            (GateKind, config_dict) 或 None
        """
        import re

        text_lower = user_text.lower()
        
        # HTTP Gate
        if any(kw in text_lower for kw in ["http", "web", "url", "健康检查"]):
            # 尝试提取 URL
            url_match = re.search(r"https?://[^\s]+", user_text)
            if url_match:
                return GateKind.HTTP, {"url": url_match.group(), "timeout_s": 5}
            return GateKind.HTTP, {"url": None, "timeout_s": 5}
        
        # 端口 Gate（兼容「检查8080端口」：中文与数字之间无 \b）
        port_match = re.search(
            r"(?:^|[^\d])(\d{1,5})\s*(?:端口|port)\b|\b(\d{1,5})\b.*?(?:端口|port)",
            user_text,
            re.IGNORECASE,
        )
        if port_match:
            port = int(port_match.group(1) or port_match.group(2))
            if 1 <= port <= 65535:
                return GateKind.PORT, {"port": port, "timeout_s": 5}
        
        # 进程 Gate
        for kw in ["nginx", "redis", "mysql", "docker", "ssh", "httpd", "apache"]:
            if kw in text_lower:
                return GateKind.PROCESS, {"process_name": kw, "timeout_s": 5}
        
        if any(kw in text_lower for kw in ["进程", "process", "pgrep"]):
            proc_match = re.search(r"(?:进程|process)\s+(\w+)", user_text)
            if proc_match:
                return GateKind.PROCESS, {"process_name": proc_match.group(1), "timeout_s": 5}
        
        # PromQL Gate
        if any(kw in text_lower for kw in ["prometheus", "promql", "metric", "指标"]):
            query_match = re.search(r"promql?\s*[:：]?\s*[`'\"']?(.+?)[`'\"']?", user_text, re.IGNORECASE)
            if query_match:
                return GateKind.PROMQL, {
                    "prom_url": "http://prometheus:9090",
                    "prom_query": query_match.group(1).strip(),
                    "prom_op": ">=",
                    "prom_threshold": 1,
                    "timeout_s": 5,
                }
        
        return None
    
    @classmethod
    def infer_gate_from_service(cls, service_name: str) -> GateChecker:
        """
        从服务名推断 Gate 类型.
        
        Args:
            service_name: 服务名 (如 nginx, mysql, redis 等)
            
        Returns:
            GateChecker 实例
        """
        # 进程检查
        return create_process_gate(service_name)
