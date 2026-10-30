import type { ProgressDocument } from "./source-progress-report.js";
import type { StatusDocument } from "./status-report.js";
import { PRODUCT } from "./version.js";

type CheckState = "pass" | "warn" | "fail" | "not_checked";

export interface DoctorCheck {
  id:
    | "core"
    | "broker"
    | "source_permissions"
    | "sync"
    | "model"
    | "pending_operations";
  state: CheckState;
  code: string;
  details?: Record<string, number | string | boolean>;
}

export interface DoctorDocument {
  schema_version: 1;
  product: string;
  state: "healthy" | "attention" | "unavailable";
  data_dir: string;
  checks: DoctorCheck[];
}

export function composeDoctorReport(
  status: StatusDocument,
  progress: ProgressDocument | null,
): DoctorDocument {
  const checks = [
    coreCheck(status),
    brokerCheck(status),
    sourceCheck(status),
    syncCheck(status),
    modelCheck(status),
    pendingCheck(progress),
  ];
  const core = checks[0];
  return {
    schema_version: 1,
    product: PRODUCT,
    state:
      core.state === "fail" || core.state === "not_checked"
        ? "unavailable"
        : checks.every((check) => check.state === "pass")
          ? "healthy"
          : "attention",
    data_dir: status.data_dir,
    checks,
  };
}

export function doctorExitCode(document: DoctorDocument): number {
  return document.state === "healthy" ? 0 : 1;
}

export function renderDoctorText(document: DoctorDocument): string {
  const labels: Record<DoctorCheck["id"], string> = {
    core: "Core",
    broker: "Broker",
    source_permissions: "来源权限",
    sync: "来源同步",
    model: "模型",
    pending_operations: "待处理操作",
  };
  const migration = document.checks.find(
    (check) => check.id === "core" && check.details?.log_file,
  );
  return [
    `MemHub 检查：${doctorState(document.state)}`,
    ...document.checks.map(
      (check) =>
        `${symbol(check.state)} ${labels[check.id]}：${checkState(check.state)}`,
    ),
    ...(document.state === "healthy"
      ? []
      : migration
        ? [
            `升级迁移没有完成（错误码：${migration.code}），MemHub 不会启动。`,
            `下一步：把迁移日志附在 GitHub 反馈里发给我们：${migration.details?.log_file}`,
            `反馈地址：${migration.details?.issues_url}`,
          ]
        : ["下一步：打开 MemHub 的“帮助与诊断”查看并处理异常。"]),
    "",
  ].join("\n");
}

function doctorState(state: DoctorDocument["state"]): string {
  if (state === "healthy") return "一切正常";
  if (state === "attention") return "有项目需要处理";
  return "暂时无法完成检查";
}

function checkState(state: CheckState): string {
  if (state === "pass") return "正常";
  if (state === "warn") return "需要处理";
  if (state === "fail") return "异常";
  return "未检查";
}

function coreCheck(status: StatusDocument): DoctorCheck {
  if (status.runtime.state === "running") {
    return {
      id: "core",
      state: "pass",
      code: status.runtime.code,
      details: {
        version: status.runtime.version ?? "unknown",
        instance_verified: Boolean(status.runtime.instance_id),
      },
    };
  }
  return {
    id: "core",
    state: status.runtime.state === "stopped" ? "warn" : "fail",
    code: status.runtime.code,
    ...(status.runtime.log_file
      ? {
          details: {
            log_file: status.runtime.log_file,
            issues_url: status.runtime.issues_url ?? "",
          },
        }
      : {}),
  };
}

function brokerCheck(status: StatusDocument): DoctorCheck {
  if (status.bridge.state === "healthy") {
    return { id: "broker", state: "pass", code: status.bridge.code };
  }
  if (
    status.bridge.state === "not_checked" ||
    status.bridge.state === "unknown"
  ) {
    return {
      id: "broker",
      state: "not_checked",
      code: status.bridge.code,
    };
  }
  return {
    id: "broker",
    state: status.bridge.state === "untrusted" ? "fail" : "warn",
    code: status.bridge.code,
  };
}

function sourceCheck(status: StatusDocument): DoctorCheck {
  const section = status.sections.sources;
  if (section?.state !== "ok") {
    return unread("source_permissions", section);
  }
  const total = number(section.total);
  const available = number(section.available);
  const authorized = number(section.authorized);
  return {
    id: "source_permissions",
    state: available > authorized ? "warn" : "pass",
    code:
      available > authorized
        ? "sources_need_authorization"
        : total === 0
          ? "no_sources_registered"
          : "source_permissions_ok",
    details: { total, available, authorized },
  };
}

function syncCheck(status: StatusDocument): DoctorCheck {
  const section = status.sections.sync;
  if (section?.state !== "ok") return unread("sync", section);
  const degraded = number(section.degraded);
  const unavailable = number(section.unavailable);
  return {
    id: "sync",
    state: degraded + unavailable > 0 ? "warn" : "pass",
    code: degraded + unavailable > 0 ? "sync_sources_degraded" : "sync_healthy",
    details: {
      running: number(section.running),
      idle: number(section.idle),
      degraded,
      unavailable,
    },
  };
}

function modelCheck(status: StatusDocument): DoctorCheck {
  const section = status.sections.model;
  if (section?.state !== "ok") return unread("model", section);
  const profiles = number(section.profiles);
  const active = record(section.active);
  if (!active) {
    return {
      id: "model",
      state: "warn",
      code: profiles === 0 ? "model_not_configured" : "model_not_active",
      details: { profiles },
    };
  }
  const credential = text(active.credential_state);
  const available = credential === "available";
  return {
    id: "model",
    state: available ? "pass" : "warn",
    code: available ? "model_ready" : "model_credential_unavailable",
    details: {
      profiles,
      active: true,
      credential_state: credential ?? "unknown",
    },
  };
}

function pendingCheck(progress: ProgressDocument | null): DoctorCheck {
  if (
    progress?.imports.state !== "ok" ||
    progress.generation.summary_state.state !== "ok"
  ) {
    return {
      id: "pending_operations",
      state: "not_checked",
      code: "pending_operations_not_checked",
    };
  }
  const jobAttention = progress.imports.jobs.filter((item) =>
    ["paused", "interrupted", "failed"].includes(text(item.status) ?? ""),
  ).length;
  const summary = progress.generation.summary;
  const waiting = summary ? number(summary.waiting_for_model) : 0;
  const failed = summary ? number(summary.failed) : 0;
  const attention = jobAttention + waiting + failed;
  return {
    id: "pending_operations",
    state: attention > 0 ? "warn" : "pass",
    code: attention > 0 ? "operations_need_attention" : "pending_operations_ok",
    details: {
      import_jobs_validated: progress.imports.validated,
      jobs_need_attention: jobAttention,
      waiting_for_model: waiting,
      failed,
      output_truncated: progress.imports.output_truncated,
    },
  };
}

function unread(
  id: DoctorCheck["id"],
  section: { state: string; code?: string } | undefined,
): DoctorCheck {
  return {
    id,
    state: "not_checked",
    code: section?.code ?? `${id}_not_checked`,
  };
}

function number(value: unknown): number {
  return Number.isSafeInteger(value) && (value as number) >= 0
    ? (value as number)
    : 0;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.length <= 128 ? value : null;
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function symbol(state: CheckState): string {
  if (state === "pass") return "✓";
  if (state === "warn") return "!";
  if (state === "fail") return "✗";
  return "?";
}
