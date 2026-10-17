// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { getSedimentation, setSedimentation } from "./api";
import { R6Settings } from "./R6Settings";

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
vi.mock("./ModelSettings", () => ({
  ModelSettings: () => null,
}));
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
afterEach(() => {
  cleanup();
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

it("leaves the switch unusable when the state cannot be read", async () => {
  vi.mocked(getSedimentation).mockRejectedValueOnce(new Error("offline"));
  render(<R6Settings onDiagnostics={vi.fn()} />);

  const toggle = (await screen.findByRole("switch", {
    name: "用模型沉淀记忆",
  })) as HTMLButtonElement;
  expect(toggle.disabled).toBe(true);
  expect(setSedimentation).not.toHaveBeenCalled();
});
