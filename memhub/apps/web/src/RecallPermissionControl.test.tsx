// @vitest-environment jsdom

import type { SourceResponse } from "@one-memory/contracts";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  RecallHostStatus,
  RecallPermissionReceipt,
  RecallPermissionStatus,
  RecallPermissionTool,
} from "./global";
import {
  RecallPermissionControl,
  type RecallPermissionControlProps,
} from "./RecallPermissionControl";
import { setUiLanguage } from "./ui-language";

afterEach(async () => {
  cleanup();
  delete window.oneMemory;
  await setUiLanguage("zh-CN");
});

function baseReceipt(): RecallPermissionReceipt {
  return {
    hostId: "workbuddy",
    installationId: "isolated-installation-a",
    revision: 1,
    status: "install-only",
    allowedTools: [],
    allowedScopes: [],
  };
}

function permission(
  overrides: Partial<RecallPermissionStatus> = {},
): RecallPermissionStatus {
  return {
    hostId: "workbuddy",
    state: "install-only",
    reason: null,
    receipt: baseReceipt(),
    ...overrides,
  };
}

function approved(
  tools: readonly RecallPermissionTool[] = ["om_recall"],
  scopes: readonly string[] = ["all-apps"],
): RecallPermissionStatus {
  const base = permission();
  return {
    ...base,
    state: "approved",
    receipt: {
      ...baseReceipt(),
      revision: 2,
      status: "approved",
      allowedTools: tools,
      allowedScopes: scopes,
    },
  };
}

function host(
  status: RecallPermissionStatus | undefined = permission(),
): RecallHostStatus {
  return {
    hostId: "workbuddy",
    label: "WorkBuddy",
    present: true,
    installed: true,
    selfCheck: null,
    skillDrift: null,
    permission: status,
  };
}

