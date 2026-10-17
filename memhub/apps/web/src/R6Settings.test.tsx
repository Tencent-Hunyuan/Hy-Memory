// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "./gui-language";
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
  await user.selectOptions(screen.getByRole("combobox"), "zh-CN");
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
  localStorage.clear();
  vi.clearAllMocks();
});

it("shows the help and diagnostics group by default and hides it when opted out", async () => {
  const user = userEvent.setup();
  const view = render(<R6Settings onDiagnostics={vi.fn()} />);
  expect(screen.getByRole("combobox")).toBeTruthy();
  expect(screen.getAllByRole("option")).toHaveLength(3);
  await user.click(screen.getByRole("button", { name: "帮助与诊断" }));
  expect(screen.getByRole("heading", { name: "帮助与诊断" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "导出日志" })).toBeTruthy();

  view.rerender(<R6Settings onDiagnostics={vi.fn()} showDiagnostics={false} />);
  expect(screen.queryByRole("button", { name: "帮助与诊断" })).toBeNull();
});
