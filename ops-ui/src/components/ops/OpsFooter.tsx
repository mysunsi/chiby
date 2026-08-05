import type { SnapshotFrom } from "xstate";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { opsMachine, type OpsMachineEvent } from "@/machines/opsMachine";

export type OpsSnapshot = SnapshotFrom<typeof opsMachine>;

export interface OpsFooterProps {
  snapshot: OpsSnapshot;
  send: (event: OpsMachineEvent) => void;
  isInvokePending?: boolean;
  className?: string;
}

export function OpsFooter({
  snapshot,
  send,
  isInvokePending,
  className,
}: OpsFooterProps) {
  const footerClass = cn(
    "ops-flex ops-items-center ops-justify-between ops-border-t ops-border-gray-200 ops-bg-white ops-px-4 ops-py-3",
    className,
  );

  if (snapshot.matches("pending")) {
    return (
      <footer className={footerClass}>
        <div className="ops-flex ops-flex-wrap ops-gap-2">
          <Button
            variant="primary"
            size="lg"
            type="button"
            isLoading={isInvokePending}
            onClick={() => send({ type: "APPROVE_BATCH" })}
          >
            一键执行
          </Button>
          <Button
            variant="secondary"
            size="lg"
            type="button"
            disabled={isInvokePending}
            onClick={() => send({ type: "APPROVE_STEPWISE" })}
          >
            逐步执行
          </Button>
        </div>
        <Button variant="ghost" size="md" type="button" onClick={() => send({ type: "ABORT" })}>
          取消
        </Button>
      </footer>
    );
  }

  if (snapshot.matches("step_confirm")) {
    return (
      <footer className={footerClass}>
        <div className="ops-flex ops-flex-wrap ops-gap-2">
          <Button variant="primary" size="md" type="button" onClick={() => send({ type: "CONTINUE" })}>
            确认无误，继续
          </Button>
          <Button variant="secondary" size="md" type="button" onClick={() => send({ type: "RETRY" })}>
            重试本步
          </Button>
        </div>
        <Button variant="danger" size="md" type="button" onClick={() => send({ type: "ABORT" })}>
          中止计划
        </Button>
      </footer>
    );
  }

  if (snapshot.matches("failed")) {
    return (
      <footer className={cn(footerClass, "ops-justify-start")}>
        <div className="ops-flex ops-flex-wrap ops-gap-2">
          <Button variant="warning" size="md" type="button" onClick={() => send({ type: "FIX_APPLIED" })}>
            采纳并执行
          </Button>
          <Button variant="secondary" size="md" type="button" onClick={() => send({ type: "MANUAL_EDIT" })}>
            手动修改
          </Button>
        </div>
      </footer>
    );
  }

  if (snapshot.matches("running")) {
    return (
      <footer className={cn(footerClass, "ops-flex-wrap ops-gap-2")}>
        <p className="ops-w-full ops-text-center ops-text-xs ops-text-gray-500">
          {isInvokePending ? "执行中…（Mock 约 1.5s）" : "运行中…"}
        </p>
        <div className="ops-flex ops-w-full ops-justify-center">
          <Button variant="ghost" size="md" type="button" onClick={() => send({ type: "ABORT" })}>
            中止计划
          </Button>
        </div>
      </footer>
    );
  }

  return null;
}
