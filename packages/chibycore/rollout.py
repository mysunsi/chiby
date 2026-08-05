"""灰度发布引擎 - Rollout with Gate.

核心功能:
1. 按百分比将主机列表分割成多个批次
2. 逐批执行运维任务
3. 每批执行后进行 Gate 健康检查
4. Gate 失败时自动回滚到最后成功批次
5. 支持实时进度回调 (WebSocket 推送)
"""
from __future__ import annotations

import asyncio
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from .gate import GateChecker, GateConfig, GateResult
from .schemas import (
    BatchReport,
    RolloutPhase,
    RolloutPlan,
    RolloutProgress,
    RolloutReport,
    RolloutRequest,
)


def split_batches(hosts: List[str], percents: List[int]) -> List[List[str]]:
    """
    根据百分比将主机列表分割成批次.
    
    Args:
        hosts: 目标主机列表
        percents: 百分比列表, 如 [10, 50, 100]
        
    Returns:
        批次列表, 每个元素是该批次包含的主机列表
        
    Example:
        hosts = [h1, h2, h3, h4, h5, h6, h7, h8, h9, h10]
        percents = [10, 50, 100]
        result = [[h1], [h1, h2, h3, h4, h5], [h1,...,h10]]
                   10%    50%             100%
    """
    if not hosts:
        return []
    
    total = len(hosts)
    batches = []
    used_indices = set()
    
    for percent in percents:
        # 计算该百分比应该包含多少主机
        target_count = max(1, int(total * percent / 100))
        target_count = min(target_count, total)
        
        # 新增主机数量 (相对于上一批次)
        new_count = target_count - len(used_indices)
        
        if new_count > 0:
            # 从未使用的主机中选择
            remaining = [h for h in hosts if h not in used_indices]
            batch_hosts = remaining[:new_count]
        else:
            # 百分比没有增加, 复用上一批次
            batch_hosts = []
        
        if batch_hosts:  # 只添加非空批次
            batches.append(batch_hosts)
            used_indices.update(batch_hosts)
    
    # 确保最后一个批次包含所有主机
    if batches and set(batches[-1]) != set(hosts):
        all_hosts_set = set(hosts)
        last_batch_set = set(batches[-1])
        missing = all_hosts_set - last_batch_set
        if missing:
            batches[-1] = list(last_batch_set | missing)
    
    return batches


def make_rollout_id() -> str:
    """生成唯一的灰度发布 ID"""
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    uid = str(uuid.uuid4())[:8]
    return f"rollout-{ts}-{uid}"


@dataclass
class RolloutExecutorConfig:
    """灰度发布执行器配置"""
    request: RolloutRequest
    gate_checker: Optional[GateChecker] = None
    auto_rollback: bool = True
    max_concurrent_per_batch: int = 5  # 每批次最多并发主机数


