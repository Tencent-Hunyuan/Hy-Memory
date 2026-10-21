// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConnectionFallback } from "./ConnectionFallback";
import { setUiLanguage } from "./ui-language";

afterEach(async () => {
  cleanup();
  delete window.oneMemory;
  await setUiLanguage("zh-CN");
});
describe("ConnectionFallback", () => {
  it("loads machine-local material on demand and never calls copy a connection", async () => {
    const user = userEvent.setup();
    const getConnectionMaterial = vi.fn().mockResolvedValue({
      mcp: '{"mcpServers":{}}',
      shell: "echo query",
      prompt: "connect local only",
    });
    const copyText = vi.fn().mockResolvedValue(undefined);
    window.oneMemory = {
      getConnectionMaterial,
      copyText,
    } as unknown as Window["oneMemory"];
    render(<ConnectionFallback />);
    expect(getConnectionMaterial).not.toHaveBeenCalled();
    await user.click(screen.getByText("连接其他工具"));
    expect(await screen.findByLabelText("本机连接材料")).toHaveProperty(
      "value",
      "connect local only",
    );
    await user.click(screen.getByRole("button", { name: "复制提示词" }));
    expect(copyText).toHaveBeenCalledWith("connect local only");
    expect(screen.getByRole("status").textContent).toContain("尚未建立连接");
    await user.click(screen.getByRole("tab", { name: "MCP 配置" }));
    expect(screen.getByLabelText("本机连接材料")).toHaveProperty(
      "value",
      '{"mcpServers":{}}',
    );
  });
  it("offers retry and clipboard failure recovery without fake success", async () => {
    const user = userEvent.setup();
    const getConnectionMaterial = vi
      .fn()
      .mockRejectedValueOnce(new Error())
      .mockResolvedValue({ mcp: "{}", shell: null, prompt: "local" });
    window.oneMemory = {
      getConnectionMaterial,
      copyText: vi.fn().mockRejectedValue(new Error()),
    } as unknown as Window["oneMemory"];
    render(<ConnectionFallback />);
    await user.click(screen.getByText("连接其他工具"));
    await user.click(await screen.findByRole("button", { name: "重试" }));
    await screen.findByLabelText("本机连接材料");
    expect(screen.getByRole("tab", { name: "命令" })).toHaveProperty(
      "disabled",
      true,
    );
    await user.click(screen.getByRole("button", { name: "复制提示词" }));
    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toContain("复制失败"),
    );
  });
  it("switches visible copy while preserving machine-supplied connection material", async () => {
    const user = userEvent.setup();
    const material = "local-only-config";
    window.oneMemory = {
      getConnectionMaterial: vi
        .fn()
        .mockResolvedValue({ mcp: material, shell: null, prompt: material }),
      setUiLanguage: vi.fn().mockResolvedValue("en"),
    } as unknown as Window["oneMemory"];
    render(<ConnectionFallback />);
    await user.click(screen.getByText("连接其他工具"));
    await screen.findByLabelText("本机连接材料");
    await setUiLanguage("en");
    expect(screen.getByText("Connect other tools")).toBeTruthy();
    expect(
      screen.getByRole("textbox", { name: "Local connection details" }),
    ).toHaveProperty("value", material);
    expect(
      screen.getByText(/Contains local paths, but no memories or keys/),
    ).toBeTruthy();
  });
  it("does not invent runtime paths in the browser", () => {
    render(<ConnectionFallback />);
    expect(screen.queryByLabelText("本机连接材料")).toBeNull();
    expect(screen.getByText("本机连接材料仅在桌面 App 中可用。")).toBeTruthy();
  });
});
