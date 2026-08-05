import * as React from "react";
import {
  ExecutionTimeline,
  MOCK_TIMELINE_NODES,
} from "@/components/Timeline/ExecutionTimeline";
import { cn } from "@/lib/utils";
import type { OpsMachineEvent } from "@/machines/opsMachine";
import { OpsFooter, type OpsSnapshot } from "./OpsFooter";

function CopyIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      xmlns="http://www.w3.org/2000/svg"
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <rect width="14" height="14" x="8" y="8" rx="2" ry="2" />
      <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
    </svg>
  );
}

function badgeFromSnapshot(snapshot: OpsSnapshot): string {
  if (snapshot.matches("pending")) return "待批准";
  if (snapshot.matches("running")) return "运行中";
  if (snapshot.matches("step_confirm")) return "待确认";
  if (snapshot.matches("failed")) return "失败 / 待修复";
  if (snapshot.matches("completed")) return "已完成";
  if (snapshot.matches("aborted")) return "已中止";
  return String(snapshot.value);
}

function isExecuteInvokePending(snapshot: OpsSnapshot): boolean {
  if (!snapshot.matches("running")) return false;
  const ch = snapshot.children;
  if (!ch || typeof ch !== "object") return false;
  for (const ref of Object.values(ch)) {
    if (!ref) continue;
    const snap = ref.getSnapshot?.();
    if (snap?.status === "active") return true;
  }
  return false;
}

export interface OpsPanelProps {
  title?: string;
  snapshot: OpsSnapshot;
  send: (event: OpsMachineEvent) => void;
  commandEditable?: boolean;
  /** 与终端 WS `node_id` 对齐的 AI 流式正文（ExecutionTimeline 节点内展示） */
  aiStreamByNodeId?: Record<string, string>;
  className?: string;
}

export function OpsPanel({
  title = "执行计划",
  snapshot,
  send,
  commandEditable = true,
  aiStreamByNodeId,
  className,
}: OpsPanelProps) {
  const [copied, setCopied] = React.useState(false);
  const cmd = snapshot.context.command;
  const out = snapshot.context.terminalOutput;

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(cmd);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      /* ignore */
    }
  };

  const invokePending = isExecuteInvokePending(snapshot);
  const badge = badgeFromSnapshot(snapshot);

  return (
    <section
      className={cn(
        "ops-flex ops-h-full ops-w-[420px] ops-shrink-0 ops-flex-col ops-border-l ops-border-gray-200 ops-bg-white",
        className,
      )}
      aria-label={title}
    >
      <header className="ops-flex ops-shrink-0 ops-items-center ops-justify-between ops-gap-3 ops-border-b ops-border-gray-100 ops-px-4 ops-py-3">
        <h2 className="ops-text-sm ops-font-semibold ops-text-gray-900">{title}</h2>
        <span className="ops-rounded-full ops-bg-gray-100 ops-px-2.5 ops-py-0.5 ops-text-xs ops-font-medium ops-text-gray-700">
          {badge}
        </span>
      </header>

      <div className="ops-flex ops-min-h-0 ops-flex-1 ops-flex-col ops-gap-3 ops-overflow-hidden ops-p-4">
        <div className="ops-shrink-0">
          <ExecutionTimeline
            nodes={MOCK_TIMELINE_NODES}
            streamTextByNodeId={aiStreamByNodeId}
          />
        </div>

        <div className="ops-shrink-0 ops-rounded-lg ops-border ops-border-gray-200 ops-bg-gray-50">
          <div className="ops-flex ops-items-center ops-justify-between ops-border-b ops-border-gray-200 ops-bg-white ops-px-3 ops-py-2">
            <span className="ops-text-xs ops-font-medium ops-text-gray-500">命令</span>
            <button
              type="button"
              onClick={onCopy}
              className="ops-inline-flex ops-items-center ops-gap-1 ops-rounded-md ops-p-1.5 ops-text-gray-500 hover:ops-bg-gray-100 hover:ops-text-gray-800 focus:ops-outline-none focus:ops-ring-2 focus:ops-ring-green-500 focus:ops-ring-offset-1"
              title={copied ? "已复制" : "复制"}
            >
              <CopyIcon className="ops-h-4 ops-w-4" />
              <span className="ops-sr-only">{copied ? "已复制到剪贴板" : "复制命令"}</span>
            </button>
          </div>
          {commandEditable && snapshot.matches("pending") ? (
            <textarea
              className="ops-box-border ops-min-h-[72px] ops-w-full ops-resize-y ops-border-0 ops-bg-transparent ops-p-3 ops-font-mono ops-text-xs ops-leading-relaxed ops-text-gray-900 ops-outline-none focus:ops-ring-2 focus:ops-ring-inset focus:ops-ring-green-500/40"
              value={cmd}
              onChange={(e) => send({ type: "SET_COMMAND", command: e.target.value })}
              rows={3}
              spellCheck={false}
            />
          ) : (
            <pre className="ops-max-h-40 ops-overflow-auto ops-whitespace-pre-wrap ops-break-all ops-p-3 ops-font-mono ops-text-xs ops-leading-relaxed ops-text-gray-900">
              {cmd}
            </pre>
          )}
        </div>

        <div className="ops-flex ops-min-h-0 ops-flex-1 ops-flex-col ops-overflow-hidden ops-rounded-lg ops-border ops-border-gray-200 ops-bg-gray-100">
          <div className="ops-shrink-0 ops-border-b ops-border-gray-200 ops-bg-gray-50 ops-px-3 ops-py-2 ops-text-xs ops-font-medium ops-text-gray-600">
            终端输出
          </div>
          <pre className="ops-min-h-[120px] ops-flex-1 ops-overflow-auto ops-whitespace-pre-wrap ops-p-3 ops-font-mono ops-text-xs ops-leading-relaxed ops-text-gray-800">
            {out || "（无输出）"}
          </pre>
        </div>
      </div>

      <OpsFooter snapshot={snapshot} send={send} isInvokePending={invokePending} />
    </section>
  );
}
