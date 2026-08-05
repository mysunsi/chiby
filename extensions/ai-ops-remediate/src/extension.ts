/**
 * AI Ops — 监听集成终端输出，匹配常见错误后调用 /api/v1/remediate。
 */
import * as vscode from "vscode";
import axios, { AxiosError } from "axios";

/** 与后端 RemediateRequest 对齐 */
interface RemediateBody {
  command: string;
  stderr: string;
  stdout: string;
  return_code: number;
  environment_id: string;
  cwd: string;
  confirm_high_risk: boolean;
}

interface RemediateResponse {
  status: string;
  original_command?: string;
  fixed_command?: string | null;
  root_cause?: string | null;
  risk_level?: string | null;
  confidence_score?: number | null;
  message?: string | null;
}

const ERROR_PATTERNS = [
  /Permission denied/i,
  /command not found/i,
  /No such file or directory/i,
];

/** 终端实例 → 最近一次 Shell Integration 上报的命令行 */
const lastCommandByTerminal = new WeakMap<vscode.Terminal, string>();

/** 终端输出环形缓冲（尾部文本，用于 stderr 上下文） */
const tailOutputByTerminal = new WeakMap<vscode.Terminal, string>();

/** 防抖：终端 → 上次触发时间 */
const lastTriggerMs = new WeakMap<vscode.Terminal, number>();

function getConfig(): {
  baseUrl: string;
  apiKey: string;
  enabled: boolean;
  debounceMs: number;
} {
  const conf = vscode.workspace.getConfiguration("aiOps");
  return {
    baseUrl: conf.get<string>("baseUrl", "http://localhost:8000").replace(/\/$/, ""),
    apiKey: conf.get<string>("apiKey", "YOUR_SECRET_API_KEY"),
    enabled: conf.get<boolean>("enabled", true),
    debounceMs: conf.get<number>("debounceMs", 45000),
  };
}

function defaultCwd(): string {
  const folders = vscode.workspace.workspaceFolders;
  if (folders && folders.length > 0) {
    return folders[0].uri.fsPath;
  }
  return ".";
}

function appendTail(terminal: vscode.Terminal, chunk: string): string {
  const prev = tailOutputByTerminal.get(terminal) ?? "";
  let next = prev + chunk;
  const max = 12000;
  if (next.length > max) {
    next = next.slice(-max);
  }
  tailOutputByTerminal.set(terminal, next);
  return next;
}

function tailLooksLikeError(text: string): boolean {
  return ERROR_PATTERNS.some((re) => re.test(text));
}

async function callRemediate(
  terminal: vscode.Terminal,
  stderrContext: string
): Promise<void> {
  const { baseUrl, apiKey } = getConfig();
  let cmd = lastCommandByTerminal.get(terminal)?.trim() ?? "";
  if (!cmd) {
    cmd =
      "(unknown — 请在集成终端执行命令，并启用 Shell Integration 以捕获命令行；亦可手动修改请求)";
  }

  const body: RemediateBody = {
    command: cmd.startsWith("(unknown") ? "(unknown)" : cmd,
    stderr: stderrContext.slice(-8000),
    stdout: "",
    return_code: 1,
    environment_id: "vscode",
    cwd: defaultCwd(),
    confirm_high_risk: false,
  };

  try {
    const res = await axios.post<RemediateResponse>(
      `${baseUrl}/api/v1/remediate`,
      body,
      {
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": apiKey,
        },
        timeout: 180_000,
        validateStatus: () => true,
      }
    );

    if (res.status === 403) {
      vscode.window.showErrorMessage("AI Ops: API Key 无效（403），请检查 aiOps.apiKey。");
      return;
    }
    if (res.status >= 400) {
      vscode.window.showErrorMessage(
        `AI Ops: HTTP ${res.status} ${typeof res.data === "object" ? JSON.stringify(res.data) : res.statusText}`
      );
      return;
    }

    const data = res.data;
    const fixed =
      data.fixed_command &&
      String(data.fixed_command).trim().length > 0
        ? String(data.fixed_command).trim()
        : null;

    if (!fixed) {
      vscode.window.showWarningMessage(
        `AI Ops: 未返回 fixed_command（status=${data.status}）${data.message ? " — " + data.message : ""}`
      );
      return;
    }

    const label = `🔧 AI Fix: ${fixed.length > 120 ? fixed.slice(0, 117) + "…" : fixed}`;
    const run = "在终端执行";
    const copy = "复制命令";

    const choice = await vscode.window.showInformationMessage(label, { modal: false }, run, copy);

    if (choice === run) {
      terminal.show(true);
      terminal.sendText(fixed, true);
    } else if (choice === copy) {
      await vscode.env.clipboard.writeText(fixed);
      vscode.window.showInformationMessage("已复制修复命令到剪贴板");
    }
  } catch (e) {
    const ax = e as AxiosError;
    const msg = ax.message || String(e);
    vscode.window.showErrorMessage(`AI Ops 请求失败: ${msg}`);
  }
}

