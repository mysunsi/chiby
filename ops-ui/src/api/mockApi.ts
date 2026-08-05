import type { ExecuteRequest, ExecuteResponse } from "./types";

const delay = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** 演示用：勾选后下一次 executeCommand 走失败分支（无需改命令里含 fail） */
let forceNextFailure = false;

export function setMockForceNextFailure(v: boolean) {
  forceNextFailure = v;
}

export async function executeCommand(
  req: ExecuteRequest,
): Promise<ExecuteResponse> {
  await delay(1500);

  const byToggle = forceNextFailure;
  if (forceNextFailure) forceNextFailure = false;
  const fail =
    byToggle || req.command.toLowerCase().includes("fail");

  if (fail) {
    return {
      exitCode: 1,
      stdout: "",
      stderr: "bash: fail: command not found",
      judgment: "failure",
    };
  }

  return {
    exitCode: 0,
    stdout: `Executed: ${req.command}\nOutput here...`,
    stderr: "",
    judgment: "success",
  };
}
