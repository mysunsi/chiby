export interface ExecuteRequest {
  command: string;
  sessionId?: string;
}

export interface ExecuteResponse {
  exitCode: number;
  stdout: string;
  stderr: string;
  judgment: "success" | "failure";
}

// ─── 灰度发布类型 ────────────────────────────────────────────────────────────────

export interface GateConfigInput {
  kind: "http" | "port" | "process" | "promql" | "cmd";
  url?: string;
  port?: number;
  host?: string;
  process_name?: string;
  prom_url?: string;
  prom_query?: string;
  prom_op?: string;
  prom_threshold?: number;
  cmd?: string;
  timeout_s?: number;
}

export interface CreateRolloutRequest {
  task_text: string;
  hosts: string[];
  percents?: number[];
  gate?: GateConfigInput;
  ssh_user?: string;
  ssh_password?: string;
  dry_run?: boolean;
}

export interface RolloutBatchStatus {
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

export interface RolloutStatusResponse {
  id: string;
  task_text: string;
  status: "pending" | "running" | "success" | "failed" | "cancelled" | "rolling_back";
  total_hosts: number;
  total_batches: number;
  current_batch: number;
  percents: number[];
  batches: RolloutBatchStatus[];
  gate_config?: GateConfigInput;
  created_at: string;
  updated_at: string;
  total_duration_ms: number;
  error_message?: string;
}

export interface RolloutCreatedResponse {
  id: string;
  status: string;
  plan: {
    total_hosts: number;
    total_batches: number;
    percents: number[];
    batches?: { index: number; percent: number; hosts: string[] }[];
    gate?: GateConfigInput | null;
  };
  message: string;
}
