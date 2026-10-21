// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConnectionFallback } from "./ConnectionFallback";

afterEach(() => {
  cleanup();
  delete window.oneMemory;
});
describe("ConnectionFallback", () => {
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
    render(<ConnectionFallback />);
    await user.click(screen.getByText("连接其他工具"));
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
    render(<ConnectionFallback />);
    await user.click(screen.getByText("连接其他工具"));
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
    expect(screen.queryByLabelText("配对 Skill")).toBeNull();
    expect(screen.getByText("本机配对仅在桌面 App 中可用。")).toBeTruthy();
  });
});
