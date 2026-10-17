import type { EventEmitter } from "node:events";
import {
  diagnosticError,
  writeDiagnosticEventSync,
} from "@one-memory/runtime-control";
import { stableRuntimeUserMessage } from "./stable-runtime";

interface EarlyStartupErrorHandlerOptions {
  processEvents: Pick<EventEmitter, "on" | "removeListener">;
  dataDir: string;
  stderr: Pick<NodeJS.WriteStream, "write">;
  showError(title: string, message: string): void;
  exit(code: number): void;
  afterDiagnosticWrite?: () => void;
}

const SAFE_ERROR_CODE = /^[a-z][a-z0-9_]{0,127}$/;
const HANDLER = Symbol.for("one-memory.desktop.early-startup-error-handler");
type ProcessWithHandler = EarlyStartupErrorHandlerOptions["processEvents"] & {
  [HANDLER]?: () => void;
};

export function installEarlyStartupErrorHandler(
  options: EarlyStartupErrorHandlerOptions,
): () => void {
  const registry = options.processEvents as ProcessWithHandler;
  registry[HANDLER]?.();
  let handled = false;
  const report = (error: unknown) => {
    if (handled) return;
    handled = true;
    const fields = diagnosticError(error);
    const message =
      error instanceof Error ? error.message : "desktop_startup_failed";
    const errorCode = SAFE_ERROR_CODE.test(message)
      ? message
      : "desktop_startup_failed";
    options.stderr.write(
      `${JSON.stringify({ error_type: fields.error_type, error_code: errorCode, safe_stack: fields.safe_stack ?? [] })}\n`,
    );
    try {
      writeDiagnosticEventSync(
        { dataDir: options.dataDir, service: "desktop" },
        {
          level: "error",
          component: "desktop.lifecycle",
          event: "desktop.early_start_failed",
          outcome: "failed",
          error_code: errorCode,
          ...fields,
          details: { reason: errorCode },
        },
      );
      options.afterDiagnosticWrite?.();
    } finally {
      // The dialog is the only part of this a person ever reads, so it gets the
      // explanation, not the code. The code stays in the diagnostic event,
      // where support can still match it against a known failure.
      options.showError("MemHub 无法启动", stableRuntimeUserMessage(error));
      options.exit(1);
    }
  };
  options.processEvents.on("uncaughtException", report);
  options.processEvents.on("unhandledRejection", report);
  const dispose = () => {
    options.processEvents.removeListener("uncaughtException", report);
    options.processEvents.removeListener("unhandledRejection", report);
    if (registry[HANDLER] === dispose) delete registry[HANDLER];
  };
  registry[HANDLER] = dispose;
  return dispose;
}
