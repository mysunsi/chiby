/**
 * 与终端 FastAPI WebSocket 对齐的 AI 流式协议（ai_stream_*）。
 *
 * **审计**：`ops_core/ai_stream_audit.append_ai_stream_event` 在每行前追加：
 * `{ "ts": "<UTC ISO8601>", "session_id": "..." }`，其余键与发往浏览器的 WS 帧一致。
 *
 * **HTTP 回放**：`GET /api/sessions/{session_id}/ai_stream` →
 * `{ "session_id": string, "events": AiStreamReplayEvent[] }`
 *
 * **思考语义块**：`ai_stream_delta` 可携带 `think_chunk` / `thought_type` /
 * `duration_ms` / `context`（详见 `terminal/ai_stream.py`）。
 */
export type AiStreamPhase = "llm" | "plan";

/** 服务端 `_paragraphs_and_thoughts` 推导的语义块标签 */
export type ThoughtType = "thought" | "action" | "decision" | "result";

export interface AiStreamStartPayload {
  type: "ai_stream_start";
  session_id: string;
  message_id: string;
  stream_id: string;
  node_id: string;
  seq: number;
  phase?: AiStreamPhase;
  stream_kind?: string;
  plan_id?: string;
}

export interface AiStreamDeltaPayload {
  type: "ai_stream_delta";
  session_id: string;
  message_id: string;
  stream_id: string;
  node_id: string;
  seq: number;
  delta: string;
  /** 可选：计划预览流 */
  plan_id?: string;
  /**
   * 语义块序号（按说明文字空行切段，≥1）。
   * 省略时回放 / 前端按「整块文本」打字（兼容旧审计行）。
   */
  think_chunk?: number;
  thought_type?: ThoughtType;
  /** 与该语义块对齐的建议最短展示毫秒（服务端提示；前端可自行节流） */
  duration_ms?: number;
  /** 折叠「依据」，如上文要点摘录 */
  context?: string;
}

/** 与终端右侧卡片字段一致（结束帧嵌套 llm_resp） */
export interface LlmRespPayload {
  type?: string;
  session_id?: string;
  explanation?: string;
  command?: string;
  dangerous?: boolean;
  warning?: string;
  confirm_required?: boolean;
  should_execute?: boolean;
  ai_card_id?: string;
  auto_executed?: boolean;
}

export interface AiStreamEndPayload {
  type: "ai_stream_end";
  session_id: string;
  message_id: string;
  stream_id: string;
  node_id: string;
  seq: number;
  stream_kind?: string;
  plan_id?: string;
  llm_resp: LlmRespPayload | null;
}

/** 纯 WS 帧（无审计时间戳） */
export type AiStreamWsPayload =
  | AiStreamStartPayload
  | AiStreamDeltaPayload
  | AiStreamEndPayload;

/** 审计 JSONL 单行、`/ai_stream` 返回的 events[] 元素：在 WS 帧上多 `ts` */
export interface AiStreamAuditMeta {
  /** 服务端写入：`datetime.now(timezone.utc).isoformat()` */
  ts?: string;
}

export type AiStreamReplayStart = AiStreamStartPayload & AiStreamAuditMeta;
export type AiStreamReplayDelta = AiStreamDeltaPayload & AiStreamAuditMeta;
export type AiStreamReplayEnd = AiStreamEndPayload & AiStreamAuditMeta;

export type AiStreamReplayEvent =
  | AiStreamReplayStart
  | AiStreamReplayDelta
  | AiStreamReplayEnd;

/** REST 回放体 */
export interface AiStreamAuditApiResponse {
  session_id: string;
  events: AiStreamReplayEvent[];
}

export function isAiStreamStart(
  ev: AiStreamReplayEvent | AiStreamWsPayload,
): ev is AiStreamStartPayload | AiStreamReplayStart {
  return ev.type === "ai_stream_start";
}

export function isAiStreamDelta(
  ev: AiStreamReplayEvent | AiStreamWsPayload,
): ev is AiStreamDeltaPayload | AiStreamReplayDelta {
  return ev.type === "ai_stream_delta";
}

export function isAiStreamEnd(
  ev: AiStreamReplayEvent | AiStreamWsPayload,
): ev is AiStreamEndPayload | AiStreamReplayEnd {
  return ev.type === "ai_stream_end";
}