function source(id = "workbuddy", authorized = true): SourceResponse {
  return {
    source_id: id,
    display_name: id === "workbuddy" ? "WorkBuddy" : "Cursor",
    authorized,
    available: true,
    detected: true,
    storage_kinds: ["jsonl"],
    root_label: `/isolated/${id}`,
    candidate_path: `/isolated/${id}`,
    candidate_kind: "default",
    root_id: `isolated-${id}`,
    grant_revision: 1,
    imported_messages: 2,
    imported_conversations: 1,
    sync_enabled: false,
    sync_completeness: "additions_only",
    capabilities: {
      historical_import: "implemented",
      incremental_import: "implemented",
      live_sync: "implemented",
      recall: "not_implemented",
      installer: "not_implemented",
      verification: "fixture_only",
    },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function setup(overrides: Partial<RecallPermissionControlProps> = {}) {
  let current = overrides.host?.permission ?? permission();
  const get = vi.fn().mockImplementation(async () => current);
  const set = vi.fn().mockImplementation(async (_id, input) => {
    current = {
      hostId: "workbuddy",
      state: "approved",
      reason: null,
      receipt: {
        hostId: "workbuddy",
        installationId: input.installationId,
        revision: input.expectedRevision + 1,
        status: "approved",
        allowedTools: [...input.allowedTools],
        allowedScopes: [...input.allowedScopes],
      },
    };
    return current;
  });
  const revoke = vi.fn().mockImplementation(async (_id, input) => {
    current = {
      hostId: "workbuddy",
      state: "revoked",
      reason: null,
      receipt: {
        hostId: "workbuddy",
        installationId: input.installationId,
        revision: input.expectedRevision + 1,
        status: "revoked",
        allowedTools: [],
        allowedScopes: [],
      },
    };
    return current;
  });
  const install = vi.fn();
  const query = vi.fn();
  const changed = vi.fn();
  window.oneMemory = {
    getRecallPermission: get,
    setRecallPermission: set,
    revokeRecallPermission: revoke,
    installRecall: install,
    probeRecallHost: query,
    setUiLanguage: vi.fn(async (next: "en" | "zh-CN") => next),
  } as unknown as NonNullable<Window["oneMemory"]>;
  let props: RecallPermissionControlProps = {
    host: host(),
    sources: [source(), source("cursor")],
    onChanged: changed,
    ...overrides,
  };
  const view = render(<RecallPermissionControl {...props} />);
  return {
    ...view,
    get,
    set,
    revoke,
    install,
    query,
    changed,
    update(next: Partial<RecallPermissionControlProps>) {
      props = { ...props, ...next };
      view.rerender(<RecallPermissionControl {...props} />);
    },
  };
}

const button = (name: string) =>
  screen.getByRole<HTMLButtonElement>("button", { name });
const check = (name: string) =>
  screen.getByRole<HTMLInputElement>("checkbox", { name });
/** The sheet's own commit, whatever the sheet is editing. */
const commitButton = () =>
  screen.getByRole<HTMLButtonElement>("button", {
    name: /允许并保存|保存这次范围/,
  });

/** Open the scope sheet. The row's "allow" grants without opening it. */
async function open() {
  await userEvent.click(button("调整范围"));
}
async function confirm() {
  await userEvent.click(commitButton());
}
const defaultTools: RecallPermissionTool[] = [
  "om_recall",
  "om_ask",
  "om_conversations",
  "om_diagnostic_trace",
];
const defaultScopes = ["all-apps", "source:cursor", "source:workbuddy"];
/** The receipt the mock echoes back for a full default grant. */
const approvedAll = () => approved(defaultTools, defaultScopes);

describe("RecallPermissionControl", () => {
  it("switches the visible row, status, accessible names and existing notice without changing permission", async () => {
    const api = setup();
    expect(
      screen.getByRole("region", { name: "WorkBuddy 调用权限" }),
    ).toBeTruthy();
    await userEvent.click(button("允许使用记忆"));
    expect(screen.getByText("已允许 WorkBuddy 使用记忆。")).toBeTruthy();
    await act(async () => setUiLanguage("en"));
    expect(
      screen.getByRole("region", { name: "WorkBuddy memory access" }),
    ).toBeTruthy();
    expect(
      screen.getByText("Memory access allowed within the selected scope"),
    ).toBeTruthy();
    expect(screen.getByText("Allowed WorkBuddy to use memories.")).toBeTruthy();
    expect(button("Adjust memory access")).toBeTruthy();
    expect(button("Revoke memory access")).toBeTruthy();
    expect(button("Refresh memory access").title).toBe("Refresh memory access");
    expect(api.set).toHaveBeenCalledTimes(1);
    expect(api.revoke).not.toHaveBeenCalled();
    await act(async () => setUiLanguage("zh-CN"));
    expect(
      screen.getByRole("region", { name: "WorkBuddy 调用权限" }),
    ).toBeTruthy();
    expect(screen.getByText("已允许 WorkBuddy 使用记忆。")).toBeTruthy();
  });

  it("switches the scope sheet and keeps source names, scope codes and frozen selection unchanged", async () => {
    const scope = `workspace:v1:${"a".repeat(64)}`;
    const api = setup({ workspaceScopes: [{ scope, label: "隔离项目" }] });
    await open();
    await act(async () => setUiLanguage("en"));
    const dialog = screen.getByRole("dialog", {
      name: "WorkBuddy · Use memories",
    });
    expect(
      within(dialog).getByText("Separate memory access permission"),
    ).toBeTruthy();
    expect(within(dialog).getByText("Allowed tools")).toBeTruthy();
    expect(
      within(dialog).getByText("Original conversation sources"),
    ).toBeTruthy();
    expect(check("Ask memories (optional)").checked).toBe(true);
    expect(check("Workspace: 隔离项目").checked).toBe(true);
    expect(check("Cursor original conversations").checked).toBe(true);
    expect(within(dialog).getByText("source:cursor")).toBeTruthy();
    expect(within(dialog).getAllByText(scope)).toHaveLength(2);
    expect(button("Allow and save")).toBeTruthy();
    expect(api.get).not.toHaveBeenCalled();
    expect(api.set).not.toHaveBeenCalled();
    await userEvent.click(button("Allow and save"));
    expect(api.set.mock.calls[0][1].allowedScopes).toEqual([
      ...defaultScopes,
      scope,
    ]);
    expect(screen.getByText("Memory access permission saved.")).toBeTruthy();
    await act(async () => setUiLanguage("zh-CN"));
    expect(screen.getByText("调用授权已保存。")).toBeTruthy();
  });

  it("switches stale and unknown-write errors without exposing IPC diagnostics", async () => {
    const api = setup();
    api.set.mockRejectedValueOnce(new Error("unknown private diagnostic"));
    await userEvent.click(button("允许使用记忆"));
    await act(async () => setUiLanguage("en"));
    expect(screen.getByRole("alert").textContent).toContain(
      "Could not confirm this grant",
    );
    expect(screen.getByRole("alert").textContent).not.toContain(
      "private diagnostic",
    );
    api.get.mockResolvedValueOnce(
      permission({ receipt: { ...baseReceipt(), revision: 3 } }),
    );
    await userEvent.click(button("Allow memory access"));
    expect(screen.getByRole("alert").textContent).toContain(
      "installation, permission version or selected sources changed",
    );
    expect(api.set).toHaveBeenCalledTimes(1);
    await act(async () => setUiLanguage("zh-CN"));
    expect(screen.getByRole("alert").textContent).toContain("关闭后重新打开");
  });

  it("localizes compact control and unavailable installation status", async () => {
    const api = setup({
      compact: true,
      host: host(permission({ state: "unsupported" })),
    });
    await act(async () => setUiLanguage("en"));
    expect(check("Allow memory access").disabled).toBe(true);
    expect(
      screen.getByRole("region", { name: "WorkBuddy memory access" }),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "This connection does not support memory access management",
      ).hidden,
    ).toBe(true);
    api.update({ compact: false });
    expect(
      screen.getByText(
        "This connection does not support memory access management",
      ).hidden,
    ).toBe(false);
  });

  it.each([
    [
      permission({ state: "not-installed" }),
      "Connection not installed; memory access cannot be granted",
    ],
    [
      permission({ state: "relink-required", reason: "legacy_installation" }),
      "An older connection was found. Reconnect before allowing memory access",
    ],
    [
      permission({
        state: "relink-required",
        reason: "managed_config_changed",
      }),
      "Connection settings changed. Review and reconnect before allowing memory access",
    ],
    [
      permission({ state: "unavailable" }),
      "Memory access service is temporarily unavailable",
    ],
    [
      permission({ receipt: null }),
      "Memory access information is incomplete; refresh to check",
    ],
  ])(
    "translates known permission state %j without exposing reason codes",
    async (status, message) => {
      setup({ host: host(status) });
      await act(async () => setUiLanguage("en"));
      expect(screen.getByText(message)).toBeTruthy();
      expect(button("Allow memory access").disabled).toBe(true);
    },
  );

  it("translates read failure and keeps the attempted permission unchanged", async () => {
    const api = setup();
    api.get.mockRejectedValueOnce(new Error("host_permission_invalid_receipt"));
    await act(async () => setUiLanguage("en"));
    await userEvent.click(button("Refresh memory access"));
    expect(screen.getByRole("alert").textContent).toContain(
      "Could not read memory access status",
    );
    expect(screen.getByRole("alert").textContent).not.toContain(
      "host_permission_invalid_receipt",
    );
    expect(api.set).not.toHaveBeenCalled();
    expect(api.revoke).not.toHaveBeenCalled();
  });

  it("translates revoke confirmation and receipt without changing revocation semantics", async () => {
    const api = setup({ host: host(approved()) });
    await act(async () => setUiLanguage("en"));
    await userEvent.click(button("Revoke memory access"));
    expect(
      screen.getByText(/does not delete sources, uninstall the connection/),
    ).toBeTruthy();
    await userEvent.click(
      check("I confirm revoking memory access for this installation"),
    );
    await userEvent.click(button("Confirm revocation"));
    expect(api.revoke).toHaveBeenCalledExactlyOnceWith("workbuddy", {
      installationId: "isolated-installation-a",
      expectedRevision: 2,
      userConfirmed: true,
    });
    expect(
      screen.getByText("Memory access for this installation was revoked."),
    ).toBeTruthy();
  });

  it("allows everything in one click from the row, after rechecking the installation identity", async () => {
    const api = setup();
    expect(screen.getByText("已安装，尚未允许使用记忆")).toBeTruthy();
    await userEvent.click(button("允许使用记忆"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(api.get).toHaveBeenCalledExactlyOnceWith("workbuddy");
    expect(api.set).toHaveBeenCalledExactlyOnceWith("workbuddy", {
      installationId: "isolated-installation-a",
      expectedRevision: 1,
      allowedTools: defaultTools,
      allowedScopes: defaultScopes,
      userConfirmed: true,
    });
    expect(api.changed).toHaveBeenCalledTimes(1);
    expect(api.install).not.toHaveBeenCalled();
    expect(api.query).not.toHaveBeenCalled();
    expect(screen.getByText("已允许 WorkBuddy 使用记忆。")).toBeTruthy();
    expect(screen.getByText("已允许按所选范围使用记忆")).toBeTruthy();
  });

  it("does not spend a row allow on a connection that changed underneath it", async () => {
    const api = setup();
    api.get.mockResolvedValue(
      permission({ receipt: { ...baseReceipt(), revision: 3 } }),
    );
    await userEvent.click(button("允许使用记忆"));
    expect(api.set).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("关闭后重新打开");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByText("已允许 WorkBuddy 使用记忆。")).toBeNull();
  });

  it("keeps a row allow that succeeded even if the parent refresh fails", async () => {
    const api = setup({
      onChanged: vi.fn().mockRejectedValue(new Error("parent refresh failed")),
    });
    await userEvent.click(button("允许使用记忆"));
    expect(api.set).toHaveBeenCalledTimes(1);
    expect(screen.getByText("已允许 WorkBuddy 使用记忆。")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain(
      "授权已保存，但列表刷新失败",
    );
  });

  it("keeps a failed row allow inline, without repeating it", async () => {
    const api = setup();
    api.set.mockRejectedValueOnce(new Error("unknown private diagnostic"));
    await userEvent.click(button("允许使用记忆"));
    expect(screen.getByRole("alert").textContent).toContain("未能确认这次授权");
    expect(screen.queryByText(/private diagnostic/)).toBeNull();
    expect(button("允许使用记忆").disabled).toBe(false);
    await userEvent.click(button("允许使用记忆"));
    expect(api.set).toHaveBeenCalledTimes(2);
    expect(api.set.mock.calls[1][1]).toEqual(api.set.mock.calls[0][1]);
  });

  it("opens the sheet with every offered tool and scope already selected, and writes nothing until it is submitted", async () => {
    const api = setup();
    await open();
    expect(check("检索记忆").checked).toBe(true);
    expect(check("记忆问答（可选）").checked).toBe(true);
    expect(check("读取原始对话").checked).toBe(true);
    expect(check("跨应用共享记忆（all-apps）").checked).toBe(true);
    expect(check("WorkBuddy 原始对话").checked).toBe(true);
    expect(check("Cursor 原始对话").checked).toBe(true);
    for (const fn of [
      api.get,
      api.set,
      api.revoke,
      api.install,
      api.query,
      api.changed,
    ])
      expect(fn).not.toHaveBeenCalled();
    await confirm();
    expect(api.get).toHaveBeenCalledExactlyOnceWith("workbuddy");
    expect(api.set).toHaveBeenCalledExactlyOnceWith("workbuddy", {
      installationId: "isolated-installation-a",
      expectedRevision: 1,
      allowedTools: defaultTools,
      allowedScopes: defaultScopes,
      userConfirmed: true,
    });
    expect(api.changed).toHaveBeenCalledTimes(1);
    expect(api.install).not.toHaveBeenCalled();
    expect(api.query).not.toHaveBeenCalled();
    expect(screen.getByText(/授权不代表应用已实际调用或采用记忆/)).toBeTruthy();
  });

  it.each(["取消", "关闭", "escape"])(
    "%s restores focus with no IPC calls and restores the default selection",
    async (action) => {
      const api = setup();
      const trigger = button("调整范围");
      await open();
      await userEvent.click(check("检索记忆"));
      if (action === "escape") await userEvent.keyboard("{Escape}");
      else await userEvent.click(button(action));
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(document.activeElement).toBe(trigger);
      expect(api.get).not.toHaveBeenCalled();
      expect(api.set).not.toHaveBeenCalled();
      await open();
      expect(check("检索记忆").checked).toBe(true);
    },
  );

  it("disables the allow button when a narrowed selection is no longer grantable", async () => {
    const api = setup();
    await open();
    for (const name of [
      "检索记忆",
      "记忆问答（可选）",
      "读取原始对话",
      "读取脱敏诊断记录",
    ])
      await userEvent.click(check(name));
    expect(button("允许并保存").disabled).toBe(true);
    await userEvent.click(check("检索记忆"));
    expect(button("允许并保存").disabled).toBe(false);
    await userEvent.click(check("跨应用共享记忆（all-apps）"));
    expect(button("允许并保存").disabled).toBe(true);
    expect(api.get).not.toHaveBeenCalled();
    expect(api.set).not.toHaveBeenCalled();
  });

  it("offers only authorized, unambiguous sources, and sends the default selection unchanged", async () => {
    const api = setup({ sources: [source(), source("cursor", false)] });
    await open();
    expect(
      screen.queryByRole("checkbox", { name: "Cursor 原始对话" }),
    ).toBeNull();
    expect(check("WorkBuddy 原始对话").checked).toBe(true);
    await confirm();
    expect(api.set.mock.calls[0][1].allowedTools).toEqual(defaultTools);
    expect(api.set.mock.calls[0][1].allowedScopes).toEqual([
      "all-apps",
      "source:workbuddy",
    ]);
  });

  it("discloses that ask may use the configured model, and lets it be left out", async () => {
    const api = setup();
    await open();
    expect(screen.getByText(/可能使用已配置的模型处理/)).toBeTruthy();
    await userEvent.click(check("记忆问答（可选）"));
    await confirm();
    expect(api.set.mock.calls[0][1].allowedTools).toEqual([
      "om_recall",
      "om_conversations",
      "om_diagnostic_trace",
    ]);
  });

  it("offers only explicitly supplied valid workspace scopes, and includes them by default", async () => {
    const scope = `workspace:v1:${"a".repeat(64)}`;
    const api = setup({
      workspaceScopes: [
        { scope, label: "隔离项目" },
        { scope: "/not-a-scope", label: "不可用路径" },
      ],
    });
    await open();
    expect(screen.queryByRole("checkbox", { name: "不可用路径" })).toBeNull();
    expect(check("工作区：隔离项目").checked).toBe(true);
    await confirm();
    expect(api.set.mock.calls[0][1].allowedScopes).toEqual([
      ...defaultScopes,
      scope,
    ]);
  });

  it("initializes edits from the receipt, discloses replacement and removals, and replaces on submit", async () => {
    const initial = approved(["om_recall"], ["all-apps", "source:workbuddy"]);
    const api = setup({ host: host(initial) });
    await userEvent.click(button("调整调用权限"));
    expect(check("检索记忆").checked).toBe(true);
    expect(check("跨应用共享记忆（all-apps）").checked).toBe(true);
    expect(check("WorkBuddy 原始对话").checked).toBe(true);
    expect(screen.getByText(/替换当前全部工具和范围/)).toBeTruthy();
    await userEvent.click(check("WorkBuddy 原始对话"));
    expect(screen.getByText(/本次将移除/).textContent).toContain(
      "source:workbuddy",
    );
    await confirm();
    expect(api.set.mock.calls[0][1].expectedRevision).toBe(2);
    expect(api.set.mock.calls[0][1].allowedScopes).toEqual(["all-apps"]);
  });

  it("does not silently lose an existing workspace or missing source scope that cannot be verified", async () => {
    const unknown = `workspace:v1:${"b".repeat(64)}`;
    const api = setup({
      host: host(
        approved(["om_recall"], ["all-apps", unknown, "source:absent"]),
      ),
    });
    await userEvent.click(button("调整调用权限"));
    expect(button("保存这次范围").disabled).toBe(true);
    expect(check(`移除未核对范围：${unknown}`).checked).toBe(false);
    expect(check("移除未核对范围：source:absent").checked).toBe(false);
    await userEvent.click(check(`移除未核对范围：${unknown}`));
    await userEvent.click(check("移除未核对范围：source:absent"));
    await confirm();
    expect(api.set.mock.calls[0][1].allowedScopes).toEqual(["all-apps"]);
  });

  it.each(["revision", "installation", "relink"])(
    "blocks stale %s before writes and requires reopening",
    async (change) => {
      const api = setup();
      await open();
      const next = permission();
      if (change === "revision")
        next.receipt = { ...baseReceipt(), revision: 3 };
      if (change === "installation")
        next.receipt = {
          ...baseReceipt(),
          installationId: "isolated-installation-b",
        };
      if (change === "relink") next.state = "relink-required";
      api.get.mockResolvedValue(next);
      await confirm();
      expect(api.set).not.toHaveBeenCalled();
      expect(screen.getByRole("alert").textContent).toContain("关闭后重新打开");
      expect(button("允许并保存").disabled).toBe(true);
    },
  );

  it("latches changed props even if a later poll returns the old receipt", async () => {
    const api = setup();
    await open();
    api.update({ host: host(approvedAll()) });
    api.update({ host: host() });
    expect(screen.getByRole("alert").textContent).toContain("关闭后重新打开");
    expect(button("允许并保存").disabled).toBe(true);
    expect(api.set).not.toHaveBeenCalled();
  });

  it("freezes receipt arrays and revision instead of sharing mutable polling references", async () => {
    const original = approved();
    const api = setup({ host: host(original) });
    await userEvent.click(button("调整调用权限"));
    if (!original.receipt) throw new Error("test receipt missing");
    (original.receipt.allowedScopes as string[]).push("source:cursor");
    api.get.mockResolvedValue(approved());
    await confirm();
    expect(api.set.mock.calls[0][1].allowedScopes).toEqual(["all-apps"]);
    expect(api.set.mock.calls[0][1].expectedRevision).toBe(2);
  });

  it.each(["missing", "revoked", "root"])(
    "blocks a selected raw source becoming %s",
    async (change) => {
      const api = setup();
      await open();
      const next = source();
      if (change === "revoked") next.authorized = false;
      if (change === "root") next.root_id = "replaced";
      api.update({ sources: change === "missing" ? [] : [next] });
      expect(screen.getByRole("alert").textContent).toContain("关闭后重新打开");
      expect(button("允许并保存").disabled).toBe(true);
      expect(api.set).not.toHaveBeenCalled();
    },
  );

  it("ignores late metadata after cancellation and a newer modal has opened", async () => {
    const api = setup();
    const late = deferred<RecallPermissionStatus>();
    api.get.mockReturnValueOnce(late.promise);
    await open();
    await confirm();
    await userEvent.click(button("取消"));
    await open();
    await act(async () => late.resolve(approved()));
    expect(check("检索记忆").checked).toBe(true);
    expect(api.set).not.toHaveBeenCalled();
    expect(api.changed).not.toHaveBeenCalled();
  });

  it("ignores a read completed after unmount", async () => {
    const api = setup();
    const late = deferred<RecallPermissionStatus>();
    api.get.mockReturnValueOnce(late.promise);
    await open();
    await confirm();
    api.unmount();
    await act(async () => late.resolve(permission()));
    expect(api.set).not.toHaveBeenCalled();
    expect(api.changed).not.toHaveBeenCalled();
  });

  it("keeps one pending mutation and prevents closing or resubmitting during commit", async () => {
    const api = setup();
    const write = deferred<RecallPermissionStatus>();
    api.set.mockReturnValueOnce(write.promise);
    await open();
    const submit = button("允许并保存");
    act(() => {
      fireEvent.click(submit);
      fireEvent.click(submit);
    });
    await act(async () => {});
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.set).toHaveBeenCalledTimes(1);
    expect(button("取消").disabled).toBe(true);
    expect(button("关闭").disabled).toBe(true);
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("dialog").getAttribute("aria-busy")).toBe("true");
    await act(async () => write.resolve(approvedAll()));
    expect(screen.getByText("调用授权已保存。")).toBeTruthy();
  });

  it("keeps errors inline, refreshes before retry, and never reuses consent", async () => {
    const api = setup();
    api.set.mockRejectedValueOnce(new Error("unknown private diagnostic"));
    await open();
    await confirm();
    expect(screen.getByRole("alert").textContent).toContain("保存结果尚未确认");
    expect(screen.queryByText(/private diagnostic/)).toBeNull();
    expect(button("允许并保存").disabled).toBe(true);
    await userEvent.click(button("刷新核对"));
    expect(api.set).toHaveBeenCalledTimes(1);
    expect(button("允许并保存").disabled).toBe(false);
    await confirm();
    expect(api.set).toHaveBeenCalledTimes(2);
    expect(api.set.mock.calls[1][1]).toEqual(api.set.mock.calls[0][1]);
  });

  it("does not retry when refresh fails, or when an uncertain write already advanced the receipt", async () => {
    const api = setup();
    api.set.mockRejectedValueOnce(new Error("timeout"));
    await open();
    await confirm();
    api.get.mockRejectedValueOnce(new Error("offline"));
    await userEvent.click(button("刷新核对"));
    expect(api.set).toHaveBeenCalledTimes(1);
    expect(button("允许并保存").disabled).toBe(true);
    api.get.mockResolvedValue(approved());
    await userEvent.click(button("刷新核对"));
    expect(api.set).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("alert").textContent).toContain("关闭后重新打开");
  });

  it("keeps a committed receipt if parent refresh fails, offering read-only retry", async () => {
    const api = setup({
      onChanged: vi.fn().mockRejectedValue(new Error("parent refresh failed")),
    });
    await open();
    await confirm();
    expect(screen.getByRole("alert").textContent).toContain(
      "授权已保存，但列表刷新失败",
    );
    expect(screen.queryByRole("button", { name: "允许并保存" })).toBeNull();
    await userEvent.click(button("刷新核对"));
    expect(api.set).toHaveBeenCalledTimes(1);
    expect(screen.getByText("调用授权已保存。")).toBeTruthy();
  });

  it("does not treat an incomplete or mismatched write response as confirmed success", async () => {
    const api = setup();
    api.set.mockResolvedValueOnce(permission({ receipt: null }));
    await open();
    await confirm();
    expect(screen.getByRole("alert").textContent).toContain("保存结果尚未确认");
    expect(screen.queryByText("调用授权已保存。")).toBeNull();
    expect(api.changed).not.toHaveBeenCalled();
  });

  it("revokes only the frozen installation after separate confirmation, without uninstalling or deleting sources", async () => {
    const api = setup({ host: host(approved()) });
    await userEvent.click(button("撤回调用权限"));
    expect(screen.getByText(/不会删除来源或卸载连接/)).toBeTruthy();
    expect(api.revoke).not.toHaveBeenCalled();
    await userEvent.click(check("我确认撤回此安装的调用授权"));
    await userEvent.click(button("确认撤回"));
    expect(api.revoke).toHaveBeenCalledExactlyOnceWith("workbuddy", {
      installationId: "isolated-installation-a",
      expectedRevision: 2,
      userConfirmed: true,
    });
    expect(api.set).not.toHaveBeenCalled();
    expect(api.install).not.toHaveBeenCalled();
    expect(screen.getByText("此安装的调用授权已撤回。")).toBeTruthy();
  });

  it("retains failed revoke inline and does not repeat it without refreshing and confirming again", async () => {
    const api = setup({ host: host(approved()) });
    api.revoke.mockRejectedValueOnce(new Error("unavailable"));
    await userEvent.click(button("撤回调用权限"));
    await userEvent.click(check("我确认撤回此安装的调用授权"));
    await userEvent.click(button("确认撤回"));
    expect(screen.getByRole("alert").textContent).toContain("撤回结果尚未确认");
    expect(button("确认撤回").disabled).toBe(true);
    await userEvent.click(button("刷新核对"));
    expect(api.revoke).toHaveBeenCalledTimes(1);
    await userEvent.click(check("我确认撤回此安装的调用授权"));
    await userEvent.click(button("确认撤回"));
    expect(api.revoke).toHaveBeenCalledTimes(2);
  });

  it.each(["none", "get", "set", "revoke"])(
    "is honest and disabled with missing %s bridge methods",
    async (missing) => {
      const api = setup();
      if (missing === "none") delete window.oneMemory;
      if (missing === "get") delete window.oneMemory?.getRecallPermission;
      if (missing === "set") delete window.oneMemory?.setRecallPermission;
      if (missing === "revoke") delete window.oneMemory?.revokeRecallPermission;
      api.update({});
      expect(screen.getByText(/当前桌面连接不支持完整权限管理/)).toBeTruthy();
      expect(button("允许使用记忆").disabled).toBe(true);
      fireEvent.click(button("允许使用记忆"));
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(api.set).not.toHaveBeenCalled();
    },
  );

  it.each([
    permission({ receipt: null }),
    permission({ reason: "installation_incomplete" }),
    permission({ state: "relink-required", reason: "legacy_installation" }),
    permission({ state: "not-installed" }),
    permission({ state: "unavailable" }),
    permission({ state: "unsupported" }),
    permission({ receipt: { ...baseReceipt(), revision: 0 } }),
    permission({ receipt: { ...baseReceipt(), hostId: "cursor" } }),
  ])("blocks incomplete or unusable permission metadata: %j", (value) => {
    const api = setup({ host: host(value) });
    expect(button("允许使用记忆").disabled).toBe(true);
    expect(api.get).not.toHaveBeenCalled();
  });

  it("blocks an old shell's missing permission field, and refreshes only on request", async () => {
    const old = host();
    delete old.permission;
    const api = setup({ host: old });
    expect(button("允许使用记忆").disabled).toBe(true);
    expect(api.get).not.toHaveBeenCalled();
    await userEvent.click(button("刷新调用权限"));
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(button("允许使用记忆").disabled).toBe(false);
  });

  it("rechecks disabled during async preflight and never commits after being disabled", async () => {
    const api = setup();
    const read = deferred<RecallPermissionStatus>();
    api.get.mockReturnValueOnce(read.promise);
    await open();
    await confirm();
    api.update({ disabled: true });
    await act(async () => read.resolve(permission()));
    expect(api.set).not.toHaveBeenCalled();
    expect(button("允许并保存").disabled).toBe(true);
  });

  it("never reapproves a terminal revoked installation", () => {
    const api = setup({
      host: host(
        permission({
          state: "revoked",
          receipt: { ...baseReceipt(), revision: 3, status: "revoked" },
        }),
      ),
    });
    expect(button("允许使用记忆").disabled).toBe(true);
    expect(screen.getByText(/需重新连接后再授权/)).toBeTruthy();
    expect(api.get).not.toHaveBeenCalled();
  });

  it.each(["allowedTools", "allowedScopes"])(
    "does not normalize missing %s into a complete receipt",
    (field) => {
      const partial = { ...baseReceipt() } as unknown as Record<
        string,
        unknown
      >;
      delete partial[field];
      const api = setup({
        host: host(
          permission({
            receipt: partial as unknown as RecallPermissionReceipt,
          }),
        ),
      });
      expect(button("允许使用记忆").disabled).toBe(true);
      expect(api.get).not.toHaveBeenCalled();
    },
  );

  it("invalidates the frozen confirmation when the host installation disappears", async () => {
    const api = setup();
    await open();
    api.update({ host: { ...host(), installed: false } });
    expect(screen.getByRole("alert").textContent).toContain("关闭后重新打开");
    expect(button("允许并保存").disabled).toBe(true);
    expect(api.set).not.toHaveBeenCalled();
  });

  it("retains existing matters permission without offering it to a new installation", async () => {
    const api = setup({ host: host(approved(["om_recall", "om_matters"])) });
    await userEvent.click(button("调整调用权限"));
    expect(check("事项（已有授权）").checked).toBe(true);
    await confirm();
    expect(api.set.mock.calls[0][1].allowedTools).toEqual([
      "om_recall",
      "om_matters",
    ]);
    await userEvent.click(button("完成"));
    api.unmount();
    setup();
    await open();
    expect(screen.queryByRole("checkbox", { name: /事项/ })).toBeNull();
  });

  it("does not offer an unverified or replaced source identity", async () => {
    setup({
      sources: [
        { ...source(), grant_revision: undefined },
        { ...source("cursor"), candidate_root_id: "new-root" },
      ],
    });
    await open();
    expect(
      screen.queryByRole("checkbox", { name: "WorkBuddy 原始对话" }),
    ).toBeNull();
    expect(
      screen.queryByRole("checkbox", { name: "Cursor 原始对话" }),
    ).toBeNull();
  });

  it("does not override a newer installation prop with a late manual status read", async () => {
    const api = setup();
    const late = deferred<RecallPermissionStatus>();
    api.get.mockReturnValueOnce(late.promise);
    await userEvent.click(button("刷新调用权限"));
    const next = permission({
      receipt: { ...baseReceipt(), installationId: "isolated-installation-b" },
    });
    api.update({ host: host(next) });
    await act(async () => late.resolve(permission()));
    await open();
    api.get.mockResolvedValue(next);
    await confirm();
    expect(api.set.mock.calls[0][1].installationId).toBe(
      "isolated-installation-b",
    );
  });

  it("owns a body portal, contains focus, restores prior inert state and trigger focus", async () => {
    const background = document.createElement("button");
    background.inert = false;
    const existing = document.createElement("div");
    existing.inert = true;
    document.body.append(background, existing);
    try {
      const api = setup();
      const trigger = button("调整范围");
      await open();
      const dialog = screen.getByRole("dialog");
      expect(api.container.contains(dialog)).toBe(false);
      expect(dialog.parentElement?.parentElement).toBe(document.body);
      expect(background.inert).toBe(true);
      expect(api.container.inert).toBe(true);
      expect(document.activeElement).toBe(
        within(dialog).getByRole("button", { name: "关闭" }),
      );
      // Tab wraps inside the sheet: back from the first control to the last,
      // forward from the last back to the first.
      await userEvent.tab({ shift: true });
      expect(document.activeElement).toBe(button("允许并保存"));
      await userEvent.tab();
      expect(document.activeElement).toBe(button("关闭"));
      background.focus();
      expect(dialog.contains(document.activeElement)).toBe(true);
      await userEvent.keyboard("{Escape}");
      expect(background.inert).toBe(false);
      expect(existing.inert).toBe(true);
      expect(document.activeElement).toBe(trigger);
    } finally {
      background.remove();
      existing.remove();
    }
  });

  it("retains a saved old-installation receipt but requires reopening if identity changes during commit", async () => {
    const api = setup();
    const write = deferred<RecallPermissionStatus>();
    api.set.mockReturnValueOnce(write.promise);
    await open();
    await confirm();
    api.update({
      host: host(
        permission({
          receipt: {
            ...baseReceipt(),
            installationId: "isolated-installation-b",
          },
        }),
      ),
    });
    await act(async () => write.resolve(approvedAll()));
    expect(screen.getByText("调用授权已保存。")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("关闭后重新打开");
    expect(screen.getByText("isolated-installation-a")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "允许并保存" })).toBeNull();
    expect(api.set).toHaveBeenCalledTimes(1);
  });

  it("does not treat its own pending committed receipt arriving via parent props as a different identity", async () => {
    const api = setup();
    const write = deferred<RecallPermissionStatus>();
    api.set.mockReturnValueOnce(write.promise);
    await open();
    await confirm();
    api.update({ host: host(approvedAll()) });
    await act(async () => write.resolve(approvedAll()));
    expect(screen.getByText("调用授权已保存。")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(api.set).toHaveBeenCalledTimes(1);
  });

  it("rejects ambiguous duplicate source IDs instead of displaying two choices for one authority", async () => {
    setup({ sources: [source(), { ...source(), root_id: "different-root" }] });
    await open();
    expect(
      screen.queryByRole("checkbox", { name: "WorkBuddy 原始对话" }),
    ).toBeNull();
  });

  it("restores the actual trigger even when focus was lost before the dialog effect", async () => {
    setup();
    const trigger = button("调整范围");
    expect(document.activeElement).not.toBe(trigger);
    fireEvent.click(trigger);
    await userEvent.click(button("取消"));
    expect(document.activeElement).toBe(trigger);
  });

  it("uses a focusable control fallback after revocation removes the initiating button", async () => {
    setup({ host: host(approved()) });
    await userEvent.click(button("撤回调用权限"));
    await userEvent.click(check("我确认撤回此安装的调用授权"));
    await userEvent.click(button("确认撤回"));
    await userEvent.click(button("完成"));
    expect(document.activeElement).toBe(
      screen.getByRole("region", { name: "WorkBuddy 调用权限" }),
    );
  });

  it("blocks a selected source ID that becomes ambiguous while the sheet is open", async () => {
    const api = setup();
    await open();
    api.update({
      sources: [source(), { ...source(), root_id: "different-root" }],
    });
    expect(button("允许并保存").disabled).toBe(true);
    expect(screen.getByRole("alert").textContent).toContain("关闭后重新打开");
    expect(api.set).not.toHaveBeenCalled();
  });

  it.each([
    "host_grant_revision_conflict",
    "host_grant_revoked",
    "host_permission_installation_mismatch",
    "host_permission_relink_required",
  ])("requires reopening after bounded IPC error %s", async (code) => {
    const api = setup();
    api.set.mockRejectedValueOnce(
      new Error(`Error invoking remote method: ${code}`),
    );
    await open();
    await confirm();
    expect(screen.getByRole("alert").textContent).toContain("关闭后重新打开");
    expect(button("允许并保存").disabled).toBe(true);
    expect(screen.queryByRole("button", { name: "刷新核对" })).toBeNull();
  });
});