function maybeTrigger(terminal: vscode.Terminal, fullTail: string): void {
  const { enabled, debounceMs } = getConfig();
  if (!enabled) {
    return;
  }
  if (!tailLooksLikeError(fullTail)) {
    return;
  }

  const now = Date.now();
  const last = lastTriggerMs.get(terminal) ?? 0;
  if (now - last < debounceMs) {
    return;
  }
  lastTriggerMs.set(terminal, now);

  const lines = fullTail.split(/\r?\n/).filter(Boolean);
  const matchedLine =
    [...lines].reverse().find((ln) => ERROR_PATTERNS.some((re) => re.test(ln))) ||
    lines.slice(-5).join("\n");

  void callRemediate(terminal, matchedLine || fullTail.slice(-2000));
}

export function activate(context: vscode.ExtensionContext): void {
  const win = vscode.window as vscode.Window & {
    onDidWriteTerminalData?: (
      listener: (e: { terminal: vscode.Terminal; data: string }) => void
    ) => vscode.Disposable;
    onDidEndTerminalShellExecution?: (
      listener: (e: {
        terminal: vscode.Terminal;
        execution: { commandLine?: { value: string } | string };
      }) => void
    ) => vscode.Disposable;
  };

  if (typeof win.onDidEndTerminalShellExecution === "function") {
    context.subscriptions.push(
      win.onDidEndTerminalShellExecution((e) => {
        const line = e.execution.commandLine;
        const val =
          typeof line === "string"
            ? line
            : line && typeof line === "object" && "value" in line
              ? (line as { value: string }).value
              : "";
        if (val && e.terminal) {
          lastCommandByTerminal.set(e.terminal, val);
        }
      })
    );
  }

  if (typeof win.onDidWriteTerminalData === "function") {
    context.subscriptions.push(
      win.onDidWriteTerminalData((e) => {
        const tail = appendTail(e.terminal, e.data);
        maybeTrigger(e.terminal, tail);
      })
    );
  } else {
    vscode.window.showWarningMessage(
      "AI Ops: 当前 VS Code 版本不支持 onDidWriteTerminalData，无法自动监听终端输出；可使用命令「AI Ops: 使用当前缓冲发起一次自愈请求」。"
    );
  }

  context.subscriptions.push(
    vscode.commands.registerCommand("aiOps.remediateLastError", async () => {
      const term = vscode.window.activeTerminal;
      if (!term) {
        vscode.window.showWarningMessage("没有活动终端");
        return;
      }
      const tail = tailOutputByTerminal.get(term) ?? "";
      if (!tail) {
        vscode.window.showWarningMessage("尚无缓冲的终端输出；请先在本终端执行命令产生输出。");
        return;
      }
      await callRemediate(term, tail.slice(-8000));
    })
  );
}

export function deactivate(): void {}
