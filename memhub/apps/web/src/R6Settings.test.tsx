// @vitest-environment jsdom

import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Ref } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { getModelProfiles, getSedimentation, setSedimentation } from "./api";
import { exportDiagnosticsFile } from "./diagnostics-export";
import { queryClient, resetClient } from "./query-client";
import { R6Settings } from "./R6Settings";
import { refreshLoginPreference } from "./runtime-state";
import { setUiLanguage } from "./ui-language";

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
  ConnectionFallback: () => null,
}));
vi.mock("./runtime-state", () => ({
  refreshLoginPreference: vi.fn(),
  setLoginPreference: vi.fn(),
  useLoginPreference: () => null,
}));
vi.mock("./diagnostics-export", () => ({
  exportDiagnosticsFile: vi.fn(),
}));
afterEach(async () => {
  cleanup();
  resetClient();
  vi.clearAllMocks();
  window.oneMemory = undefined;
  await setUiLanguage("zh-CN");
  window.localStorage.clear();
});

it("switches settings labels to English without changing model configuration", async () => {
  const user = userEvent.setup();
  render(<R6Settings onDiagnostics={vi.fn()} />);
  await user.selectOptions(
    screen.getByRole("combobox", { name: "语言" }),
    "en",
  );
  expect(await screen.findByRole("heading", { name: "Settings" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "App language" })).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Help and diagnostics" }),
  ).toBeTruthy();
  expect(
    (screen.getByRole("combobox", { name: "Language" }) as HTMLSelectElement)
      .value,
  ).toBe("en");
  expect(window.localStorage.getItem("memhub.ui-language")).toBe("en");
  expect(setSedimentation).not.toHaveBeenCalled();
});

it("localizes async status failures and diagnostic export feedback without exporting files", async () => {
  vi.mocked(refreshLoginPreference).mockRejectedValueOnce(new Error("offline"));
  vi.mocked(exportDiagnosticsFile).mockResolvedValueOnce({
    ok: false,
    message: "诊断信息未能导出，请重试。",
  });
  await setUiLanguage("en");
  render(<R6Settings onDiagnostics={vi.fn()} />);
  await userEvent.click(
    screen.getByRole("button", { name: "Help and diagnostics" }),
  );
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Some status information is unavailable. Showing the last known state; please retry.",
  );
  await userEvent.click(screen.getByRole("button", { name: "Export logs" }));
  expect(
    await screen.findByText("Could not export diagnostics. Please retry."),
  ).toBeTruthy();
  expect(exportDiagnosticsFile).toHaveBeenCalledOnce();
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

it("translates a failed model stop live without changing the last confirmed switch state", async () => {
  const user = userEvent.setup();
  vi.mocked(setSedimentation).mockRejectedValueOnce(new Error("offline"));
  render(<R6Settings onDiagnostics={vi.fn()} />);
  const toggle = await screen.findByRole("switch", { name: "用模型沉淀记忆" });
  await user.click(toggle);
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "停止未能确认，模型可能仍在整理记忆，请重试。",
  );
  await act(async () => setUiLanguage("en"));
  expect(screen.getByRole("alert")).toHaveProperty(
    "textContent",
    "Could not confirm stopping. The model may still be processing memories; please retry.",
  );
  expect(
    screen
      .getByRole("switch", { name: "Create memories with a model" })
      .getAttribute("aria-checked"),
  ).toBe("true");
  expect(setSedimentation).toHaveBeenCalledExactlyOnceWith(false);
});

it("reports an unsuccessful enable in English without claiming the model is running", async () => {
  vi.mocked(getSedimentation)
    .mockResolvedValueOnce({
      enabled: false,
      pending_retained: true,
      imports_unaffected: true,
    })
    .mockResolvedValueOnce({
      enabled: false,
      pending_retained: true,
      imports_unaffected: true,
    });
  vi.mocked(setSedimentation).mockRejectedValueOnce(new Error("offline"));
  await setUiLanguage("en");
  render(<R6Settings onDiagnostics={vi.fn()} />);
  const toggle = await screen.findByRole("switch", {
    name: "Create memories with a model",
  });
  expect(toggle.getAttribute("aria-checked")).toBe("false");
  await userEvent.click(toggle);
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Could not confirm enabling. Showing the last confirmed state; please retry.",
  );
  expect(toggle.getAttribute("aria-checked")).toBe("false");
  expect(setSedimentation).toHaveBeenCalledExactlyOnceWith(true);
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
