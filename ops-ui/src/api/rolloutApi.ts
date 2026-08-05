/**
 * 灰度发布 API 客户端
 * 
 * API 端点:
 *   POST   /api/v1/rollout              创建灰度发布
 *   GET    /api/v1/rollout/{id}         获取发布状态
 *   GET    /api/v1/rollout/{id}/batches 获取批次详情
 *   POST   /api/v1/rollout/{id}/cancel  取消发布
 *   POST   /api/v1/rollout/{id}/rollback 回滚上一批次
 *   GET    /api/v1/rollout/history/list 查询历史
 * 
 * WebSocket:
 *   WS     /ws/rollout/{id}             实时进度推送
 */

import type { GateConfigInput, CreateRolloutRequest, RolloutStatusResponse, RolloutBatchStatus } from "./types";

// ─── 类型定义 ────────────────────────────────────────────────────────────────

export { GateConfigInput, CreateRolloutRequest, RolloutStatusResponse, RolloutBatchStatus };

export interface RolloutBatch {
  batch_index: number;
  percent: number;
  host_count: number;
  hosts: string[];
  status: "pending" | "running" | "gate_checking" | "success" | "failed" | "skipped";
  success_count: number;
  failed_count: number;
  gate_result?: {
    passed: boolean;
    message: string;
    details?: Record<string, boolean>;
  };
  error_message?: string;
  duration_ms: number;
  started_at?: string;
  finished_at?: string;
}

export interface RolloutStatus {
  id: string;
  task_text: string;
  status: "pending" | "running" | "success" | "failed" | "cancelled" | "rolling_back";
  total_hosts: number;
  total_batches: number;
  current_batch: number;
  percents: number[];
  batches: RolloutBatch[];
  gate_config?: GateConfigInput;
  created_at: string;
  updated_at: string;
  total_duration_ms: number;
  error_message?: string;
}

export interface RolloutPlan {
  total_hosts: number;
  total_batches: number;
  percents: number[];
  batches?: { index: number; percent: number; hosts: string[] }[];
  gate?: GateConfigInput | null;
}

export interface RolloutCreated {
  id: string;
  status: string;
  plan: RolloutPlan;
  message: string;
}

export interface RolloutHistory {
  total: number;
  limit: number;
  offset: number;
  rollouts: {
    id: string;
    task_text: string;
    status: string;
    total_hosts: number;
    total_batches: number;
    current_batch: number;
    created_at: string;
    updated_at: string;
    duration_ms: number;
  }[];
}

// ─── WebSocket 消息类型 ─────────────────────────────────────────────────────

export type RolloutWSMessage =
  | { type: "status"; [key: string]: unknown }
  | { type: "rollout_start"; id: string; total_hosts: number; total_batches: number }
  | { type: "batch_start"; batch_index: number; hosts: string[] }
  | { type: "batch_complete"; batch_index: number; success: number; failed: number; results?: unknown[] }
  | { type: "batch_error"; batch_index: number; error: string }
  | { type: "gate_check_start"; batch_index: number }
  | { type: "gate_check_result"; batch_index: number; gate_result: { passed: boolean; message: string } }
  | { type: "gate_failed"; batch_index: number; error: string }
  | { type: "rollout_complete"; id: string; status: string; total_duration_ms: number; error_message?: string }
  | { type: "rollout_error"; id: string; error: string }
  | { type: "cancelled"; batch_index: number }
  | { type: "ping" }
  | { type: "pong" }
  | { type: "error"; message: string };

// ─── API 客户端 ─────────────────────────────────────────────────────────────

const API_BASE = "/api/v1/rollout";
const WS_BASE = "/ws/rollout";

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(error.detail || `HTTP ${res.status}`);
  }

  return res.json();
}

/**
 * 创建灰度发布
 */
export async function createRollout(req: CreateRolloutRequest): Promise<RolloutCreated> {
  return request<RolloutCreated>(`${API_BASE}`, {
    method: "POST",
    body: JSON.stringify(req),
  });
}

/**
 * 获取发布状态
 */
