import { useMachine } from "@xstate/react";
import { useEffect, useState } from "react";
import { setMockForceNextFailure } from "@/api/mockApi";
import { initOpsPostMessageBridge } from "@/bridge/postMessageApi";
import { HermesChatPanel } from "@/components/hermes/HermesChatPanel";
import { OpsPanel } from "@/components/ops/OpsPanel";
import { RolloutConsole } from "@/components/ops/RolloutConsole";
import { useAiStreamDemo } from "@/hooks/useAiStreamDemo";
import { opsMachine } from "@/machines/opsMachine";

const DEFAULT_CMD = "ip addr show | grep inet";

type AppView = "ops" | "rollout" | "hermes";

export default function App() {
  const [snapshot, send] = useMachine(opsMachine, {
    input: { command: DEFAULT_CMD },
  });
  const [forceFail, setForceFail] = useState(false);
  const [view, setView] = useState<AppView>("ops");
  const aiStreamByNodeId = useAiStreamDemo(view === "ops");

  useEffect(() => {
    return initOpsPostMessageBridge({
      onExecute: (payload) => {
        send({ type: "SET_COMMAND", command: payload.command });
        send({ type: "APPROVE_BATCH" });
      },
    });
  }, [send]);

  const isFinal =
    snapshot.matches("aborted") ||
    snapshot.matches("completed");

  return (
    <div className="ops-flex ops-min-h-screen ops-justify-end ops-bg-gray-100">
      {/* 视图切换标签页 */}
      <div className="ops-fixed ops-top-4 ops-right-[440px] ops-z-10 ops-flex ops-rounded-lg ops-bg-white ops-shadow-md ops-overflow-hidden">
        <button
          onClick={() => setView("ops")}
          className={`ops-px-4 ops-py-2 ops-text-sm ops-font-medium ops-transition-colors ${
            view === "ops"
              ? "ops-bg-blue-600 ops-text-white"
              : "ops-bg-white ops-text-gray-700 hover:ops-bg-gray-100"
          }`}
        >
          命令执行
        </button>
        <button
          onClick={() => setView("rollout")}
          className={`ops-px-4 ops-py-2 ops-text-sm ops-font-medium ops-transition-colors ${
            view === "rollout"
              ? "ops-bg-blue-600 ops-text-white"
              : "ops-bg-white ops-text-gray-700 hover:ops-bg-gray-100"
          }`}
        >
          灰度发布
        </button>
        <button
          onClick={() => setView("hermes")}
          className={`ops-px-4 ops-py-2 ops-text-sm ops-font-medium ops-transition-colors ${
            view === "hermes"
              ? "ops-bg-blue-600 ops-text-white"
              : "ops-bg-white ops-text-gray-700 hover:ops-bg-gray-100"
          }`}
        >
          掌上AI大脑
        </button>
      </div>

      {/* 主内容区 */}
      {view === "ops" ? (
        <OpsPanel snapshot={snapshot} send={send} aiStreamByNodeId={aiStreamByNodeId} />
      ) : view === "rollout" ? (
        <RolloutConsole className="ops-h-full ops-w-[420px] ops-shrink-0 ops-border-l ops-border-gray-200 ops-bg-white" />
      ) : (
        <HermesChatPanel className="ops-h-full" />
      )}

      <div className="ops-fixed ops-bottom-4 ops-left-4 ops-max-w-sm ops-rounded-lg ops-bg-black ops-p-3 ops-text-xs ops-text-white ops-shadow-lg">
        <div className="ops-font-mono">Current State: {String(snapshot.value)}</div>
        <div className="ops-mt-2 ops-text-gray-300">retryCount: {snapshot.context.retryCount}</div>
        <label className="ops-mt-2 ops-flex ops-cursor-pointer ops-items-center ops-gap-2 ops-text-gray-200">
          <input
            type="checkbox"
            checked={forceFail}
            onChange={(e) => {
              const v = e.target.checked;
              setForceFail(v);
              setMockForceNextFailure(v);
            }}
            className="ops-rounded ops-border-gray-500"
          />
          下一次执行模拟失败（也可在命令中含 fail）
        </label>
        {isFinal ? (
          <p className="ops-mt-2 ops-text-amber-200/90">终态（已完成 / 已中止）：刷新页面可重新演示。</p>
        ) : null}
      </div>
    </div>
  );
}
