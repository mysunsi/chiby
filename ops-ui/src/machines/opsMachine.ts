import { assign, fromPromise, setup } from "xstate";
import { executeCommand } from "@/api/mockApi";
import type { ExecuteResponse } from "@/api/types";

export interface OpsMachineContext {
  command: string;
  terminalOutput: string;
  retryCount: number;
  maxRetries: number;
  lastResult: ExecuteResponse | null;
}

export type OpsMachineEvent =
  | { type: "APPROVE_BATCH" }
  | { type: "APPROVE_STEPWISE" }
  | { type: "RETRY" }
  | { type: "ABORT" }
  | { type: "CONTINUE" }
  | { type: "FIX_APPLIED" }
  | { type: "MANUAL_EDIT" }
  | { type: "SET_COMMAND"; command: string };

export type OpsMachineInput = {
  command: string;
};

const executeActor = fromPromise(
  async ({ input }: { input: { command: string } }) => {
    return executeCommand({ command: input.command });
  },
);

export const opsMachine = setup({
  types: {
    context: {} as OpsMachineContext,
    events: {} as OpsMachineEvent,
    input: {} as OpsMachineInput,
  },
  actors: {
    executeCommand: executeActor,
  },
  guards: {
    executionFailed: ({ event }) => {
      const e = event as unknown as { output?: ExecuteResponse };
      return e.output?.judgment === "failure";
    },
  },
}).createMachine({
  id: "opsPlan",
  context: ({ input }) => ({
    command: input.command,
    terminalOutput: "",
    retryCount: 0,
    maxRetries: 3,
    lastResult: null,
  }),
  initial: "pending",
  states: {
    pending: {
      on: {
        SET_COMMAND: {
          actions: assign({
            command: ({ event }) => event.command,
          }),
        },
        APPROVE_BATCH: { target: "running" },
        APPROVE_STEPWISE: { target: "running" },
        ABORT: { target: "aborted" },
      },
    },
    running: {
      invoke: {
        id: "executeCommand",
        src: "executeCommand",
        input: ({ context }) => ({ command: context.command }),
        onDone: [
          {
            guard: "executionFailed",
            target: "failed",
            actions: assign({
              lastResult: ({ event }) => event.output as ExecuteResponse,
              terminalOutput: ({ event }) => {
                const o = event.output as ExecuteResponse;
                return [o.stderr, o.stdout].filter(Boolean).join("\n") || "(无输出)";
              },
            }),
          },
          {
            target: "step_confirm",
            actions: assign({
              lastResult: ({ event }) => event.output as ExecuteResponse,
              terminalOutput: ({ event }) => {
                const o = event.output as ExecuteResponse;
                if (o.stdout) return o.stdout;
                if (o.stderr) return `stderr:\n${o.stderr}`;
                return "（无输出）";
              },
            }),
          },
        ],
        onError: {
          target: "failed",
          actions: assign({
            lastResult: () => null,
            terminalOutput: ({ event }) => {
              const err = (event as unknown as { error?: unknown }).error;
              return err instanceof Error ? err.message : String(err ?? "invoke error");
            },
          }),
        },
      },
      on: {
        ABORT: { target: "aborted" },
      },
    },
    step_confirm: {
      on: {
        CONTINUE: { target: "running" },
        RETRY: {
          target: "running",
          actions: assign({
            retryCount: ({ context }) => context.retryCount + 1,
          }),
        },
        ABORT: { target: "aborted" },
      },
    },
    failed: {
      on: {
        FIX_APPLIED: {
          target: "running",
          actions: assign({
            command: () => "echo mock-fix-ok",
            terminalOutput: () => "",
          }),
        },
        MANUAL_EDIT: {
          target: "pending",
          actions: assign({
            terminalOutput: () => "",
            retryCount: () => 0,
            lastResult: () => null,
          }),
        },
        ABORT: { target: "aborted" },
      },
    },
    completed: { type: "final" },
    aborted: { type: "final" },
  },
});
