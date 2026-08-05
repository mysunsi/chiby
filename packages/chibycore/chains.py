"""任务链模板：将复杂操作预定义为可复用的多步工作流。"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import EXECUTION_MODE, MAX_RETRIES
from .schemas import ActionType, ExecutionStep, StepStatus
from .script_generator import build_command, build_verify_command
from .ssh_executor import CmdResult, exec_local, exec_ssh

logger = logging.getLogger(__name__)


# ─── 步骤配置 ────────────────────────────────────────────────────────────────
@dataclass
class ChainStepConfig:
    action: ActionType               # 动作类型
    description: str                 # 人类可读描述（含 {param} 占位符）
    command_template: Optional[str] = None   # 自定义命令（None则用 script_generator）
    verify_template: Optional[str] = None     # 自定义验证命令
    rollback_template: Optional[str] = None  # 自定义回滚命令
    depends_on: List[str] = field(default_factory=list)  # 依赖的前置步骤ID
    parallel_group: Optional[str] = None    # 同组步骤可并行执行
    continue_on_fail: bool = False          # 失败后是否继续（默认否）
    timeout: int = 60                       # 超时秒数


# ─── 任务链定义 ────────────────────────────────────────────────────────────────
# intent_keywords: 触发该链的自然语言关键词（任意一个匹配即触发）
@dataclass
class TaskChain:
    name: str                        # 链名称
    intent_keywords: List[str]       # 触发关键词列表
    description: str                 # 链的说明
    steps: List[ChainStepConfig]     # 步骤列表
    requires_approval: bool = True   # 是否需要用户确认再执行


TASK_CHAINS: Dict[str, TaskChain] = {

    # ── 1. 主机资源监控 ─────────────────────────────────────────────────────
    "monitor_resources": TaskChain(
        name="主机资源监控",
        intent_keywords=[
            "资源使用情况", "资源运作状态", "主机状态", "系统监控",
            "查看资源", "系统负载", "性能监控", "资源报表",
            "查看主机资源", "主机资源", "服务器状态",
        ],
        description="全面监控主机资源：CPU、内存、磁盘、网络、进程",
        steps=[
            ChainStepConfig(
                action=ActionType.SYSTEM_INFO,
                description="获取系统基本信息",
                parallel_group="info",
            ),
            ChainStepConfig(
                action=ActionType.DISK_USAGE,
                description="获取磁盘使用情况",
                parallel_group="info",
            ),
            ChainStepConfig(
                action=ActionType.MEMORY_USAGE,
                description="获取内存使用情况",
                parallel_group="info",
            ),
            ChainStepConfig(
                action=ActionType.CPU_USAGE,
                description="获取 CPU 使用率",
                parallel_group="info",
            ),
            ChainStepConfig(
                action=ActionType.NETSTAT,
                description="获取网络连接状态",
                parallel_group="info",
            ),
            ChainStepConfig(
                action=ActionType.PROCESS_LIST,
                description="获取进程列表（Top15）",
            ),
        ],
    ),

    # ── 2. 用户账号开通 ─────────────────────────────────────────────────────
    "create_user_with_full_access": TaskChain(
        name="用户账号开通（含完整配置）",
        intent_keywords=[
            # 标准说法
            "创建账号", "开通账号", "新建用户", "添加账号",
            "创建用户并配置", "创建管理员", "创建sudo用户",
            # 口语化（带"帮我"前缀）
            "帮我创建账号", "帮我创建用户", "帮我开通账号",
            "帮我添加用户", "帮我新建一个账号", "帮我添加一个账号",
            "帮我创建", "创建用户", "添加用户",
            # 英文/混合
            "adduser", "create user",
        ],
        description="创建用户 → 设置密码 → 验证存在 → 配置权限",
        steps=[
            ChainStepConfig(
                action=ActionType.CREATE_USER,
                description="在目标主机创建用户账号「{username}」",
            ),
            ChainStepConfig(
                action=ActionType.CHECK_USER,
                description="验证用户「{username}」是否创建成功",
                depends_on=["step_0"],
            ),
            ChainStepConfig(
                action=ActionType.SERVICE_STATUS,
                description="检查 SSH 服务状态",
                depends_on=["step_1"],
            ),
        ],
    ),

    # ── 3. 服务部署 ─────────────────────────────────────────────────────────
    "deploy_service": TaskChain(
        name="服务部署",
        intent_keywords=[
            # 标准
            "部署服务", "安装服务", "上线服务", "部署应用", "安装并启动",
            "部署nginx", "部署redis", "部署mysql", "部署docker",
            # 口语化
            "帮我部署", "帮我安装服务", "帮我上线", "安装一下服务",
            "部署一下", "安装一下", "帮我装一个服务",
            "给我部署", "给我安装",
        ],
        description="安装包 → 启动服务 → 验证运行状态",
        steps=[
            ChainStepConfig(
                action=ActionType.PACKAGE_INSTALL,
                description="安装软件包「{package}」",
            ),
            ChainStepConfig(
                action=ActionType.SERVICE_START,
                description="启动服务「{service}」",
                depends_on=["step_0"],
            ),
            ChainStepConfig(
                action=ActionType.SERVICE_STATUS,
                description="验证服务「{service}」运行状态",
                depends_on=["step_1"],
            ),
        ],
    ),

    # ── 4. 服务启停管理 ──────────────────────────────────────────────────────
    "service_restart_reload": TaskChain(
        name="服务重启重载",
        intent_keywords=[
            # 标准
            "重启服务", "重载服务", "刷新服务", "reload服务",
            "restart服务", "重新启动服务",
            # 口语化
            "帮我重启", "帮我重载", "帮我reload", "重启一下",
            "重载一下", "重新启动", "restart一下",
            "帮我重启服务", "重启ssh", "重启nginx",
            "关闭服务", "停止服务",
        ],
        description="停止 → 启动 → 验证状态",
        steps=[
            ChainStepConfig(
                action=ActionType.SERVICE_STOP,
                description="停止服务「{service}」",
            ),
            ChainStepConfig(
                action=ActionType.SERVICE_START,
                description="启动服务「{service}」",
                depends_on=["step_0"],
            ),
            ChainStepConfig(
                action=ActionType.SERVICE_STATUS,
                description="验证服务「{service}」状态",
                depends_on=["step_1"],
            ),
        ],
    ),

    # ── 5. 故障排查 ─────────────────────────────────────────────────────────
    "troubleshoot_service": TaskChain(
        name="服务故障排查",
        intent_keywords=[
            # 标准
            "服务故障", "服务挂了", "排查问题", "服务异常",
            "为什么服务", "服务报错", "检查服务", "诊断服务",
            "服务起不来", "服务无法启动",
            # 口语化
            "帮我排查", "帮我看看服务", "服务好像有问题",
            "服务不对劲", "查一下服务", "看看服务怎么了",
            "ssh为什么", "nginx挂了", "服务不工作",
            "帮我诊断", "服务跑不起来",
        ],
        description="检查服务状态 → 查看进程 → 检查日志路径 → 检查端口",
        steps=[
            ChainStepConfig(
                action=ActionType.SERVICE_STATUS,
                description="检查服务「{service}」状态",
            ),
            ChainStepConfig(
                action=ActionType.PROCESS_LIST,
                description="查找相关进程（{service}）",
                depends_on=["step_0"],
            ),
            ChainStepConfig(
                action=ActionType.NETSTAT,
                description="检查端口监听状态",
                depends_on=["step_0"],
            ),
            ChainStepConfig(
                action=ActionType.SYSTEM_INFO,
                description="检查系统资源（排查OOM等）",
                parallel_group="sys_check",
            ),
            ChainStepConfig(
                action=ActionType.MEMORY_USAGE,
                description="检查内存使用",
                parallel_group="sys_check",
            ),
        ],
    ),

    # ── 7. 用户账号检查 ───────────────────────────────────────────────────────
    "check_user": TaskChain(
        name="用户账号检查",
        intent_keywords=[
            "检查账号", "检查用户", "查看账号", "查看用户",
            "账号是否存在", "用户是否存在", "验证账号",
            "帮我查一下", "帮我检查", "看一下这个用户",
            "看看账号", "用户是否存在",
        ],
        description="检查用户是否存在",
        steps=[
            ChainStepConfig(
                action=ActionType.CHECK_USER,
                description="验证用户「{username}」是否存在",
            ),
            ChainStepConfig(
                action=ActionType.SYSTEM_INFO,
                description="获取系统基本信息",
            ),
        ],
    ),

    # ── 6. 批量包管理 ────────────────────────────────────────────────────────
    "batch_package_install": TaskChain(
        name="批量安装软件包",
        intent_keywords=[
            # 含空格的精确说法
            "帮我安装", "帮我装", "安装一下",
            "一整套安装", "批量安装",
            # 短词（让子串匹配生效）
            "安装多个", "同时安装", "安装以下",
            # "X和Y" 格式用"安装"+"和"联合匹配
        ],
        description="批量安装多个软件包",
        steps=[
            ChainStepConfig(
                action=ActionType.PACKAGE_INSTALL,
                description="安装软件包「{package}」",
            ),
        ],
    ),
}


# ─── 链式编排器 ──────────────────────────────────────────────────────────────
class ChainPlanner:
    """将自然语言匹配到任务链，并展开为步骤列表。"""

    def __init__(self):
        self._intent_cache: Dict[str, Tuple[Optional[str], Dict]] = {}

    def match_chain(self, command: str) -> Tuple[Optional[TaskChain], Dict]:
        """匹配用户命令到任务链，返回 (链, 公共参数)。
        使用正则模糊匹配，支持同义词和口语化表达。
        """
        cmd_lower = command.lower()

        for chain_id, chain in TASK_CHAINS.items():
            for kw in chain.intent_keywords:
                kw_l = kw.lower()
                # 1. 子串匹配（最优先）
                if kw_l in cmd_lower:
                    params = self._extract_chain_params(command, chain)
                    logger.info(f"[ChainPlanner] 命中链: {chain.name}，参数: {params}")
                    return chain, params
                # 2. 正则模糊匹配（处理含空格的变体，如"安装 nginx" vs "安装nginx"）
                try:
                    pattern = re.escape(kw_l)
                    # 把中文"和"替换为"任意内容+和+任意内容"（贪心匹配）
                    # 例: "安装nginx和redis" → "安装nginx.*和.*redis"
                    #     能匹配 "安装 nginx 和 redis"（中间有空格和"和"字）
                    pattern = pattern.replace("\u548c", r".*?\u548c.*")
                    # 如果关键词中没有"和"，尝试用"安装"做模糊锚点
                    if re.search(pattern, cmd_lower):
                        params = self._extract_chain_params(command, chain)
                        logger.info(f"[ChainPlanner] 正则命中链: {chain.name}")
                        return chain, params
                except re.error:
                    pass

        return None, {}
    def _extract_chain_params(self, command: str, chain: TaskChain) -> Dict[str, Any]:
        """从命令中提取任务链所需的公共参数。"""
        params: Dict[str, Any] = {}

        # ── 用户名（更宽松：允许动词和用户名之间有任意字符）───────────────
        um = re.search(
            r"(?:账号|用户)\s*[:：]?\s*([a-zA-Z][a-zA-Z0-9_-]{0,31})", command)
        if not um:
            um = re.search(
                r"([a-zA-Z][a-zA-Z0-9_-]{2,31})\s*(?:这个|那个)?(?:账号|用户|用户账号)", command)
        if not um:
            um = re.search(
                r"(?:帮我)?(?:创建|添加|新建|删除|检查|验证)\s*(?:我|一个)?(?:账号|用户)?\s*([a-zA-Z][a-zA-Z0-9_-]{0,31})",
                command,
            )
        if not um:
            # 宽松：从"一下"后面找用户名
            um = re.search(r"一下\s+([a-zA-Z][a-zA-Z0-9_-]{2,31})", command)
        if um:
            params["username"] = um.group(1)

        # ── 密码 ──────────────────────────────────────────────────────────────
        pm = re.search(r"密码\s*[:：]\s*([a-zA-Z0-9@#$%^&*!]{6,32})", command)
        if pm:
            params["password"] = pm.group(1)

        # ── 软件包名（支持 "nginx 和 redis" 格式）────────────────────────────
        if chain.name in ("批量安装软件包", "服务部署"):
            # 仅提取类 Unix 包名，避免「安装\s*(\S+)」把中文逗号后整句吃进 package
            _pkg_tok = r"[a-zA-Z0-9][a-zA-Z0-9._+-]*"
            # 提取"安装X和Y" → 主包=X, 副包=Y
            m = re.search(
                rf"安装\s*({_pkg_tok})\s*(?:和|以及|,)\s*({_pkg_tok})",
                command,
                re.IGNORECASE,
            )
            if m:
                params["package"] = m.group(1).strip()
                params["package2"] = m.group(2).strip()
            else:
                # "安装nginx" / "安装 nginx" → 主包
                m2 = re.search(rf"安装\s*({_pkg_tok})", command, re.IGNORECASE)
                if m2:
                    params["package"] = m2.group(1).strip()
                else:
                    # 从已知包名匹配（按顺序）
                    known = ["nginx", "redis", "mysql", "apache2", "docker", "git", "curl", "vim"]
                    found = [k for k in known if k in command.lower()]
                    if found:
                        params["package"] = found[0]
                        if len(found) > 1:
                            params["package2"] = found[1]

        # ── 服务名 ─────────────────────────────────────────────────────────────
        for svc in ["ssh", "nginx", "apache2", "mysql", "redis", "docker",
                    "postgres", "firewalld", "ufw", "httpd"]:
            if svc in command.lower():
                params["service"] = svc
                # 自动补 package（如果没单独指定）
                if "package" not in params and svc in ("nginx", "apache2", "httpd", "mysql", "redis"):
                    params["package"] = svc
                break
        if "service" not in params and ("服务" in command or "应用" in command):
            params["service"] = "ssh"

        # ── 主机IP ─────────────────────────────────────────────────────────────
        hm = re.search(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", command)
        if hm:
            params["host"] = hm.group(1)

        return params

    def build_plan(
        self,
        chain: TaskChain,
        shared_params: Dict[str, Any],
    ) -> List[Dict]:
        """将任务链展开为执行计划（带描述和依赖信息）。"""
        plan = []
        for i, step_cfg in enumerate(chain.steps):
            step_id = f"step_{i}"

            # 填充占位符
            desc = step_cfg.description
            for k, v in shared_params.items():
                desc = desc.replace(f"{{{k}}}", str(v))

            plan.append({
                "step_id": step_id,
                "action": step_cfg.action,
                "description": desc,
                "depends_on": step_cfg.depends_on,
                "parallel_group": step_cfg.parallel_group,
                "continue_on_fail": step_cfg.continue_on_fail,
                "timeout": step_cfg.timeout,
                "params": dict(shared_params),  # 每个步骤继承公共参数
            })
        return plan


# ─── 链式执行器 ───────────────────────────────────────────────────────────────
class ChainExecutor:
    """执行任务链，支持依赖排序、并行组、回滚。"""

    def __init__(self, host: str, user: str, password: str):
        self.host = host
        self.user = user
        self.password = password
        self._step_results: Dict[str, Dict] = {}

    def execute_plan(
        self,
        plan: List[Dict],
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """按依赖顺序执行计划，返回完整结果。"""
        # 拓扑排序（按依赖关系排序）
        sorted_steps = self._topological_sort(plan)
        logger.info(f"[ChainExecutor] 执行顺序: {[s['step_id'] for s in sorted_steps]}")

        all_results: List[Dict] = []
        failed = False
        failed_step_id: Optional[str] = None
        chain_status = "success"

        for step_def in sorted_steps:
            step_id = step_def["step_id"]

            # 检查依赖是否都成功
            deps = step_def.get("depends_on", [])
            deps_ok = all(
                self._step_results.get(d, {}).get("status") in ("success", "verified")
                for d in deps
            )
            if not deps_ok and deps:
                logger.warning(f"[ChainExecutor] 步骤 {step_id} 依赖未满足，跳过")
                chain_status = "skipped"
                continue

            # 执行
            result = self._execute_step(step_def, dry_run)
            result["step_id"] = step_id
            self._step_results[step_id] = result
            all_results.append(result)

            if result["status"] in ("success", "verified"):
                pass  # 继续
            elif step_def.get("continue_on_fail"):
                logger.warning(f"[ChainExecutor] 步骤 {step_id} 失败但继续")
            else:
                failed = True
                failed_step_id = step_id
                chain_status = "failed"
                logger.warning(f"[ChainExecutor] 步骤 {step_id} 失败，停止执行链")
                break

        return {
            "chain_status": chain_status,
            "failed_step_id": failed_step_id,
            "all_results": all_results,
            "total_steps": len(plan),
            "completed_steps": len(all_results),
        }

    def _execute_step(self, step_def: Dict, dry_run: bool) -> Dict:
        """执行单个步骤。"""
        action = step_def["action"]
        params = step_def["params"]
        desc = step_def["description"]

        # 生成命令
        cmd = build_command(action, params, self.password)
        verify_cmd = build_verify_command(action, params)

        step = ExecutionStep(
            action=action,
            description=desc,
            command=cmd,
            verify_command=verify_cmd,
            rollback_command=None,
            status=StepStatus.PENDING,
        )

        if dry_run:
            return {
                "action": action.value,
                "description": desc,
                "command": cmd,
                "status": "pending",
                "stdout": "(dry_run) 模拟执行",
                "stderr": "",
                "exit_code": 0,
            }

        # 执行（与 engine._run_step 相同逻辑）
        t0 = time.time()
        step.status = StepStatus.RUNNING

        if self.host in ("127.0.0.1", "localhost") or EXECUTION_MODE == "local":
            result = exec_local(step.command)
        else:
            result = exec_ssh(self.host, step.command, self.user, self.password)

        step.stdout = result.stdout
        step.stderr = result.stderr
        step.exit_code = result.exit_code
        step.duration_ms = int((time.time() - t0) * 1000)

        # 判断
        if result.exit_code == 0:
            step.status = StepStatus.SUCCESS
            # 自动验证
            if verify_cmd:
                t0v = time.time()
                verify_r = exec_local(verify_cmd) if self.host in ("127.0.0.1", "localhost") \
                    else exec_ssh(self.host, verify_cmd, self.user, self.password)
                step.verification_output = verify_r.stdout
                step.duration_ms += int((time.time() - t0v) * 1000)
                if verify_r.exit_code == 0:
                    step.status = StepStatus.VERIFIED
                    step.verified = True
                else:
                    step.status = StepStatus.FAILED

        else:
            step.status = StepStatus.FAILED

        return {
            "action": step.action.value,
            "description": step.description,
            "command": step.command,
            "status": step.status.value,
            "stdout": step.stdout,
            "stderr": step.stderr,
            "exit_code": step.exit_code,
            "verified": step.verified,
            "verification_output": step.verification_output,
            "duration_ms": step.duration_ms,
        }

    def _topological_sort(self, plan: List[Dict]) -> List[Dict]:
        """Kahn算法拓扑排序，按依赖顺序返回步骤列表。"""
        # 构建邻接表
        in_degree = {s["step_id"]: 0 for s in plan}
        dep_map: Dict[str, List[Dict]] = {s["step_id"]: [] for s in plan}
        for s in plan:
            for d in s.get("depends_on", []):
                if d in dep_map:
                    dep_map[d].append(s)
                    in_degree[s["step_id"]] += 1

        # 队列
        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        sorted_list: List[Dict] = []
        id_to_step = {s["step_id"]: s for s in plan}

        while queue:
            # 按 parallel_group 分组，同组的可视为无序
            sid = queue.pop(0)
            sorted_list.append(id_to_step[sid])
            for nxt in dep_map[sid]:
                in_degree[nxt["step_id"]] -= 1
                if in_degree[nxt["step_id"]] == 0:
                    queue.append(nxt["step_id"])

        # 未排序的（循环依赖保护）
        for s in plan:
            if s["step_id"] not in [x["step_id"] for x in sorted_list]:
                sorted_list.append(s)

        return sorted_list
