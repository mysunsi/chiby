import * as React from "react";
import {
  createRollout,
  getRolloutStatus,
  cancelRollout,
  rollbackRollout,
  createRolloutWS,
  formatDuration,
  getBatchStatusColor,
  type RolloutStatus,
  type RolloutBatch,
  type RolloutWSMessage,
  type GateConfigInput,
  type RolloutHistory,
} from "@/api/rolloutApi";
import type { CreateRolloutRequest } from "@/api/types";

interface RolloutConsoleProps {
  className?: string;
  /** 预设的目标主机列表 */
  defaultHosts?: string[];
  /** 预设的 SSH 用户 */
  defaultSshUser?: string;
  /** 父组件触发：追加节选到任务描述（工单草稿） */
  workOrderAppend?: { tick: number; text: string };
}

type Tab = "create" | "history";

const DEFAULT_PERCENTS = [10, 50, 100];

export function RolloutConsole({
  className,
  defaultHosts = [],
  defaultSshUser = "root",
  workOrderAppend,
}: RolloutConsoleProps) {
  const [tab, setTab] = React.useState<Tab>("create");
  const [activeId, setActiveId] = React.useState<string | null>(null);
  const [status, setStatus] = React.useState<RolloutStatus | null>(null);
  const [wsDisconnected, setWsDisconnected] = React.useState(false);

  // 创建表单状态
  const [taskText, setTaskText] = React.useState("");
  const [hostsInput, setHostsInput] = React.useState(defaultHosts.join("\n"));
  const [percentsInput, setPercentsInput] = React.useState(DEFAULT_PERCENTS.join(","));
  const [sshUser, setSshUser] = React.useState(defaultSshUser);
  const [sshPassword, setSshPassword] = React.useState("");
  const [dryRun, setDryRun] = React.useState(false);

  // Gate 配置
  const [gateEnabled, setGateEnabled] = React.useState(false);
  const [gateKind, setGateKind] = React.useState<GateConfigInput["kind"]>("port");
  const [gateHost, setGateHost] = React.useState("localhost");
  const [gatePort, setGatePort] = React.useState(80);
  const [gateProcess, setGateProcess] = React.useState("");
  const [gateUrl, setGateUrl] = React.useState("");

  // UI 状态
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  // createdId 已废弃，使用 activeId 替代
  const [_createdId, setCreatedId] = React.useState<string | null>(null);
  const [pollingInterval, setPollingInterval] = React.useState<number | null>(null);

  // WebSocket 连接
  const wsRef = React.useRef<{ ws: WebSocket; disconnect: () => void } | null>(null);

  React.useEffect(() => {
    if (!workOrderAppend?.text) return;
    const block = `[终端摘录 · ${new Date().toLocaleString()}]\n${workOrderAppend.text}`;
    setTaskText((t) => (t.trim() ? `${t}\n\n${block}` : block));
    setTab("create");
  }, [workOrderAppend?.tick]);

  // 清理 WebSocket 和轮询
  React.useEffect(() => {
    return () => {
      wsRef.current?.disconnect();
      if (pollingInterval) {
        clearInterval(pollingInterval);
      }
    };
  }, [pollingInterval]);

  // 监听 activeId 变化，连接 WebSocket 或开始轮询
  React.useEffect(() => {
    if (!activeId) return;

    // 先尝试 WebSocket
    const { ws, disconnect } = createRolloutWS(
      activeId,
      handleWSMessage,
      () => {
        setWsDisconnected(false);
        // WebSocket 连接成功，停止轮询
        if (pollingInterval) {
          clearInterval(pollingInterval);
          setPollingInterval(null);
        }
      },
      () => {
        setWsDisconnected(true);
      },
    );

    wsRef.current = { ws, disconnect };

    // 降级：如果 WebSocket 30 秒未连接，开始轮询
    const fallbackTimer = setTimeout(() => {
      if (ws.readyState !== WebSocket.OPEN) {
        ws.close();
        startPolling();
      }
    }, 30000);

    return () => {
      clearTimeout(fallbackTimer);
      disconnect();
    };
  }, [activeId]);

  function startPolling() {
    const interval = window.setInterval(async () => {
      try {
        const s = await getRolloutStatus(activeId!);
        setStatus(s);
        if (["success", "failed", "cancelled"].includes(s.status)) {
          clearInterval(interval);
          setPollingInterval(null);
        }
      } catch (e) {
        console.error("Polling error:", e);
      }
    }, 1000) as unknown as number;
    setPollingInterval(interval);
  }

  function handleWSMessage(msg: RolloutWSMessage) {
    switch (msg.type) {
      case "status":
        setStatus(() => ({
          ...(msg as unknown as { status: RolloutStatus }).status,
        }));
        break;

      case "rollout_start":
        setStatus((prev) =>
          prev
            ? { ...prev, status: "running", total_hosts: msg.total_hosts, total_batches: msg.total_batches }
            : prev,
        );
        break;

      case "batch_start":
        setStatus((prev) =>
          prev
            ? {
                ...prev,
                current_batch: msg.batch_index,
                batches: prev.batches.map((b, i) =>
                  i === msg.batch_index ? { ...b, status: "running" as const, started_at: new Date().toISOString() } : b,
                ),
              }
            : prev,
        );
        break;

      case "batch_complete":
        setStatus((prev) =>
          prev
            ? {
                ...prev,
                batches: prev.batches.map((b, i) =>
                  i === msg.batch_index
                    ? {
                        ...b,
                        status: "success" as const,
                        success_count: msg.success,
                        failed_count: msg.failed,
                        finished_at: new Date().toISOString(),
                      }
                    : b,
                ),
              }
            : prev,
        );
        break;

      case "gate_check_result":
        setStatus((prev) =>
          prev
            ? {
                ...prev,
                batches: prev.batches.map((b, i) =>
                  i === msg.batch_index
                    ? { ...b, gate_result: msg.gate_result }
                    : b,
                ),
              }
            : prev,
        );
        break;

      case "rollout_complete": {
        const completeMsg = msg as { type: "rollout_complete"; id: string; status: string; total_duration_ms: number; error_message?: string };
        setStatus((prev) =>
          prev
            ? {
                ...prev,
                status: (completeMsg.status || "failed") as RolloutStatus["status"],
                error_message: completeMsg.error_message,
                total_duration_ms: completeMsg.total_duration_ms || 0,
              }
            : prev,
        );
        break;
      }
      case "rollout_error": {
        const errorMsg = msg as { type: "rollout_error"; id: string; error: string };
        setStatus((prev) =>
          prev
            ? {
                ...prev,
                status: "failed" as const,
                error_message: errorMsg.error,
              }
            : prev,
        );
        break;
      }

      case "cancelled":
        setStatus((prev) =>
          prev ? { ...prev, status: "cancelled" as const } : prev,
        );
        break;

      case "gate_failed":
        setStatus((prev) =>
          prev
            ? {
                ...prev,
                status: "failed",
                error_message: msg.error,
                batches: prev.batches.map((b, i) =>
                  i === msg.batch_index ? { ...b, status: "failed" as const, error_message: msg.error } : b,
                ),
              }
            : prev,
        );
        break;
    }
  }

  async function handleCreate() {
    setError(null);
    setLoading(true);

    try {
      const hosts = hostsInput.split("\n").map((h) => h.trim()).filter(Boolean);
      const percents = percentsInput.split(",").map((p) => parseInt(p.trim(), 10)).filter((p) => !isNaN(p) && p > 0 && p <= 100);

      if (hosts.length === 0) {
        throw new Error("请至少输入一个目标主机");
      }

      if (percents.length === 0) {
        throw new Error("请输入有效的百分比");
      }

      const req: CreateRolloutRequest = {
        task_text: taskText,
        hosts,
        percents,
        ssh_user: sshUser,
        ssh_password: sshPassword || undefined,
        dry_run: dryRun,
      };

      if (gateEnabled) {
        const gate: GateConfigInput = { kind: gateKind };
        if (gateKind === "port") {
          gate.host = gateHost;
          gate.port = gatePort;
        } else if (gateKind === "process") {
          gate.process_name = gateProcess;
        } else if (gateKind === "http") {
          gate.url = gateUrl;
        }
        req.gate = gate;
      }

      const result = await createRollout(req);

      if (dryRun) {
        setTab("history");
        setCreatedId(result.id);
        setLoading(false);
        return;
      }

      setCreatedId(result.id);
      setActiveId(result.id);
      setTab("create");

      // 获取初始状态
      const initialStatus = await getRolloutStatus(result.id);
      setStatus(initialStatus);

      // 如果已完成，停止
      if (["success", "failed", "cancelled"].includes(initialStatus.status)) {
        // 已完成
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建失败");
    } finally {
      setLoading(false);
    }
  }

  async function handleCancel() {
    if (!activeId) return;
    try {
      await cancelRollout(activeId);
      setStatus((prev) => (prev ? { ...prev, status: "cancelled" } : prev));
    } catch (e) {
      setError(e instanceof Error ? e.message : "取消失败");
    }
  }

  async function handleRollback() {
    if (!activeId) return;
    try {
      await rollbackRollout(activeId);
      setStatus((prev) => (prev ? { ...prev, status: "rolling_back" } : prev));
    } catch (e) {
      setError(e instanceof Error ? e.message : "回滚失败");
    }
  }

  function handleSelectHistory(id: string) {
    setActiveId(id);
    setTab("create");
    getRolloutStatus(id).then(setStatus).catch(console.error);
  }

  return (
    <div className={cn("flex flex-col h-full", className)}>
      {/* 标签页 */}
      <div className="flex border-b border-gray-700">
        <button
          className={cn("px-4 py-2 text-sm", tab === "create" ? "border-b-2 border-blue-500 text-blue-400" : "text-gray-400 hover:text-gray-300")}
          onClick={() => setTab("create")}
        >
          创建发布
        </button>
        <button
          className={cn("px-4 py-2 text-sm", tab === "history" ? "border-b-2 border-blue-500 text-blue-400" : "text-gray-400 hover:text-gray-300")}
          onClick={() => setTab("history")}
        >
          历史记录
        </button>
      </div>

      {/* 内容 */}
      <div className="flex-1 overflow-auto p-4">
        {tab === "create" ? (
          <div className="space-y-4">
            {/* 状态面板 */}
            {status && (
              <StatusPanel
                status={status}
                onCancel={handleCancel}
                onRollback={handleRollback}
                wsDisconnected={wsDisconnected}
              />
            )}

            {/* 创建表单 */}
            {!status || status.status === "pending" ? (
              <CreateForm
                taskText={taskText}
                onTaskTextChange={setTaskText}
                hostsInput={hostsInput}
                onHostsInputChange={setHostsInput}
                percentsInput={percentsInput}
                onPercentsInputChange={setPercentsInput}
                sshUser={sshUser}
                onSshUserChange={setSshUser}
                sshPassword={sshPassword}
                onSshPasswordChange={setSshPassword}
                dryRun={dryRun}
                onDryRunChange={setDryRun}
                gateEnabled={gateEnabled}
                onGateEnabledChange={setGateEnabled}
                gateKind={gateKind}
                onGateKindChange={setGateKind}
                gateHost={gateHost}
                onGateHostChange={setGateHost}
                gatePort={gatePort}
                onGatePortChange={setGatePort}
                gateProcess={gateProcess}
                onGateProcessChange={setGateProcess}
                gateUrl={gateUrl}
                onGateUrlChange={setGateUrl}
                onSubmit={handleCreate}
                loading={loading}
              />
            ) : null}

            {/* 错误 */}
            {error && (
              <div className="bg-red-900/30 border border-red-700 rounded px-3 py-2 text-red-400 text-sm">
                {error}
              </div>
            )}
          </div>
        ) : (
          <HistoryPanel onSelect={handleSelectHistory} />
        )}
      </div>
    </div>
  );
}

// ─── 状态面板 ───────────────────────────────────────────────────────────────

interface StatusPanelProps {
  status: RolloutStatus;
  onCancel: () => void;
  onRollback: () => void;
  wsDisconnected: boolean;
}

function StatusPanel({ status, onCancel, onRollback, wsDisconnected }: StatusPanelProps) {
  const statusColors: Record<string, string> = {
    pending: "text-gray-400",
    running: "text-blue-400",
    success: "text-green-400",
    failed: "text-red-400",
    cancelled: "text-yellow-400",
    rolling_back: "text-orange-400",
  };

  return (
    <div className="bg-gray-800 rounded-lg p-4 space-y-3">
      {/* 头部 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-lg font-medium">发布进度</span>
          <span className={cn("text-sm font-medium", statusColors[status.status] || "text-gray-400")}>
            {STATUS_LABELS[status.status] || status.status}
          </span>
          {wsDisconnected && (
            <span className="text-xs text-yellow-500">(WebSocket 断开，轮询中)</span>
          )}
        </div>
        <div className="flex gap-2">
          {status.status === "running" || status.status === "pending" ? (
            <button
              onClick={onCancel}
              className="px-3 py-1 text-sm bg-red-600 hover:bg-red-700 rounded transition"
            >
              取消
            </button>
          ) : null}
          {["success", "failed", "cancelled"].includes(status.status) ? (
            <button
              onClick={onRollback}
              className="px-3 py-1 text-sm bg-orange-600 hover:bg-orange-700 rounded transition"
            >
              回滚
            </button>
          ) : null}
        </div>
      </div>

      {/* 概览 */}
      <div className="grid grid-cols-4 gap-4 text-sm">
        <div>
          <span className="text-gray-400">任务 ID</span>
          <div className="font-mono text-blue-400">{status.id}</div>
        </div>
        <div>
          <span className="text-gray-400">总主机</span>
          <div className="text-lg">{status.total_hosts} 台</div>
        </div>
        <div>
          <span className="text-gray-400">当前批次</span>
          <div className="text-lg">
            {status.current_batch} / {status.total_batches}
          </div>
        </div>
        <div>
          <span className="text-gray-400">耗时</span>
          <div className="text-lg">{formatDuration(status.total_duration_ms)}</div>
        </div>
      </div>

      {/* 批次进度条 */}
      <div>
        <div className="text-sm text-gray-400 mb-1">
          批次进度: {Math.round((status.current_batch / status.total_batches) * 100)}%
        </div>
        <div className="flex h-2 rounded overflow-hidden bg-gray-700">
          {status.batches.map((batch, i) => (
            <div
              key={i}
              className={cn(
                "flex-1 transition-colors",
                batch.status === "success"
                  ? "bg-green-500"
                  : batch.status === "failed"
                  ? "bg-red-500"
                  : batch.status === "running"
                  ? "bg-blue-500 animate-pulse"
                  : batch.status === "gate_checking"
                  ? "bg-yellow-500 animate-pulse"
                  : "bg-gray-600",
              )}
              title={`批次 ${i + 1}: ${batch.status}`}
            />
          ))}
        </div>
      </div>

      {/* 批次列表 */}
      <div className="space-y-2">
        {status.batches.map((batch) => (
          <BatchRow key={batch.batch_index} batch={batch} />
        ))}
      </div>

      {/* 错误消息 */}
      {status.error_message && (
        <div className="bg-red-900/30 border border-red-700 rounded px-3 py-2 text-red-400 text-sm">
          {status.error_message}
        </div>
      )}
    </div>
  );
}

function BatchRow({ batch }: { batch: RolloutBatch }) {
  const [expanded, setExpanded] = React.useState(false);

  const statusIcons: Record<string, string> = {
    pending: "○",
    running: "◐",
    gate_checking: "◔",
    success: "●",
    failed: "✕",
    skipped: "◌",
  };

  return (
    <div className="bg-gray-700/50 rounded px-3 py-2">
      <div className="flex items-center justify-between cursor-pointer" onClick={() => setExpanded(!expanded)}>
        <div className="flex items-center gap-3">
          <span className={getBatchStatusColor(batch.status)}>{statusIcons[batch.status]}</span>
          <span className="font-medium">批次 {batch.batch_index + 1}</span>
          <span className="text-sm text-gray-400">{batch.percent}%</span>
          <span className="text-sm text-gray-400">({batch.host_count} 台)</span>
        </div>
        <div className="flex items-center gap-3 text-sm">
          {batch.success_count > 0 && (
            <span className="text-green-400">✓ {batch.success_count}</span>
          )}
          {batch.failed_count > 0 && (
            <span className="text-red-400">✕ {batch.failed_count}</span>
          )}
          {batch.duration_ms > 0 && (
            <span className="text-gray-400">{formatDuration(batch.duration_ms)}</span>
          )}
          <span className="text-gray-500">{expanded ? "▲" : "▼"}</span>
        </div>
      </div>

      {expanded && (
        <div className="mt-2 pl-6 space-y-1 text-sm">
          <div className="text-gray-400">
            主机: {batch.hosts.join(", ")}
          </div>
          {batch.gate_result && (
            <div className={batch.gate_result.passed ? "text-green-400" : "text-red-400"}>
              Gate: {batch.gate_result.passed ? "✓ 通过" : "✕ 失败"} - {batch.gate_result.message}
            </div>
          )}
          {batch.error_message && (
            <div className="text-red-400">错误: {batch.error_message}</div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── 创建表单 ───────────────────────────────────────────────────────────────

interface CreateFormProps {
  taskText: string;
  onTaskTextChange: (v: string) => void;
  hostsInput: string;
  onHostsInputChange: (v: string) => void;
  percentsInput: string;
  onPercentsInputChange: (v: string) => void;
  sshUser: string;
  onSshUserChange: (v: string) => void;
  sshPassword: string;
  onSshPasswordChange: (v: string) => void;
  dryRun: boolean;
  onDryRunChange: (v: boolean) => void;
  gateEnabled: boolean;
  onGateEnabledChange: (v: boolean) => void;
  gateKind: GateConfigInput["kind"];
  onGateKindChange: (v: GateConfigInput["kind"]) => void;
  gateHost: string;
  onGateHostChange: (v: string) => void;
  gatePort: number;
  onGatePortChange: (v: number) => void;
  gateProcess: string;
  onGateProcessChange: (v: string) => void;
  gateUrl: string;
  onGateUrlChange: (v: string) => void;
  onSubmit: () => void;
  loading: boolean;
}

function CreateForm({
  taskText,
  onTaskTextChange,
  hostsInput,
  onHostsInputChange,
  percentsInput,
  onPercentsInputChange,
  sshUser,
  onSshUserChange,
  sshPassword,
  onSshPasswordChange,
  dryRun,
  onDryRunChange,
  gateEnabled,
  onGateEnabledChange,
  gateKind,
  onGateKindChange,
  gateHost,
  onGateHostChange,
  gatePort,
  onGatePortChange,
  gateProcess,
  onGateProcessChange,
  gateUrl,
  onGateUrlChange,
  onSubmit,
  loading,
}: CreateFormProps) {
  return (
    <div className="bg-gray-800 rounded-lg p-4 space-y-4">
      <h3 className="text-lg font-medium">创建灰度发布</h3>

      {/* 运维指令 */}
      <div>
        <label className="block text-sm text-gray-400 mb-1">运维指令 *</label>
        <textarea
          className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white placeholder-gray-400 focus:outline-none focus:border-blue-500"
          rows={3}
          placeholder="例如: 部署 nginx 配置到所有主机"
          value={taskText}
          onChange={(e) => onTaskTextChange(e.target.value)}
        />
      </div>

      {/* 目标主机 */}
      <div>
        <label className="block text-sm text-gray-400 mb-1">目标主机 (每行一个) *</label>
        <textarea
          className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white font-mono text-sm focus:outline-none focus:border-blue-500"
          rows={5}
          placeholder="172.25.87.85&#10;172.25.87.86&#10;172.25.87.87"
          value={hostsInput}
          onChange={(e) => onHostsInputChange(e.target.value)}
        />
      </div>

      {/* 灰度百分比 */}
      <div>
        <label className="block text-sm text-gray-400 mb-1">灰度百分比 (逗号分隔)</label>
        <input
          type="text"
          className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white focus:outline-none focus:border-blue-500"
          placeholder="10, 50, 100"
          value={percentsInput}
          onChange={(e) => onPercentsInputChange(e.target.value)}
        />
        <p className="text-xs text-gray-500 mt-1">例如: 10,50,100 表示先发布 10%，再 50%，最后 100%</p>
      </div>

      {/* SSH 认证 */}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm text-gray-400 mb-1">SSH 用户</label>
          <input
            type="text"
            className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white focus:outline-none focus:border-blue-500"
            placeholder="root"
            value={sshUser}
            onChange={(e) => onSshUserChange(e.target.value)}
          />
        </div>
        <div>
          <label className="block text-sm text-gray-400 mb-1">SSH 密码</label>
          <input
            type="password"
            className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white focus:outline-none focus:border-blue-500"
            placeholder="留空使用默认"
            value={sshPassword}
            onChange={(e) => onSshPasswordChange(e.target.value)}
          />
        </div>
      </div>

      {/* Gate 配置 */}
      <div>
        <label className="flex items-center gap-2 text-sm text-gray-400 cursor-pointer">
          <input
            type="checkbox"
            checked={gateEnabled}
            onChange={(e) => onGateEnabledChange(e.target.checked)}
            className="w-4 h-4"
          />
          启用 Gate 健康检查
        </label>

        {gateEnabled && (
          <div className="mt-2 pl-6 space-y-2">
            <div>
              <label className="block text-sm text-gray-400 mb-1">检查类型</label>
              <select
                className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white focus:outline-none focus:border-blue-500"
                value={gateKind}
                onChange={(e) => onGateKindChange(e.target.value as GateConfigInput["kind"])}
              >
                <option value="port">端口检查</option>
                <option value="process">进程检查</option>
                <option value="http">HTTP 检查</option>
              </select>
            </div>

            {gateKind === "port" && (
              <div className="grid grid-cols-2 gap-2">
                <input
                  type="text"
                  className="bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white text-sm"
                  placeholder="主机"
                  value={gateHost}
                  onChange={(e) => onGateHostChange(e.target.value)}
                />
                <input
                  type="number"
                  className="bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white text-sm"
                  placeholder="端口"
                  value={gatePort}
                  onChange={(e) => onGatePortChange(parseInt(e.target.value, 10))}
                />
              </div>
            )}

            {gateKind === "process" && (
              <input
                type="text"
                className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white text-sm"
                placeholder="进程名，如 nginx"
                value={gateProcess}
                onChange={(e) => onGateProcessChange(e.target.value)}
              />
            )}

            {gateKind === "http" && (
              <input
                type="text"
                className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white text-sm"
                placeholder="http://..."
                value={gateUrl}
                onChange={(e) => onGateUrlChange(e.target.value)}
              />
            )}
          </div>
        )}
      </div>

      {/* 干运行 */}
      <label className="flex items-center gap-2 text-sm text-gray-400 cursor-pointer">
        <input
          type="checkbox"
          checked={dryRun}
          onChange={(e) => onDryRunChange(e.target.checked)}
          className="w-4 h-4"
        />
        干运行 (仅预览，不实际执行)
      </label>

      {/* 提交按钮 */}
      <button
        onClick={onSubmit}
        disabled={loading || !taskText.trim() || !hostsInput.trim()}
        className="w-full py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 disabled:cursor-not-allowed rounded text-white font-medium transition"
      >
        {loading ? "创建中..." : "创建发布"}
      </button>
    </div>
  );
}

// ─── 历史记录 ───────────────────────────────────────────────────────────────

function HistoryPanel({ onSelect }: { onSelect: (id: string) => void }) {
  const [history, setHistory] = React.useState<RolloutHistory["rollouts"] | null>(null);
  const [loading, setLoading] = React.useState(false);

  React.useEffect(() => {
    setLoading(true);
    fetch("/api/v1/rollout/history/list?limit=20")
      .then((r) => r.json())
      .then((data) => setHistory(data.rollouts || []))
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className="text-center text-gray-400 py-8">加载中...</div>;
  }

  if (!history || history.length === 0) {
    return <div className="text-center text-gray-400 py-8">暂无历史记录</div>;
  }

  const statusColors: Record<string, string> = {
    pending: "text-gray-400",
    running: "text-blue-400",
    success: "text-green-400",
    failed: "text-red-400",
    cancelled: "text-yellow-400",
    rolling_back: "text-orange-400",
  };

  return (
    <div className="space-y-2">
      <h3 className="text-lg font-medium mb-4">历史记录</h3>
      {history.map((item) => (
        <div
          key={item.id}
          className="bg-gray-800 rounded-lg p-3 cursor-pointer hover:bg-gray-700 transition"
          onClick={() => onSelect(item.id)}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="font-mono text-blue-400">{item.id}</span>
              <span className={statusColors[item.status] || "text-gray-400"}>
                {STATUS_LABELS[item.status] || item.status}
              </span>
            </div>
            <div className="text-sm text-gray-400">
              {formatDuration(item.duration_ms)}
            </div>
          </div>
          <div className="mt-1 text-sm text-gray-400 truncate">
            {item.task_text}
          </div>
          <div className="mt-1 text-xs text-gray-500">
            {item.created_at} | {item.current_batch}/{item.total_batches} 批次 | {item.total_hosts} 台主机
          </div>
        </div>
      ))}
    </div>
  );
}

// ─── 辅助 ───────────────────────────────────────────────────────────────────

const STATUS_LABELS: Record<string, string> = {
  pending: "待执行",
  running: "执行中",
  success: "成功",
  failed: "失败",
  cancelled: "已取消",
  rolling_back: "回滚中",
  planned: "已计划",
};

function cn(...args: (string | undefined | null | false)[]): string {
  return args.filter(Boolean).join(" ");
}
