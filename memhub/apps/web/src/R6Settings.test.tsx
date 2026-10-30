// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Ref } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { getModelProfiles, getSedimentation, setSedimentation } from "./api";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "./gui-language";
import { queryClient, resetClient } from "./query-client";
import { R6Settings } from "./R6Settings";

it("changes settings copy live without rereading profiles or changing permissions", async () => {
  const user = userEvent.setup();
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
  const { getModelProfiles } = await import("./api");
  render(
    <LanguageProvider>
      <R6Settings onDiagnostics={vi.fn()} />
    </LanguageProvider>,
  );
  expect(
    await screen.findByRole("heading", { name: "Modelo de memorias" }),
  ).toBeTruthy();
  expect(
    screen.queryByRole("switch", {
      name: "Permitir que el modelo configurado lea las conversaciones locales",
    }),
  ).toBeNull();
  const reads = vi.mocked(getModelProfiles).mock.calls.length;
  await user.click(screen.getByRole("combobox"));
  await user.click(screen.getByRole("option", { name: "Chino simplificado" }));
  expect(await screen.findByRole("heading", { name: "记忆模型" })).toBeTruthy();
  expect(vi.mocked(getModelProfiles).mock.calls).toHaveLength(reads);
});
vi.mock("./api", () => ({
  getModelProfiles: vi.fn().mockResolvedValue([
    {
      profile_id: "profile-1",
      name: "当前模型",
      model: "model-1",
      base_url: "https://example.test/v1",
      is_active: true,
    },
  ]),
  getSedimentation: vi.fn().mockResolvedValue({
    enabled: true,
    pending_retained: true,
    imports_unaffected: true,
  }),
  setSedimentation: vi.fn(),
}));
vi.mock("./ModelSettings", async () => {
  const { createElement, useImperativeHandle } = await import("react");
  return {
    // Stands in for the real component at its worst: every mutating action is
    // fenced, and the only way out is the recovery entry that lives inside the
    // same area the fence used to keep shut.
    ModelSettings: ({ controlsRef }: { controlsRef?: Ref<unknown> }) => {
      useImperativeHandle(controlsRef, () => ({
        edit: () => false,
        editProfile: () => false,
        add: () => false,
        test: () => Promise.resolve(undefined),
      }));
      return createElement("button", { type: "button" }, "核对测试结果");
    },
  };
});
vi.mock("./ConnectionFallback", () => ({
  ConnectionFallback: ({ defaultExpanded }: { defaultExpanded?: boolean }) => (
    <p data-testid="connection-default">{String(defaultExpanded)}</p>
  ),
}));
vi.mock("./runtime-state", () => ({
  refreshLoginPreference: vi.fn(),
  setLoginPreference: vi.fn(),
  useLoginPreference: () => null,
}));
vi.mock("./diagnostics-export", () => ({
  exportDiagnosticsFile: vi.fn(),
}));
afterEach(() => {
  cleanup();
  localStorage.clear();
  resetClient();
  vi.clearAllMocks();
  window.oneMemory = undefined;
});

it("shows the help and diagnostics group by default and hides it when opted out", async () => {
  window.oneMemory = {
    getBuildInfo: () => ({
      version: "1.0.4",
      buildNumber: "8",
      commit: "1234567890abcdef",
    }),
  } as NonNullable<typeof window.oneMemory>;
  const user = userEvent.setup();
  const view = render(<R6Settings onDiagnostics={vi.fn()} />);
  expect(screen.getByRole("combobox")).toBeTruthy();
  await user.click(screen.getByRole("combobox"));
  expect(screen.getAllByRole("option")).toHaveLength(4);
  await user.keyboard("{Escape}");
  await user.click(screen.getByRole("button", { name: "连接其他应用" }));
  expect(screen.getByTestId("connection-default").textContent).toBe("true");
  await user.click(screen.getByRole("button", { name: "帮助与诊断" }));
  expect(screen.getByRole("heading", { name: "帮助与诊断" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "导出日志" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "关于" })).toBeTruthy();
  expect(screen.getByText("1.0.4")).toBeTruthy();
  expect(screen.getByText("8")).toBeTruthy();
  expect(screen.getByText("1234567890ab")).toBeTruthy();

  view.rerender(<R6Settings onDiagnostics={vi.fn()} showDiagnostics={false} />);
  expect(screen.queryByRole("button", { name: "帮助与诊断" })).toBeNull();
});

it("stops model sedimentation on one switch flip without touching the model config", async () => {
  const user = userEvent.setup();
  vi.mocked(setSedimentation).mockResolvedValue({
    enabled: false,
    pending_retained: true,
    imports_unaffected: true,
  });
  render(<R6Settings onDiagnostics={vi.fn()} />);

  const toggle = await screen.findByRole("switch", { name: "用模型沉淀记忆" });
  expect(toggle.getAttribute("aria-checked")).toBe("true");
  await user.click(toggle);

  expect(setSedimentation).toHaveBeenCalledExactlyOnceWith(false);
  expect(queryClient.getQueryData(["sedimentation"])).toEqual({
    enabled: false,
    pending_retained: true,
    imports_unaffected: true,
  });
  await screen.findByText(/停止后不再调用模型/);
});

it("reports a stop that did not confirm instead of showing it stopped", async () => {
  const user = userEvent.setup();
  vi.mocked(setSedimentation).mockRejectedValue(new Error("offline"));
  render(<R6Settings onDiagnostics={vi.fn()} />);

  const toggle = await screen.findByRole("switch", { name: "用模型沉淀记忆" });
  await user.click(toggle);

  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain(
    "停止未能确认，模型可能仍在整理记忆，请重试。",
  );
  // The model configuration is a different store: a failed switch must not
  // leave the page claiming the model was removed.
  expect(screen.getByRole("heading", { name: "当前模型" })).toBeTruthy();
});

it("opens the model area even when a pending probe fences the editor, so recovery stays reachable", async () => {
  const user = userEvent.setup();
  render(<R6Settings onDiagnostics={vi.fn()} />);

  await user.click(await screen.findByRole("button", { name: "修改模型" }));

  // The fence is still reported: the editor really did refuse.
  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("待核对");
  // What changed is that the recovery entry no longer hides with the form it
  // is supposed to unlock.
  expect(screen.getByRole("button", { name: "核对测试结果" })).toBeTruthy();
});

it("reaches the same recovery entry when no model has been configured yet", async () => {
  vi.mocked(getModelProfiles).mockResolvedValueOnce([]);
  const user = userEvent.setup();
  render(<R6Settings onDiagnostics={vi.fn()} />);

  await user.click(await screen.findByRole("button", { name: "配置模型" }));

  expect(screen.getByRole("button", { name: "核对测试结果" })).toBeTruthy();
});

it("leaves the switch unusable when the state cannot be read", async () => {
  vi.mocked(getSedimentation).mockRejectedValueOnce(new Error("offline"));
  render(<R6Settings onDiagnostics={vi.fn()} />);

  const toggle = (await screen.findByRole("switch", {
    name: "用模型沉淀记忆",
  })) as HTMLButtonElement;
  expect(toggle.disabled).toBe(true);
  expect(setSedimentation).not.toHaveBeenCalled();
});
