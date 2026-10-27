// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConnectionFallback } from "./ConnectionFallback";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "./gui-language";

afterEach(() => {
  cleanup();
  delete window.oneMemory;
  localStorage.clear();
});
describe("ConnectionFallback", () => {
  it("localizes pairing controls while preserving generated Skill bytes", async () => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
    const skill = "pair --token '{no-traducir}'";
    window.oneMemory = {
      createCustomHarnessSkill: vi.fn().mockResolvedValue({
        displayName: "Harness 原名",
        expiresAt: "2026-10-19T12:10:00.000Z",
        skill,
      }),
      copyText: vi.fn().mockResolvedValue(undefined),
      getLanguagePreference: vi
        .fn()
        .mockResolvedValue({ preference: "es", locale: "es", revision: 1 }),
      onLanguagePreferenceChanged: vi.fn().mockReturnValue(() => {}),
    } as unknown as Window["oneMemory"];
    render(
      <LanguageProvider>
        <ConnectionFallback defaultExpanded />
      </LanguageProvider>,
    );
    const user = userEvent.setup();
    expect(
      (await screen.findByText("Conectar otras herramientas")).closest(
        "details",
      ),
    ).toHaveProperty("open", true);
    await user.type(
      screen.getByLabelText("Nombre del Harness"),
      "Harness 原名",
    );
    await user.click(
      screen.getByRole("button", {
        name: "Generar Skill de emparejamiento",
      }),
    );
    expect(
      await screen.findByLabelText("Skill de emparejamiento"),
    ).toHaveProperty("value", skill);
  });

  it("names an unknown Harness before generating and copying its pairing Skill", async () => {
    const user = userEvent.setup();
    const createCustomHarnessSkill = vi.fn().mockResolvedValue({
      displayName: "Kimi Code",
      expiresAt: "2026-10-19T12:10:00.000Z",
      skill: "pair with 123456",
    });
    const copyText = vi.fn().mockResolvedValue(undefined);
    window.oneMemory = {
      createCustomHarnessSkill,
      copyText,
    } as unknown as Window["oneMemory"];
    render(<ConnectionFallback defaultExpanded />);
    expect(screen.getByText("连接其他工具").closest("details")).toHaveProperty(
      "open",
      true,
    );
    const generate = screen.getByRole("button", { name: "生成配对 Skill" });
    expect(generate).toHaveProperty("disabled", true);
    await user.type(screen.getByLabelText("Harness 名称"), "Kimi Code");
    await user.click(generate);
    expect(createCustomHarnessSkill).toHaveBeenCalledWith("Kimi Code");
    expect(await screen.findByLabelText("配对 Skill")).toHaveProperty(
      "value",
      "pair with 123456",
    );
    await user.click(screen.getByRole("button", { name: "复制 Skill" }));
    expect(copyText).toHaveBeenCalledWith("pair with 123456");
    expect(screen.getByRole("status").textContent).toContain(
      "粘贴到对应 Harness",
    );
  });
  it("offers retry and clipboard failure recovery without fake pairing success", async () => {
    const user = userEvent.setup();
    const createCustomHarnessSkill = vi
      .fn()
      .mockRejectedValueOnce(new Error())
      .mockResolvedValue({
        displayName: "Kimi Code",
        expiresAt: "2026-10-19T12:10:00.000Z",
        skill: "pair once",
      });
    window.oneMemory = {
      createCustomHarnessSkill,
      copyText: vi.fn().mockRejectedValue(new Error()),
    } as unknown as Window["oneMemory"];
    render(<ConnectionFallback defaultExpanded />);
    await user.type(screen.getByLabelText("Harness 名称"), "Kimi Code");
    await user.click(screen.getByRole("button", { name: "生成配对 Skill" }));
    await user.click(await screen.findByRole("button", { name: "重试" }));
    await screen.findByLabelText("配对 Skill");
    await user.click(screen.getByRole("button", { name: "复制 Skill" }));
    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toContain("复制失败"),
    );
  });
  it("does not invent runtime paths in the browser", () => {
    render(<ConnectionFallback />);
    expect(screen.getByText("连接其他工具").closest("details")).toHaveProperty(
      "open",
      false,
    );
    expect(screen.queryByLabelText("配对 Skill")).toBeNull();
    expect(screen.getByText("本机配对仅在桌面 App 中可用。")).toBeTruthy();
  });
});