export async function getRolloutStatus(id: string): Promise<RolloutStatus> {
  return request<RolloutStatus>(`${API_BASE}/${id}`);
}

/**
 * 获取所有批次详情
 */
export async function getRolloutBatches(id: string): Promise<{ rollout_id: string; batches: RolloutBatch[] }> {
  return request(`${API_BASE}/${id}/batches`);
}

/**
 * 取消发布
 */
export async function cancelRollout(id: string): Promise<{ ok: boolean; id: string; status: string }> {
  return request(`${API_BASE}/${id}/cancel`, { method: "POST" });
}

/**
 * 回滚到上一成功批次
 */
export async function rollbackRollout(id: string): Promise<{ ok: boolean; id: string; rollback_to_batch: number; message: string }> {
  return request(`${API_BASE}/${id}/rollback`, { method: "POST" });
}

/**
 * 查询发布历史
 */
export async function getRolloutHistory(params?: {
  limit?: number;
  offset?: number;
  status?: string;
}): Promise<RolloutHistory> {
  const qs = new URLSearchParams();
  if (params?.limit) qs.set("limit", String(params.limit));
  if (params?.offset) qs.set("offset", String(params.offset));
  if (params?.status) qs.set("status", params.status);

  const query = qs.toString();
  return request<RolloutHistory>(`${API_BASE}/history/list${query ? `?${query}` : ""}`);
}

/**
 * 创建 WebSocket 连接用于实时进度
 */
export function createRolloutWS(
  id: string,
  onMessage: (msg: RolloutWSMessage) => void,
  onConnect?: () => void,
  onDisconnect?: () => void,
): { ws: WebSocket; disconnect: () => void } {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}${WS_BASE}/${id}`;
  const ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log(`[RolloutWS] Connected: ${id}`);
    onConnect?.();
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data) as RolloutWSMessage;
      onMessage(msg);
    } catch (e) {
      console.error("[RolloutWS] Parse error:", e);
    }
  };

  ws.onerror = (error) => {
    console.error("[RolloutWS] Error:", error);
  };

  ws.onclose = () => {
    console.log(`[RolloutWS] Disconnected: ${id}`);
    onDisconnect?.();
  };

  return {
    ws,
    disconnect: () => {
      ws.close();
    },
  };
}

/**
 * 轮询获取状态（WebSocket 不可用时的降级方案）
 */
export async function pollRolloutStatus(
  id: string,
  intervalMs = 1000,
  stopWhenComplete = true,
  onUpdate?: (status: RolloutStatus) => void,
): Promise<RolloutStatus> {
  return new Promise((resolve, reject) => {
    let stopped = false;

    const poll = async () => {
      if (stopped) return;

      try {
        const status = await getRolloutStatus(id);
        onUpdate?.(status);

        if (stopWhenComplete && ["success", "failed", "cancelled"].includes(status.status)) {
          stopped = true;
          resolve(status);
          return;
        }

        setTimeout(poll, intervalMs);
      } catch (e) {
        if (!stopped) {
          stopped = true;
          reject(e);
        }
      }
    };

    poll();
  });
}

// ─── 辅助函数 ───────────────────────────────────────────────────────────────

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const remainingS = s % 60;
  return `${m}m ${remainingS}s`;
}

export function getBatchStatusColor(status: RolloutBatch["status"]): string {
  switch (status) {
    case "pending": return "text-gray-400";
    case "running": return "text-blue-500";
    case "gate_checking": return "text-yellow-500";
    case "success": return "text-green-500";
    case "failed": return "text-red-500";
    case "skipped": return "text-gray-400";
    default: return "text-gray-500";
  }
}

export function getBatchStatusBadge(status: RolloutBatch["status"]): string {
  switch (status) {
    case "pending": return "badge-gray";
    case "running": return "badge-blue";
    case "gate_checking": return "badge-yellow";
    case "success": return "badge-green";
    case "failed": return "badge-red";
    case "skipped": return "badge-gray";
    default: return "badge-gray";
  }
}