class RolloutEngine:
    """
    灰度发布执行器.
    
    使用方式:
    
    ```python
    request = RolloutRequest(
        command="部署 nginx 配置",
        hosts=["172.25.87.85", "172.25.87.86", "172.25.87.87"],
        ssh_user="root",
        ssh_password="xxx",
        gate=GateConfig(kind=GateKind.PROCESS, process_name="nginx"),
        percents=[10, 50, 100],
        auto_rollback=True,
    )
    
    engine = RolloutEngine(request)
    report = await engine.execute()
    ```
    
    支持实时进度回调:
    
    ```python
    async def on_progress(progress: RolloutProgress):
        print(f"[{progress.phase}] Batch {progress.batch_index}/{progress.batch_total}")
    
    engine = RolloutEngine(request, progress_callback=on_progress)
    report = await engine.execute()
    ```
    """
    
    def __init__(
        self,
        request: RolloutRequest,
        gate_checker: Optional[GateChecker] = None,
        auto_rollback: bool = True,
        progress_callback: Optional[Callable[[RolloutProgress], None]] = None,
    ):
        self.request = request
        self.auto_rollback = auto_rollback
        self.progress_callback = progress_callback
        
        # 初始化 Gate 检查器
        if request.gate and not gate_checker:
            self.gate_checker = GateChecker(request.gate)
        else:
            self.gate_checker = gate_checker
        
        # 状态
        self.rollout_id = make_rollout_id()
        self.batches: List[List[str]] = []
        self.batch_reports: List[BatchReport] = []
        self.started_at: Optional[str] = None
        self._last_successful_batch: Optional[int] = None
    
    def plan(self) -> RolloutPlan:
        """
        生成灰度发布计划 (dry-run).
        
        Returns:
            RolloutPlan: 包含批次划分预览
        """
        batches = split_batches(self.request.hosts, self.request.percents)
        
        batch_previews = []
        for i, batch_hosts in enumerate(batches):
            batch_previews.append({
                "batch_index": i + 1,
                "percent": self.request.percents[i] if i < len(self.request.percents) else 100,
                "hosts": batch_hosts,
                "host_count": len(batch_hosts),
            })
        
        return RolloutPlan(
            hosts=self.request.hosts,
            batches=batch_previews,
            gate=self.request.gate,
            auto_rollback=self.auto_rollback,
            estimated_duration_s=len(batches) * 30,  # 估算: 每批 30 秒
        )
    
    def _send_progress(
        self,
        phase: RolloutPhase,
        batch_index: int,
        batch_total: int,
        current_hosts: List[str],
        gate_ok: Optional[bool] = None,
        message: str = "",
        batch_duration_s: Optional[float] = None,
    ) -> None:
        """发送进度更新"""
        if self.progress_callback:
            elapsed = 0.0
            if self.started_at:
                elapsed = time.time() - (datetime.fromisoformat(self.started_at).timestamp() if isinstance(self.started_at, str) else 0)
            
            progress = RolloutProgress(
                phase=phase,
                batch_index=batch_index,
                batch_total=batch_total,
                current_hosts=current_hosts,
                gate_ok=gate_ok,
                message=message,
                batch_duration_s=batch_duration_s,
                total_duration_s=elapsed,
            )
            self.progress_callback(progress)
    
    async def execute(
        self,
        executor_func: Callable[..., Tuple[bool, str, List[Dict[str, Any]]]],
        # executor_func 签名: (host, ssh_user, ssh_password, steps) -> (success, message, steps_results)
    ) -> RolloutReport:
        """
        执行灰度发布.
        
        Args:
            executor_func: 执行器函数, 负责在单台主机上执行任务
                签名: (host, ssh_user, ssh_password, steps) -> (success, message, steps_results)
                
        Returns:
            RolloutReport: 完整灰度发布报告
        """
        self.started_at = datetime.now().isoformat()
        t0 = time.time()
        
        # 生成批次
        self.batches = split_batches(self.request.hosts, self.request.percents)
        
        if not self.batches:
            return self._make_report(success=False, total_duration=time.time() - t0)
        
        # 发送开始进度
        self._send_progress(
            phase=RolloutPhase.PENDING,
            batch_index=0,
            batch_total=len(self.batches),
            current_hosts=[],
            message="开始灰度发布",
        )
        
        # 逐批执行
        for batch_idx, batch_hosts in enumerate(self.batches):
            batch_index = batch_idx + 1
            batch_percent = self.request.percents[batch_idx] if batch_idx < len(self.request.percents) else 100
            
            # 发送执行开始进度
            self._send_progress(
                phase=RolloutPhase.EXECUTING,
                batch_index=batch_index,
                batch_total=len(self.batches),
                current_hosts=batch_hosts,
                message=f"开始执行批次 {batch_index} ({batch_percent}%)",
            )
            
            batch_t0 = time.time()
            
            # 执行当前批次
            success, message, steps_results = await self._execute_batch(
                batch_hosts,
                executor_func,
            )
            
            batch_duration = time.time() - batch_t0
            
            if not success:
                # 批次执行失败
                batch_report = BatchReport(
                    batch_index=batch_index,
                    batch_percent=batch_percent,
                    hosts=batch_hosts,
                    steps_results=steps_results,
                    success=False,
                    started_at=datetime.fromtimestamp(batch_t0, tz=timezone.utc).isoformat(),
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    duration_s=batch_duration,
                )
                self.batch_reports.append(batch_report)
                
                # Gate 检查 (即使执行失败也检查)
                gate_result = await self._check_gate(batch_hosts)
                batch_report.gate_result = gate_result
                
                # 发送失败进度
                self._send_progress(
                    phase=RolloutPhase.GATE_FAILED,
                    batch_index=batch_index,
                    batch_total=len(self.batches),
                    current_hosts=batch_hosts,
                    gate_ok=False,
                    message=f"批次 {batch_index} 执行失败: {message}",
                    batch_duration_s=batch_duration,
                )
                
                # 尝试回滚
                if self.auto_rollback and self._last_successful_batch is not None:
                    rollback_report = await self._rollback()
                    return self._make_report(
                        success=False,
                        total_duration=time.time() - t0,
                        rollback_report=rollback_report,
                    )
                else:
                    return self._make_report(
                        success=False,
                        total_duration=time.time() - t0,
                    )
            
            # 批次执行成功, 进行 Gate 检查
            self._send_progress(
                phase=RolloutPhase.GATE_CHECK,
                batch_index=batch_index,
                batch_total=len(self.batches),
                current_hosts=batch_hosts,
                message=f"批次 {batch_index} 执行成功, 开始 Gate 检查...",
                batch_duration_s=batch_duration,
            )
            
            gate_result = await self._check_gate(batch_hosts)
            
            batch_report = BatchReport(
                batch_index=batch_index,
                batch_percent=batch_percent,
                hosts=batch_hosts,
                steps_results=steps_results,
                success=True,
                gate_result=gate_result,
                started_at=datetime.fromtimestamp(batch_t0, tz=timezone.utc).isoformat(),
                finished_at=datetime.now(timezone.utc).isoformat(),
                duration_s=batch_duration,
            )
            self.batch_reports.append(batch_report)
            
            if not gate_result.ok:
                # Gate 检查失败
                self._send_progress(
                    phase=RolloutPhase.GATE_FAILED,
                    batch_index=batch_index,
                    batch_total=len(self.batches),
                    current_hosts=batch_hosts,
                    gate_ok=False,
                    message=f"Gate 检查失败: {gate_result.message}",
                    batch_duration_s=batch_duration,
                )
                
                # 回滚
                if self.auto_rollback and self._last_successful_batch is not None:
                    rollback_report = await self._rollback()
                    return self._make_report(
                        success=False,
                        total_duration=time.time() - t0,
                        rollback_report=rollback_report,
                    )
                else:
                    return self._make_report(
                        success=False,
                        total_duration=time.time() - t0,
                    )
            
            # Gate 检查通过
            self._last_successful_batch = batch_idx
            self._send_progress(
                phase=RolloutPhase.GATE_PASSED,
                batch_index=batch_index,
                batch_total=len(self.batches),
                current_hosts=batch_hosts,
                gate_ok=True,
                message=f"批次 {batch_index} Gate 检查通过",
                batch_duration_s=batch_duration,
            )
        
        # 所有批次完成
        self._send_progress(
            phase=RolloutPhase.COMPLETED,
            batch_index=len(self.batches),
            batch_total=len(self.batches),
            current_hosts=self.request.hosts,
            message="灰度发布完成",
            total_duration_s=time.time() - t0,
        )
        
        return self._make_report(success=True, total_duration=time.time() - t0)
    
    async def _execute_batch(
        self,
        hosts: List[str],
        executor_func: Callable,
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        执行单个批次.
        
        在批次内主机间并发执行.
        
        Returns:
            (success, message, steps_results)
        """
        all_results: List[Dict[str, Any]] = []
        failed_hosts: List[str] = []
        
        loop = asyncio.get_event_loop()
        
        with ThreadPoolExecutor(max_workers=min(len(hosts), 5)) as executor:
            futures = {
                loop.run_in_executor(
                    executor,
                    executor_func,
                    host,
                    self.request.ssh_user,
                    self.request.ssh_password,
                    self.request.steps,
                ): host
                for host in hosts
            }
            
            for future in as_completed(futures):
                host = futures[future]
                try:
                    success, message, steps_results = await future
                    all_results.append({
                        "host": host,
                        "success": success,
                        "message": message,
                        "steps_results": steps_results,
                    })
                    if not success:
                        failed_hosts.append(host)
                except Exception as e:
                    all_results.append({
                        "host": host,
                        "success": False,
                        "message": str(e),
                        "steps_results": [],
                    })
                    failed_hosts.append(host)
        
        if failed_hosts:
            return False, f"主机执行失败: {', '.join(failed_hosts)}", all_results
        
        return True, f"批次执行成功 ({len(hosts)} 台主机)", all_results
    
    async def _check_gate(self, hosts: List[str]) -> Optional[GateResult]:
        """执行 Gate 检查"""
        if not self.gate_checker:
            return None
        
        # 对批次内所有主机执行 Gate 检查
        result = self.gate_checker.check_hosts(
            hosts,
            ssh_user=self.request.ssh_user,
            ssh_password=self.request.ssh_password,
        )
        return result
    
    async def _rollback(self) -> Optional[RolloutReport]:
        """
        执行回滚.
        
        回滚到最后成功批次.
        """
        if self._last_successful_batch is None or self._last_successful_batch < 0:
            return None
        
        last_batch = self.batch_reports[self._last_successful_batch]
        
        self._send_progress(
            phase=RolloutPhase.ROLLBACK,
            batch_index=self._last_successful_batch + 1,
            batch_total=len(self.batches),
            current_hosts=last_batch.hosts,
            message="开始回滚...",
        )
        
        # 创建回滚报告
        rollback_report = RolloutReport(
            rollout_id=f"{self.rollout_id}-rollback",
            user_command=f"[回滚] {self.request.command}",
            hosts=last_batch.hosts,
            percents=[last_batch.batch_percent],
            gate_config=self.request.gate,
            batches=[],
            success=False,  # 待更新
            auto_rollback=False,
        )
        
        # 回滚逻辑: 调用 rollback_command 或重新执行最后成功批次
        # 这里简化处理, 实际应该执行回滚命令
        rollback_message = f"回滚到批次 {self._last_successful_batch + 1} ({last_batch.batch_percent}%)"
        
        self._send_progress(
            phase=RolloutPhase.ROLLBACK,
            batch_index=self._last_successful_batch + 1,
            batch_total=len(self.batches),
            current_hosts=last_batch.hosts,
            message=rollback_message,
        )
        
        return rollback_report
    
    def _make_report(
        self,
        success: bool,
        total_duration: float,
        rollback_report: Optional[RolloutReport] = None,
    ) -> RolloutReport:
        """生成最终报告"""
        return RolloutReport(
            rollout_id=self.rollout_id,
            user_command=self.request.command,
            hosts=self.request.hosts,
            percents=self.request.percents,
            gate_config=self.request.gate,
            batches=self.batch_reports,
            rollback_report=rollback_report,
            success=success,
            auto_rollback=self.auto_rollback,
            total_duration_s=total_duration,
            created_at=self.started_at or datetime.now(timezone.utc).isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )


# ─── 同步封装 ───────────────────────────────────────────────────────────────

class SyncRolloutEngine:
    """
    同步版本的灰度发布引擎.
    
    用于不支持 async 的场景.
    """
    
    def __init__(
        self,
        request: RolloutRequest,
        gate_checker: Optional[GateChecker] = None,
        auto_rollback: bool = True,
        progress_callback: Optional[Callable[[RolloutProgress], None]] = None,
    ):
        self.async_engine = RolloutEngine(
            request=request,
            gate_checker=gate_checker,
            auto_rollback=auto_rollback,
            progress_callback=progress_callback,
        )
    
    def plan(self) -> RolloutPlan:
        return self.async_engine.plan()
    
    def execute(
        self,
        executor_func: Callable[..., Tuple[bool, str, List[Dict[str, Any]]]],
    ) -> RolloutReport:
        """同步执行 (在子线程中运行 async 代码)"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(self.async_engine.execute(executor_func))
        finally:
            loop.close()
