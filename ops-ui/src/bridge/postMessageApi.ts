/**
 * 终端（父窗口）↔ Ops UI（iframe）窄协议 v1
 *
 * 父 → 子：
 *   - EXECUTE：种子命令（演示流 / Rollout 预填）
 *   - OPS_TERMINAL_CONTEXT：终端选中输出等上下文
 *
 * 子 → 父：
 *   - OPS_UI_READY：iframe 就绪
 *   - OPS_INJECT_COMMAND：将已验证命令下发当前 PTY（经 WS exec）
 *   - OPS_FILL_COMPOSER：填充终端页右侧 NL 输入框
 */

export const OPS_BRIDGE_PROTOCOL_V = 1 as const;

export type ExecutePayload = {
  command: string;
  sessionId?: string;
};

export type ParentToOpsExecuteMessage = {
  type: "EXECUTE";
  payload: ExecutePayload;
};

/** 终端选中一段输出 → 右侧面板消费 */
export type TerminalContextPayload = {
  sessionId: string;
  kind: "selection";
  text: string;
  ts?: number;
};

export type ParentToOpsTerminalContextMessage = {
  type: "OPS_TERMINAL_CONTEXT";
  source: "terminal-web";
  v: typeof OPS_BRIDGE_PROTOCOL_V;
  payload: TerminalContextPayload;
};

export type ParentToOpsMessage = ParentToOpsExecuteMessage | ParentToOpsTerminalContextMessage;

export type InjectCommandPayload = {
  command: string;
  sessionId?: string;
  /** exec_ws：WebSocket type=exec（经网关）；input_line：模拟键盘输入 */
  mode?: "exec_ws" | "input_line";
};

export type OpsToParentInjectMessage = {
  type: "OPS_INJECT_COMMAND";
  source: "ops-ui";
  v: typeof OPS_BRIDGE_PROTOCOL_V;
  payload: InjectCommandPayload;
};

export type FillComposerPayload = {
  text: string;
  mode?: "append" | "replace";
};

export type OpsToParentFillComposerMessage = {
  type: "OPS_FILL_COMPOSER";
  source: "ops-ui";
  v: typeof OPS_BRIDGE_PROTOCOL_V;
  payload: FillComposerPayload;
};

export type OpsToParentReadyMessage = {
  type: "OPS_UI_READY";
  source: "ops-ui";
  v: number;
};

export type OpsToParentMessage =
  | OpsToParentInjectMessage
  | OpsToParentFillComposerMessage
  | OpsToParentReadyMessage;

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

export function isExecuteMessage(data: unknown): data is ParentToOpsExecuteMessage {
  if (!isRecord(data) || data.type !== "EXECUTE") return false;
  const p = data.payload;
  if (!isRecord(p)) return false;
  return typeof p.command === "string";
}

export function isTerminalContextMessage(data: unknown): data is ParentToOpsTerminalContextMessage {
  if (!isRecord(data) || data.type !== "OPS_TERMINAL_CONTEXT") return false;
  if (data.source !== "terminal-web") return false;
  if (data.v !== OPS_BRIDGE_PROTOCOL_V) return false;
  const p = data.payload;
  if (!isRecord(p)) return false;
  return (
    typeof p.sessionId === "string" &&
    p.kind === "selection" &&
    typeof p.text === "string"
  );
}

export type PostMessageBridgeOptions = {
  onExecute: (payload: ExecutePayload) => void;
  onTerminalContext?: (payload: TerminalContextPayload) => void;
  /**
   * 校验 message 来源；默认开发环境放行同源，生产应收紧。
   */
  isOriginAllowed?: (origin: string) => boolean;
};

const defaultOriginAllow: (origin: string) => boolean = (origin) => {
  if (import.meta.env.DEV) return true;
  try {
    if (document.referrer) {
      const po = new URL(document.referrer).origin;
      if (origin === po) return true;
    }
  } catch {
    /* noop */
  }
  try {
    return origin === window.location.origin || origin === "null";
  } catch {
    return false;
  }
};

/** 子窗口向父窗口告知已就绪（可选） */
export function postOpsChildReady() {
  try {
    if (window.parent && window.parent !== window) {
      const target = resolveParentPostMessageTarget();
      window.parent.postMessage(
        { type: "OPS_UI_READY", source: "ops-ui", v: OPS_BRIDGE_PROTOCOL_V } satisfies OpsToParentReadyMessage,
        target,
      );
    }
  } catch {
    /* cross-origin parent */
  }
}

/** 嵌入终端页时父窗口 origin（referrer）；独立打开时退回自身 origin */
export function resolveParentPostMessageTarget(): string {
  try {
    if (document.referrer) {
      return new URL(document.referrer).origin;
    }
  } catch {
    /* noop */
  }
  return window.location.origin;
}

/** 将审批过的命令发回终端父页执行（WebSocket exec 或 PTY 输入） */
export function postOpsInjectToTerminal(payload: InjectCommandPayload) {
  try {
    if (!window.parent || window.parent === window) return;
    const msg: OpsToParentInjectMessage = {
      type: "OPS_INJECT_COMMAND",
      source: "ops-ui",
      v: OPS_BRIDGE_PROTOCOL_V,
      payload,
    };
    window.parent.postMessage(msg, resolveParentPostMessageTarget());
  } catch {
    /* noop */
  }
}

/** 填充终端页「自然语言」输入框，联动左侧 Agent */
export function postOpsFillTerminalComposer(payload: FillComposerPayload) {
  try {
    if (!window.parent || window.parent === window) return;
    const msg: OpsToParentFillComposerMessage = {
      type: "OPS_FILL_COMPOSER",
      source: "ops-ui",
      v: OPS_BRIDGE_PROTOCOL_V,
      payload,
    };
    window.parent.postMessage(msg, resolveParentPostMessageTarget());
  } catch {
    /* noop */
  }
}

export function initOpsPostMessageBridge(opts: PostMessageBridgeOptions): () => void {
  const allow = opts.isOriginAllowed ?? defaultOriginAllow;

  const onMessage = (ev: MessageEvent) => {
    if (!allow(ev.origin)) return;
    const data = ev.data;
    if (isExecuteMessage(data)) {
      opts.onExecute(data.payload);
      return;
    }
    if (isTerminalContextMessage(data)) {
      opts.onTerminalContext?.(data.payload);
    }
  };

  window.addEventListener("message", onMessage);
  postOpsChildReady();

  return () => window.removeEventListener("message", onMessage);
}
