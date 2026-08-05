import * as React from "react";
import type { TerminalContextPayload } from "@/bridge/postMessageApi";
import {
  postOpsFillTerminalComposer,
  postOpsInjectToTerminal,
} from "@/bridge/postMessageApi";
import { cn } from "@/lib/utils";
import type { OpsSnapshot } from "@/components/ops/OpsFooter";

export interface TerminalContextStripProps {
  ctx: TerminalContextPayload | null;
  snapshot: OpsSnapshot;
  onAppendWorkOrder: (text: string) => void;
  className?: string;
}

function truncate(s: string, n: number) {
  const t = s.trim();
  if (t.length <= n) return t;
  return t.slice(0, n) + "…";
}

/**
 * 终端选中输出 → 一键联动 NL 侧 / 注入命令 / 工单摘要
 */
export function TerminalContextStrip({
  ctx,
  snapshot,
  onAppendWorkOrder,
  className,
}: TerminalContextStripProps) {
  const cmd = snapshot.context.command.trim();
  const text = ctx?.text?.trim() ?? "";

  const canInject = Boolean(cmd);

  return (
    <div
      className={cn(
        "ops-rounded-lg ops-border ops-border-dashed ops-border-blue-200 ops-bg-blue-50/80 ops-px-3 ops-py-2 ops-text-xs",
        className,
      )}
    >
      <div className="ops-mb-2 ops-flex ops-items-start ops-justify-between ops-gap-2">
        <span className="ops-font-semibold ops-text-blue-900">终端双向上下文</span>
        {ctx ? (
          <span className="ops-shrink-0 ops-text-[10px] ops-text-blue-700">
            session {truncate(ctx.sessionId, 12)}
          </span>
        ) : (
          <span className="ops-text-[10px] ops-text-gray-500">在左侧终端拖选输出…</span>
        )}
      </div>
      {ctx ? (
        <pre className="ops-mb-2 ops-max-h-24 ops-overflow-auto ops-whitespace-pre-wrap ops-break-all ops-rounded ops-bg-white/90 ops-p-2 ops-font-mono ops-text-[11px] ops-text-gray-800">
          {truncate(text, 4000)}
        </pre>
      ) : (
        <p className="ops-mb-2 ops-text-[11px] ops-leading-snug ops-text-gray-600">
          选中终端缓冲中的任意行或段落，将自动同步到这里，供右侧一键解释或回填工单。
        </p>
      )}
      <div className="ops-flex ops-flex-wrap ops-gap-1.5">
        <button
          type="button"
          disabled={!text}
          className="ops-rounded ops-bg-white ops-px-2 ops-py-1 ops-text-[11px] ops-font-medium ops-text-gray-800 ops-shadow-sm hover:ops-bg-blue-100 disabled:ops-opacity-40"
          onClick={() =>
            postOpsFillTerminalComposer({
              text: `请用运维视角解释以下终端输出（可能的错误码、风险与下一步建议）：\n\n${text}`,
              mode: "replace",
            })
          }
        >
          解释
        </button>
        <button
          type="button"
          disabled={!text}
          className="ops-rounded ops-bg-white ops-px-2 ops-py-1 ops-text-[11px] ops-font-medium ops-text-gray-800 ops-shadow-sm hover:ops-bg-blue-100 disabled:ops-opacity-40"
          onClick={() =>
            postOpsFillTerminalComposer({
              text: `根据以下终端输出，生成一份分步修复计划（含验证命令）：\n\n${text}`,
              mode: "replace",
            })
          }
        >
          生成修复计划
        </button>
        <button
          type="button"
          disabled={!text}
          className="ops-rounded ops-bg-white ops-px-2 ops-py-1 ops-text-[11px] ops-font-medium ops-text-gray-800 ops-shadow-sm hover:ops-bg-amber-100 disabled:ops-opacity-40"
          onClick={() => onAppendWorkOrder(text)}
        >
          加入工单摘要
        </button>
        <button
          type="button"
          disabled={!canInject}
          title="将上方「命令」面板中的命令经父页 WebSocket 下发（走策略网关）"
          className="ops-rounded ops-bg-blue-600 ops-px-2 ops-py-1 ops-text-[11px] ops-font-medium ops-text-white hover:ops-bg-blue-700 disabled:ops-opacity-40"
          onClick={() =>
            postOpsInjectToTerminal({
              command: cmd,
              mode: "exec_ws",
            })
          }
        >
          注入当前会话
        </button>
      </div>
    </div>
  );
}
