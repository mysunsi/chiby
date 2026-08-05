import * as React from "react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/** Lifecycle state for a single timeline node (maps to Tailwind styles below). */
export type NodeStatus =
  | "pending"
  | "running"
  | "success"
  | "failed"
  | "skipped";

export interface TimelineNode {
  id: string;
  /** Short title shown in the node chip */
  label: string;
  status: NodeStatus;
  /** Full output / logs for the detail panel */
  logs: string;
  /** When set, renders `<Badge>Retry n</Badge>` on the node */
  retryNumber?: number;
  /**
   * Visual style of the connector segment arriving at this node from the left.
   * Retries use dashed SVG stroke per product spec.
   */
  linkFromPrevious?: "solid" | "dashed";
}

export const MOCK_TIMELINE_NODES: TimelineNode[] = [
  {
    id: "s1",
    label: "Preflight",
    status: "success",
    logs: "[OK] disk space check\n[OK] network reachability\nexit 0",
    linkFromPrevious: "solid",
  },
  {
    id: "node_llm_demo",
    label: "NL→命令",
    status: "running",
    logs: "",
    linkFromPrevious: "solid",
  },
  {
    id: "s2",
    label: "Apply patch",
    status: "failed",
    logs: "error: patch conflicts with local changes\nexit 1",
    linkFromPrevious: "solid",
  },
  {
    id: "s2-r1",
    label: "Apply patch",
    status: "failed",
    retryNumber: 1,
    logs: "retry with --force rejected by policy\nexit 2",
    linkFromPrevious: "dashed",
  },
  {
    id: "s3",
    label: "Post-verify",
    status: "pending",
    logs: "(not started)",
    linkFromPrevious: "dashed",
  },
];

function nodeStatusClasses(status: NodeStatus): string {
  switch (status) {
    case "pending":
      return cn(
        "ops-border-gray-300 ops-bg-gray-50 ops-text-gray-700",
        "hover:ops-bg-gray-100",
      );
    case "running":
      return cn(
        "ops-animate-pulse ops-border-blue-500 ops-bg-blue-50 ops-text-blue-900",
        "hover:ops-bg-blue-100",
      );
    case "success":
      return cn(
        "ops-border-green-500 ops-bg-green-50 ops-text-green-900",
        "hover:ops-bg-green-100",
      );
    case "failed":
      return cn(
        "ops-border-red-500 ops-bg-red-50 ops-text-red-900",
        "hover:ops-bg-red-100",
      );
    case "skipped":
      return cn(
        "ops-border-gray-400 ops-bg-gray-100 ops-text-gray-500 ops-line-through",
        "hover:ops-bg-gray-200",
      );
    default:
      return "";
  }
}

/** Horizontal connector between two nodes (solid or dashed SVG line). */
function TimelineConnector({
  variant,
  height,
}: {
  variant: "solid" | "dashed";
  /** vertical center line offset from top of wrapper */
  height: number;
}) {
  const dash = variant === "dashed" ? "6 5" : undefined;
  return (
    <div
      className="ops-relative ops-flex ops-shrink-0 ops-items-center ops-self-stretch"
      style={{ width: 36, minWidth: 36 }}
      aria-hidden
    >
      <svg
        className="ops-pointer-events-none ops-absolute ops-left-0 ops-text-gray-300"
        style={{ top: height }}
        width={36}
        height={4}
        viewBox="0 0 36 4"
        preserveAspectRatio="none"
      >
        <line
          x1={0}
          y1={2}
          x2={36}
          y2={2}
          stroke="currentColor"
          strokeWidth={2}
          strokeDasharray={dash}
          className={
            variant === "dashed" ? "ops-text-amber-500" : undefined
          }
        />
      </svg>
    </div>
  );
}

export interface ExecutionTimelineProps {
  nodes: TimelineNode[];
  /** Optional live hook — defaults to mock interval that mutates demo state */
  useLiveSimulation?: boolean;
  /** 与 WS `node_id` 对齐：节点内实时展示 AI 流式正文（强耦合） */
  streamTextByNodeId?: Record<string, string>;
  className?: string;
}

