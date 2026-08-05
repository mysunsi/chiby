import type { AiStreamReplayEvent, AiStreamWsPayload } from "@/lib/aiStreamTypes";

/** WS 帧或审计回放事件（语义一致；`ts` 不参与折叠文本） */
export type AiStreamFoldableEvent = AiStreamWsPayload | AiStreamReplayEvent;

/**
 * 单帧增量（用于实时 WebSocket 与演示）。
 * `think_chunk` 等元数据不改变「按 node_id 拼接 delta」的规则。
 */
export function applyOneStreamEvent(
  prev: Record<string, string>,
  ev: AiStreamFoldableEvent,
): Record<string, string> {
  const next = { ...prev };
  if (ev.type === "ai_stream_start") {
    next[ev.node_id] = "";
    return next;
  }
  if (ev.type === "ai_stream_delta") {
    next[ev.node_id] = (next[ev.node_id] ?? "") + (ev.delta || "");
    return next;
  }
  if (ev.type === "ai_stream_end") {
    if (ev.llm_resp?.explanation != null && ev.llm_resp.explanation !== "") {
      next[ev.node_id] = ev.llm_resp.explanation;
    }
    return next;
  }
  return next;
}

/** 将事件序列折叠为「节点 id → 当前流式文本」用于 Timeline */
export function foldAiStreamToNodeText(
  events: AiStreamFoldableEvent[],
): Record<string, string> {
  let acc: Record<string, string> = {};
  for (const ev of events) {
    acc = applyOneStreamEvent(acc, ev);
  }
  return acc;
}
