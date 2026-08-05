import * as React from "react";
import { HermesPermissionModal, type HermesPermissionOptionItem } from "@/components/hermes/HermesPermissionModal";
import { cn } from "@/lib/utils";

function hermesWsUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/hermes`;
}

type WsInbound =
  | { type: "hermes.ready"; bridge_version?: string; project_root?: string; hermes_home?: string }
  | { type: "hermes.error"; code?: string; message: string; stderr_tail?: string }
  | { type: "hermes.chunk"; stream_id: string; delta: string }
  | { type: "hermes.acp_note"; text: string }
  | { type: "hermes.permission"; permission_id: string; prompt: string; permission_options: HermesPermissionOptionItem[] }
  | { type: "hermes.session"; hermes_session_id: string; event: string; cwd?: string }
  | { type: "hermes.run_state"; phase: "idle" | "busy"; acp_prompt_inflight: number; bridge_pipeline: number }
  | { type: "hermes.chat_ack"; ok: boolean; client_message_id: string; acp_prompt_inflight: number; bridge_pipeline: number; message?: string }
  | { type: "hermes.chat_queued"; client_message_id: string; detail_zh: string }
  | { type: "pong" };

function isPermissionOptionItem(x: unknown): x is HermesPermissionOptionItem {
  if (!x || typeof x !== "object") return false;
  const o = x as Record<string, unknown>;
  return typeof o.optionId === "string" && o.optionId.length > 0 && typeof o.name === "string";
}

export function HermesChatPanel({ className }: { className?: string }) {
  const [status, setStatus] = React.useState<"idle" | "connecting" | "open" | "error">("idle");
  const [bridgeHint, setBridgeHint] = React.useState<string | null>(null);
  const [runStateHint, setRunStateHint] = React.useState<string | null>(null);
  const [errorText, setErrorText] = React.useState<string | null>(null);
  const [lines, setLines] = React.useState<{ role: "user" | "assistant" | "system"; text: string }[]>([]);
  const [input, setInput] = React.useState("");
  const [perm, setPerm] = React.useState<{
    permissionId: string;
    prompt: string;
    permissionOptions: HermesPermissionOptionItem[];
  } | null>(null);
  const wsRef = React.useRef<WebSocket | null>(null);
  const assistantBufRef = React.useRef<Map<string, string>>(new Map());

  const respondPermission = React.useCallback((optionId: string) => {
    if (!perm || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(
      JSON.stringify({
        v: 1,
        type: "permission.respond",
        permission_id: perm.permissionId,
        option_id: optionId,
      }),
    );
    setPerm(null);
  }, [perm]);

  React.useEffect(() => {
    // 推迟到 macrotask：React 18 开发模式 Strict Mode 会先执行 effect 再立刻 cleanup；
    // 若同步 new WebSocket，会多占一条 /ws/hermes 并多起一个 掌上AI大脑 子进程。clearTimeout 可消掉「假挂载」那一次。
    let ws: WebSocket | null = null;
    let ping: ReturnType<typeof window.setInterval> | undefined;
    const openTimer = window.setTimeout(() => {
      setStatus("connecting");
      setErrorText(null);
      ws = new WebSocket(hermesWsUrl());
      wsRef.current = ws;

      ws.onopen = () => {
        setStatus("open");
      };

      ws.onclose = () => {
        setStatus((s) => (s === "connecting" ? "error" : "idle"));
        wsRef.current = null;
        setPerm(null);
      };

      ws.onerror = () => {
        setErrorText("WebSocket 连接错误（请确认终端服务已启动且已在 data/hermes_bridge.yaml 启用 掌上AI大脑）");
        setStatus("error");
      };

      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(String(ev.data)) as WsInbound;
          if (!data || typeof data !== "object" || !("type" in data)) return;
          switch (data.type) {
            case "hermes.ready": {
              setErrorText(null);
              const v = data.bridge_version ?? "";
              const root = data.project_root ?? "";
              const tail = root.length > 48 ? `…${root.slice(-44)}` : root;
              setBridgeHint([v && `桥 ${v}`, tail && `cwd ${tail}`].filter(Boolean).join(" · "));
              break;
            }
            case "hermes.error":
              setErrorText(data.message + (data.stderr_tail ? `\n${data.stderr_tail}` : ""));
              setStatus("error");
              setPerm(null);
              break;
            case "hermes.permission": {
              const raw = Array.isArray(data.permission_options) ? data.permission_options : [];
              const permissionOptions = raw.filter(isPermissionOptionItem);
              setPerm({
                permissionId: data.permission_id,
                prompt: data.prompt,
                permissionOptions,
              });
              break;
            }
            case "hermes.chunk": {
              const prev = assistantBufRef.current.get(data.stream_id) ?? "";
              assistantBufRef.current.set(data.stream_id, prev + data.delta);
              setLines((old) => {
                const next = [...old];
                const marker = `\u0000stream:${data.stream_id}\n`;
                const body = assistantBufRef.current.get(data.stream_id) ?? "";
                const idx = next.findIndex(
                  (l) => l.role === "assistant" && l.text.startsWith(marker),
                );
                if (idx >= 0) {
                  next[idx] = { role: "assistant", text: marker + body };
                } else {
                  next.push({ role: "assistant", text: marker + body });
                }
                return next;
              });
              break;
            }
            case "hermes.acp_note": {
              const raw = typeof data.text === "string" ? data.text.trim() : "";
              if (!raw) break;
              setLines((old) => [...old, { role: "system", text: raw }]);
              break;
            }
            case "hermes.session":
              break;
            case "hermes.run_state": {
              setRunStateHint(
                `状态 ${data.phase} · ACP 飞行 ${data.acp_prompt_inflight} · 桥内 ${data.bridge_pipeline}`,
              );
              break;
            }
            case "hermes.chat_queued": {
              const qz = typeof data.detail_zh === "string" ? data.detail_zh.trim() : "";
              setLines((old) => [...old, { role: "system", text: `[排队] ${qz || "本条已排队，上一条仍在执行。"}` }]);
              setRunStateHint(qz ? qz.slice(0, 96) : "本条已排队");
              break;
            }
            case "hermes.chat_ack": {
              setRunStateHint(
                `末条确认 ${data.ok ? "ok" : "失败"} · ACP ${data.acp_prompt_inflight} · 桥 ${data.bridge_pipeline}`,
              );
              break;
            }
            case "pong":
              break;
            default:
              break;
          }
        } catch {
          /* ignore */
        }
      };

      ping = window.setInterval(() => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ v: 1, type: "ping" }));
        }
      }, 25000);
    }, 0);

    return () => {
      window.clearTimeout(openTimer);
      if (ping !== undefined) window.clearInterval(ping);
      if (ws) {
        ws.close();
      }
      wsRef.current = null;
      setPerm(null);
    };
  }, []);

  const send = () => {
    const text = input.trim();
    if (!text || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    const mid =
      typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `hcm_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
    wsRef.current.send(JSON.stringify({ v: 1, type: "chat.send", text, client_message_id: mid }));
    setLines((o) => [...o, { role: "user", text }]);
    setInput("");
  };

  const sendCancel = () => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ v: 1, type: "cancel" }));
  };

  return (
    <section
      className={cn(
        "ops-flex ops-h-full ops-w-[420px] ops-shrink-0 ops-flex-col ops-border-l ops-border-gray-200 ops-bg-white",
        className,
      )}
      aria-label="掌上AI大脑"
    >
      <HermesPermissionModal
        open={perm !== null}
        permissionId={perm?.permissionId ?? ""}
        prompt={perm?.prompt ?? ""}
        permissionOptions={perm?.permissionOptions ?? []}
        onSelect={respondPermission}
        onDismiss={() => respondPermission("deny")}
      />

      <header className="ops-flex ops-shrink-0 ops-flex-col ops-gap-2 ops-border-b ops-border-gray-100 ops-px-4 ops-py-3">
        <div className="ops-flex ops-items-center ops-justify-between ops-gap-3">
          <h2 className="ops-text-sm ops-font-semibold ops-text-gray-900">掌上AI大脑</h2>
          <div className="ops-flex ops-items-center ops-gap-2">
            <button
              type="button"
              onClick={sendCancel}
              disabled={status !== "open"}
              title="中断当前生成（ACP session/cancel）"
              className="ops-rounded-md ops-border ops-border-amber-300 ops-bg-amber-50 ops-px-2.5 ops-py-1 ops-text-xs ops-font-medium ops-text-amber-900 hover:ops-bg-amber-100 disabled:ops-cursor-not-allowed disabled:ops-opacity-40"
            >
              取消生成
            </button>
            <span className="ops-rounded-full ops-bg-gray-100 ops-px-2.5 ops-py-0.5 ops-text-xs ops-font-medium ops-text-gray-700">
              {status === "open" ? "已连接" : status === "connecting" ? "连接中" : status === "error" ? "错误" : "未连接"}
            </span>
          </div>
        </div>
        {bridgeHint ? (
          <p className="ops-font-mono ops-text-[10px] ops-leading-snug ops-text-gray-500">{bridgeHint}</p>
        ) : null}
        {runStateHint ? (
          <p className="ops-font-mono ops-text-[10px] ops-leading-snug ops-text-gray-500">{runStateHint}</p>
        ) : null}
      </header>

      <div className="ops-flex ops-min-h-0 ops-flex-1 ops-flex-col ops-gap-2 ops-overflow-hidden ops-p-4">
        {errorText ? (
          <div className="ops-shrink-0 ops-rounded-md ops-border ops-border-red-200 ops-bg-red-50 ops-p-2 ops-text-xs ops-text-red-900 ops-whitespace-pre-wrap">
            {errorText}
          </div>
        ) : null}

        <div className="ops-min-h-0 ops-flex-1 ops-overflow-y-auto ops-rounded-md ops-border ops-border-gray-100 ops-bg-gray-50 ops-p-3 ops-text-sm">
          {lines.length === 0 ? (
            <div className="ops-space-y-3 ops-text-gray-600">
              <p className="ops-text-sm ops-font-medium ops-text-gray-800">演示顺序（约 1 分钟）</p>
              <ol className="ops-list-decimal ops-space-y-1.5 ops-pl-5 ops-text-xs">
                <li>确认本页右上角为「已连接」。</li>
                <li>在下方输入一句简单问题并发送，观察助手流式输出。</li>
                <li>若触发了危险命令审批，在弹窗中选「允许一次」或「拒绝」。</li>
                <li>生成过程中可点「取消生成」验证中断。</li>
              </ol>
              <p className="ops-text-xs ops-text-gray-500">
                若无法连接：请开终端服务（uvicorn 8000）、<code className="ops-rounded ops-bg-gray-200 ops-px-1">data/hermes_bridge.yaml</code>{" "}
                中 <code className="ops-rounded ops-bg-gray-200 ops-px-1">enabled: true</code>，且 掌上AI大脑 已{" "}
                <code className="ops-rounded ops-bg-gray-200 ops-px-1">uv pip install -e &quot;.[acp]&quot;</code>。
              </p>
            </div>
          ) : (
            <ul className="ops-space-y-3">
              {lines.map((l, i) => (
                <li
                  key={i}
                  className={
                    l.role === "user"
                      ? "ops-text-gray-900"
                      : l.role === "system"
                        ? "ops-border-l-2 ops-border-gray-300 ops-pl-2 ops-text-xs ops-text-gray-600"
                        : "ops-text-gray-800"
                  }
                >
                  <span className="ops-font-medium ops-text-gray-500">
                    {l.role === "user" ? "你" : l.role === "system" ? "掌上AI大脑 进度" : "助手"}
                    {": "}
                  </span>
                  <span className="ops-whitespace-pre-wrap">
                    {l.text.replace(/^\u0000stream:[^\n]+\n/, "")}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="ops-flex ops-shrink-0 ops-gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            rows={2}
            placeholder="输入消息后 Enter 发送（Shift+Enter 换行）"
            className="ops-min-h-[52px] ops-flex-1 ops-resize-none ops-rounded-md ops-border ops-border-gray-200 ops-bg-white ops-px-3 ops-py-2 ops-text-sm focus:ops-outline-none focus:ops-ring-2 focus:ops-ring-blue-500"
          />
          <button
            type="button"
            onClick={send}
            disabled={status !== "open"}
            className="ops-self-end ops-rounded-md ops-bg-blue-600 ops-px-4 ops-py-2 ops-text-sm ops-font-medium ops-text-white hover:ops-bg-blue-700 disabled:ops-cursor-not-allowed disabled:ops-opacity-50"
          >
            发送
          </button>
        </div>
      </div>
    </section>
  );
}