export function ExecutionTimeline({
  nodes: initialNodes,
  useLiveSimulation = true,
  streamTextByNodeId,
  className,
}: ExecutionTimelineProps) {
  const [nodes, setNodes] = React.useState<TimelineNode[]>(() =>
    initialNodes.map((n) => ({ ...n })),
  );
  const [selected, setSelected] = React.useState<TimelineNode | null>(null);
  const trackRef = React.useRef<HTMLDivElement>(null);
  /** Mock WebSocket: periodically advance “running” steps and tweak statuses */
  React.useEffect(() => {
    if (!useLiveSimulation) return;

    const id = window.setInterval(() => {
      setNodes((prev) => {
        const next = prev.map((n) => ({ ...n }));
        const runningIx = next.findIndex((n) => n.status === "running");
        if (runningIx >= 0) {
          const coin = Math.random();
          if (coin > 0.65) {
            next[runningIx] = {
              ...next[runningIx],
              status: "success",
              logs:
                next[runningIx].logs +
                "\n[mock tick] completed successfully.\nexit 0",
            };
          } else {
            next[runningIx] = {
              ...next[runningIx],
              logs:
                next[runningIx].logs +
                `\n[mock tick] still running… ${new Date().toLocaleTimeString()}`,
            };
          }
        } else {
          const pendingIx = next.findIndex((n) => n.status === "pending");
          if (pendingIx >= 0 && Math.random() > 0.5) {
            next[pendingIx] = {
              ...next[pendingIx],
              status: "running",
              logs: next[pendingIx].logs + "\n[mock] started",
            };
          }
        }
        return next;
      });
    }, 1400);

    return () => window.clearInterval(id);
  }, [useLiveSimulation]);

  /** Auto-scroll track so the latest activity stays in view */
  React.useEffect(() => {
    const el = trackRef.current;
    if (!el) return;
    el.scrollTo({ left: el.scrollWidth, behavior: "smooth" });
  }, [nodes, streamTextByNodeId]);

  const centerY = 22;

  return (
    <div
      className={cn(
        "ops-relative ops-min-w-0 ops-max-w-full ops-rounded-lg ops-border ops-border-gray-200 ops-bg-white ops-p-3 ops-shadow-sm",
        className,
      )}
    >
      <div className="ops-mb-2 ops-flex ops-items-center ops-justify-between ops-gap-2">
        <h3 className="ops-text-xs ops-font-semibold ops-text-gray-800">
          执行时间线
        </h3>
        <span className="ops-text-[10px] ops-text-gray-400">
          （节点流式文本与终端 WS `ai_stream_*` 协议对齐）
        </span>
      </div>

      {/* Horizontal strip: flex row + overflow for responsiveness */}
      <div
        ref={trackRef}
        className="ops-relative ops-flex ops-min-h-[52px] ops-min-w-0 ops-flex-row ops-items-stretch ops-gap-0 ops-overflow-x-auto ops-overflow-y-visible ops-pb-1 ops-scroll-smooth"
      >
        {nodes.map((node, index) => {
          const link = node.linkFromPrevious ?? "solid";
          const showConnector = index > 0;

          return (
            <React.Fragment key={node.id}>
              {showConnector ? (
                <TimelineConnector variant={link} height={centerY} />
              ) : null}

              <div className="ops-relative ops-flex ops-shrink-0 ops-flex-col ops-items-center">
                <button
                  type="button"
                  onClick={() => setSelected(node)}
                  className={cn(
                    "ops-relative ops-z-10 ops-flex ops-min-h-[44px] ops-min-w-[88px] ops-max-w-[120px] ops-flex-col ops-items-center ops-justify-center ops-gap-1 ops-rounded-lg ops-border-2 ops-px-2 ops-py-1.5 ops-text-center ops-text-[11px] ops-font-medium ops-leading-tight ops-transition-colors focus:ops-outline-none focus:ops-ring-2 focus:ops-ring-blue-400 focus:ops-ring-offset-1",
                    nodeStatusClasses(node.status),
                  )}
                >
                  <span className="ops-line-clamp-2">{node.label}</span>
                  {node.retryNumber != null && node.retryNumber > 0 ? (
                    <Badge variant="retry" className="ops-pointer-events-none">
                      Retry {node.retryNumber}
                    </Badge>
                  ) : null}
                  {streamTextByNodeId?.[node.id] ? (
                    <pre
                      className="ops-mt-1 ops-max-h-16 ops-w-full ops-overflow-auto ops-whitespace-pre-wrap ops-break-all ops-text-left ops-text-[9px] ops-leading-snug ops-text-gray-600"
                      style={{ fontFamily: "ui-monospace, monospace" }}
                    >
                      {streamTextByNodeId[node.id]}
                    </pre>
                  ) : null}
                </button>
              </div>
            </React.Fragment>
          );
        })}
      </div>

      {selected ? (
        <div
          className="ops-fixed ops-inset-0 ops-z-50 ops-flex ops-items-center ops-justify-center ops-bg-black/40 ops-p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="timeline-detail-title"
          onClick={() => setSelected(null)}
        >
          <div
            className="ops-max-h-[70vh] ops-w-full ops-max-w-lg ops-overflow-hidden ops-rounded-xl ops-bg-white ops-shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="ops-flex ops-items-center ops-justify-between ops-border-b ops-border-gray-200 ops-px-4 ops-py-3">
              <h4
                id="timeline-detail-title"
                className="ops-text-sm ops-font-semibold ops-text-gray-900"
              >
                {selected.label}
                {selected.retryNumber ? (
                  <span className="ops-ml-2 ops-text-xs ops-font-normal ops-text-gray-500">
                    · Retry {selected.retryNumber}
                  </span>
                ) : null}
              </h4>
              <button
                type="button"
                className="ops-rounded-md ops-p-1 ops-text-gray-500 hover:ops-bg-gray-100 hover:ops-text-gray-800"
                onClick={() => setSelected(null)}
                aria-label="关闭"
              >
                ✕
              </button>
            </div>
            <div className="ops-max-h-[calc(70vh-52px)] ops-overflow-auto ops-bg-gray-50 ops-px-4 ops-py-3">
              <p className="ops-mb-2 ops-text-[10px] ops-font-medium ops-uppercase ops-tracking-wide ops-text-gray-500">
                状态 · {selected.status}
              </p>
              <pre className="ops-whitespace-pre-wrap ops-break-all ops-font-mono ops-text-xs ops-leading-relaxed ops-text-gray-800">
                {selected.logs || "（无日志）"}
              </pre>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
