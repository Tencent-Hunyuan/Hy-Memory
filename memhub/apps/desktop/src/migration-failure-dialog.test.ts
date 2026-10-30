import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";
import {
  displayPath,
  MAX_ISSUE_URL_LENGTH,
  MIGRATION_ISSUE_URL,
  type MigrationDialogDependencies,
  migrationFailureCode,
  migrationIssueBody,
  migrationIssueUrl,
  migrationLogPath,
  readMigrationLogFacts,
  showMigrationFailureDialog,
} from "./migration-failure-dialog";

async function home(): Promise<string> {
  return mkdtemp(path.join(os.tmpdir(), "memhub-migration-dialog-"));
}

function dependencies(choice: number) {
  return {
    showMessageBoxSync: vi.fn(
      (
        _options: Parameters<
          MigrationDialogDependencies["showMessageBoxSync"]
        >[0],
      ) => choice,
    ),
    showItemInFolder: vi.fn(),
    openExternal: vi.fn(async () => undefined),
  };
}

describe("migration failure dialog", () => {
  it("names the code, reveals the log and opens a prefilled GitHub issue", async () => {
    const dataDir = await home();
    const deps = dependencies(0);

    const handled = showMigrationFailureDialog({
      error: new Error("sdk_migration_preflight_failed"),
      dataDir,
      locale: "zh-CN",
      dependencies: deps,
    });

    expect(handled).toBe(true);
    const options = deps.showMessageBoxSync.mock.calls[0]?.[0];
    expect(options).toMatchObject({
      type: "error",
      message: "MemHub 升级时数据迁移没有完成。",
      buttons: ["打开日志", "退出"],
      defaultId: 0,
      cancelId: 1,
    });
    expect(options?.detail).toContain(
      "为保护你的数据，MemHub 不会启动。请把迁移日志附在 GitHub 反馈里发给我们。（错误码：sdk_migration_preflight_failed）",
    );
    expect(options?.detail).toContain("sdk-migration.log");
    expect(deps.showItemInFolder).toHaveBeenCalledWith(
      migrationLogPath(dataDir),
    );
    const opened = new URL(
      String((deps.openExternal.mock.calls[0] as unknown[] | undefined)?.[0]),
    );
    expect(`${opened.origin}${opened.pathname}`).toBe(MIGRATION_ISSUE_URL);
    expect(opened.searchParams.get("title")).toBe(
      "[升级迁移失败 / migration failed] sdk_migration_preflight_failed",
    );
    expect(opened.searchParams.get("body")).toContain(
      "- 错误码：sdk_migration_preflight_failed",
    );
    expect(migrationIssueUrl("sdk_migration_preflight_failed")).toMatch(
      new RegExp(
        `^${MIGRATION_ISSUE_URL.replaceAll(".", "\\.")}\\?title=.*sdk_migration_preflight_failed$`,
      ),
    );
    // The file the dialog points at exists and ends with this failure.
    const last = (await readFile(migrationLogPath(dataDir), "utf8"))
      .trim()
      .split("\n")
      .at(-1);
    expect(JSON.parse(last ?? "{}")).toMatchObject({
      writer: "desktop",
      event: "desktop.migration_failure_shown",
      code: "sdk_migration_preflight_failed",
    });
  });

  it("quits without opening anything when the person chooses quit", async () => {
    const deps = dependencies(1);
    expect(
      showMigrationFailureDialog({
        error: new Error("sdk_migration_required"),
        dataDir: await home(),
        locale: "en-US",
        dependencies: deps,
      }),
    ).toBe(true);
    expect(deps.showMessageBoxSync.mock.calls[0]?.[0]?.buttons).toEqual([
      "Open log",
      "Quit",
    ]);
    expect(deps.showItemInFolder).not.toHaveBeenCalled();
    expect(deps.openExternal).not.toHaveBeenCalled();
  });

  it("uses the block note the service left when Desktop only saw 'not ready'", async () => {
    const dataDir = await home();
    await writeFile(
      path.join(dataDir, "sdk-migration-blocked.json"),
      JSON.stringify({
        schema_version: 1,
        code: "sdk_migration_model_profile_required",
        caller: "runtime-service",
        at: "2026-10-26T00:00:00.000Z",
      }),
    );
    expect(
      migrationFailureCode(new Error("runtime_service_not_ready"), dataDir),
    ).toBe("sdk_migration_model_profile_required");
    // A different, specific failure is not relabelled as a migration.
    expect(
      migrationFailureCode(new Error("installed_version_conflict"), dataDir),
    ).toBeNull();
  });

  it("fills the issue body from the migration log, never naming the user", async () => {
    const accountHome = await home();
    const dataDir = path.join(accountHome, ".one-memory");
    await mkdir(path.join(dataDir, "logs"), { recursive: true });
    const log = [
      {
        event: "migration.invoked",
        details: { source_version: "unknown", source_build: "unknown" },
      },
      {
        event: "migrate.invoked",
        details: { source_version: "1.0.4", source_build: "104" },
      },
      {
        event: "step.failed",
        code: "sdk_data_plane_dispatch_failed",
        details: {
          step: "prepare.legacy_work",
          sub_step: "pending_dispatches",
        },
      },
      { event: "restore.started", details: {} },
      { event: "restore.finished", details: { files: 9 } },
      {
        event: "migrate.failed",
        code: "sdk_data_plane_dispatch_failed",
        details: {},
      },
    ]
      .map((line) => JSON.stringify(line))
      .join("\n");
    await writeFile(migrationLogPath(dataDir), `${log}\nnot json\n`);
    await writeFile(
      path.join(dataDir, "sdk-migration-blocked.json"),
      JSON.stringify({
        schema_version: 1,
        code: "sdk_data_plane_dispatch_failed",
        caller: "desktop",
        at: "2026-10-27T00:00:00.000Z",
        frozen: { service_was_enabled: true },
        data_restored: true,
      }),
    );
    const deps = dependencies(0);

    expect(
      showMigrationFailureDialog({
        // Desktop's own migration throws the code the note records.
        error: new Error("sdk_data_plane_dispatch_failed"),
        dataDir,
        locale: "zh-CN",
        dependencies: deps,
        environment: {
          appVersion: "2.0.6",
          buildNumber: 206,
          osName: "macOS",
          osVersion: "15.6",
          arch: "arm64",
          home: accountHome,
        },
      }),
    ).toBe(true);

    expect(deps.showMessageBoxSync.mock.calls[0]?.[0]?.detail).toContain(
      "记忆数据已恢复到升级前的状态。",
    );
    expect(deps.showItemInFolder).toHaveBeenCalledWith(
      migrationLogPath(dataDir),
    );
    const url = String(
      (deps.openExternal.mock.calls[0] as unknown[] | undefined)?.[0],
    );
    expect(url.length).toBeLessThanOrEqual(MAX_ISSUE_URL_LENGTH);
    const body = new URL(url).searchParams.get("body");
    expect(body).toBe(
      [
        "## 升级迁移失败",
        "- 从 MemHub 1.0.4（build 104）升级到 2.0.6（build 206），迁移数据失败。",
        "- 错误码：sdk_data_plane_dispatch_failed",
        "- 失败步骤：prepare.legacy_work / pending_dispatches",
        "- 数据已恢复到升级前状态：是",
        "- 系统：macOS 15.6（arm64）",
        `- 迁移日志（本地）：~${path.sep}${path.join(".one-memory", "logs", "sdk-migration.log")}`,
        "",
        "请把上面这个日志文件拖到这里作为附件，再提交。",
        "",
      ].join("\n"),
    );
    expect(body).not.toContain(accountHome);
  });

  it("says 未知 for what the log does not know instead of guessing", () => {
    const body = migrationIssueBody({
      code: "sdk_migration_preflight_failed",
      fromVersion: "../../etc",
      logPath: "~/.one-memory/logs/sdk-migration.log",
    });
    expect(body).toContain(
      "- 从 MemHub 未知（build 未知）升级到 未知（build 未知）",
    );
    expect(body).toContain("- 失败步骤：未知");
    expect(body).toContain("- 数据已恢复到升级前状态：未知");
    expect(body).toContain("- 系统：未知");
  });

  it("keeps the issue URL within GitHub's limit and encodes it", () => {
    const body = `${"迁移".repeat(4000)}\n& = # ?`;
    const url = migrationIssueUrl("sdk_migration_preflight_failed", body);
    expect(url.length).toBeLessThanOrEqual(MAX_ISSUE_URL_LENGTH);
    const parsed = new URL(url);
    expect(parsed.searchParams.get("title")).toContain(
      "sdk_migration_preflight_failed",
    );
    expect(body.startsWith(parsed.searchParams.get("body") ?? "x")).toBe(true);
    const small = migrationIssueUrl("sdk_migration_x", "a & b = c #d");
    expect(new URL(small).searchParams.get("body")).toBe("a & b = c #d");
  });

  it("shows a custom data dir as it is and the default one under ~", () => {
    expect(
      displayPath("/Users/alice/.one-memory/logs/x.log", "/Users/alice"),
    ).toBe(`~${path.sep}${path.join(".one-memory", "logs", "x.log")}`);
    expect(displayPath("/Volumes/data/memhub/logs/x.log", "/Users/alice")).toBe(
      "/Volumes/data/memhub/logs/x.log",
    );
    expect(displayPath("/Users/alice2/x.log", "/Users/alice")).toBe(
      "/Users/alice2/x.log",
    );
  });

  it("reads only the latest attempt's step and restore from the log", async () => {
    const dataDir = await home();
    await mkdir(path.join(dataDir, "logs"), { recursive: true });
    await writeFile(
      migrationLogPath(dataDir),
      [
        {
          event: "migrate.invoked",
          details: { source_version: "1.0.4", source_build: 104 },
        },
        { event: "step.failed", details: { step: "vectors.reattach" } },
        { event: "restore.finished", details: {} },
        {
          event: "migrate.invoked",
          details: { source_version: "unknown", source_build: "unknown" },
        },
        {
          event: "step.failed",
          details: { step: "preflight.model_profile_check" },
        },
      ]
        .map((line) => JSON.stringify(line))
        .join("\n"),
    );
    expect(readMigrationLogFacts(dataDir)).toEqual({
      fromVersion: "1.0.4",
      fromBuild: 104,
      step: "preflight.model_profile_check",
      subStep: null,
      code: null,
      dataRestored: null,
    });
  });

  it("leaves other startup failures to the generic error box", async () => {
    const deps = dependencies(0);
    expect(
      showMigrationFailureDialog({
        error: new Error("installed_version_conflict"),
        dataDir: await home(),
        locale: "zh-CN",
        dependencies: deps,
      }),
    ).toBe(false);
    expect(deps.showMessageBoxSync).not.toHaveBeenCalled();
  });
});
