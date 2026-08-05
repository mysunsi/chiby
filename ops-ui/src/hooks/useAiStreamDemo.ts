import * as React from "react";
import type {
  AiStreamDeltaPayload,
  AiStreamEndPayload,
  AiStreamStartPayload,
} from "@/lib/aiStreamTypes";
import { applyOneStreamEvent } from "@/lib/foldAiStreamEvents";

const DEMO_SESSION = "ops-ui-demo";
const DEMO_NODE = "node_llm_demo";

/** 与终端 WS 相同帧形状：演示 Timeline 节点内流式文本（无后端时） */
export function useAiStreamDemo(enabled: boolean): Record<string, string> {
  const [streamTextByNodeId, setStreamTextByNodeId] = React.useState<
    Record<string, string>
  >({});

  React.useEffect(() => {
    if (!enabled) return;

    const explanation =
      "建议先核对命名空间与工作负载。\n\n"
      + "可使用 kubectl get pods -n prod 查看；若 ImagePullBackOff，请先 docker pull 对应镜像。";
    const messageId = "demo-msg-" + Date.now().toString(36);
    const streamId = "demo-str-" + Date.now().toString(36);

    const start: AiStreamStartPayload = {
      type: "ai_stream_start",
      session_id: DEMO_SESSION,
      message_id: messageId,
      stream_id: streamId,
      node_id: DEMO_NODE,
      seq: 0,
      phase: "llm",
      stream_kind: "llm_resp",
    };

    setStreamTextByNodeId((p) => applyOneStreamEvent(p, start));

    const splitIx = explanation.indexOf("\n\n");
    let i = 0;
    let seq = 0;
    const id = window.setInterval(() => {
      if (i >= explanation.length) {
        window.clearInterval(id);
        const end: AiStreamEndPayload = {
          type: "ai_stream_end",
          session_id: DEMO_SESSION,
          message_id: messageId,
          stream_id: streamId,
          node_id: DEMO_NODE,
          seq: seq + 1,
          stream_kind: "llm_resp",
          llm_resp: {
            type: "llm_resp",
            explanation,
            command: "kubectl get pods -n prod",
            dangerous: false,
            warning: "",
            confirm_required: false,
            should_execute: true,
            ai_card_id: "aic_demo",
            auto_executed: false,
          },
        };
        setStreamTextByNodeId((p) => applyOneStreamEvent(p, end));
        return;
      }
      const delta = explanation.slice(i, i + 8);
      const chunk1Len = splitIx < 0 ? explanation.length : splitIx;
      const inFirstPara = i < chunk1Len;
      i += 8;
      seq += 1;
      const d: AiStreamDeltaPayload = {
        type: "ai_stream_delta",
        session_id: DEMO_SESSION,
        message_id: messageId,
        stream_id: streamId,
        node_id: DEMO_NODE,
        seq,
        delta,
        think_chunk: inFirstPara ? 1 : 2,
        thought_type: inFirstPara ? "thought" : "result",
        duration_ms: inFirstPara ? 1050 : 650,
        context:
          !inFirstPara && splitIx >= 0
            ? `上文要点：${explanation.slice(0, Math.min(120, chunk1Len)).trim()}`
            : undefined,
      };
      setStreamTextByNodeId((p) => applyOneStreamEvent(p, d));
    }, 90);

    return () => window.clearInterval(id);
  }, [enabled]);

  return streamTextByNodeId;
}
