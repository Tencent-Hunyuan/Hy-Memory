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
  it("does not issue on expand; an explicit generate-and-copy shares one local identity", async () => {
    const user = userEvent.setup();
    const prepareConnectionMaterial = vi.fn().mockResolvedValue({
      mcp: '{"mcpServers":{}}',
      shell: "query local",
      prompt: "connect local only",
    });
    const copyText = vi.fn().mockResolvedValue(undefined);
    window.oneMemory = {
      prepareConnectionMaterial,
      copyText,
    } as unknown as Window["oneMemory"];
    render(<ConnectionFallback />);
    expect(prepareConnectionMaterial).not.toHaveBeenCalled();
    await user.click(screen.getByText("连接其他工具"));
    expect(prepareConnectionMaterial).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("本机连接材料")).toBeNull();
    expect(screen.getByText(/同一身份与权限/)).toBeTruthy();
    expect(screen.getByText(/原文片段/)).toBeTruthy();
    expect(screen.getByText(/不会自动导入/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "生成并复制" }));
    await waitFor(() =>
      expect(copyText).toHaveBeenCalledWith("connect local only"),
    );
    expect(prepareConnectionMaterial).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("本机连接材料")).toHaveProperty(
      "value",
      "connect local only",
    );
    expect(screen.getByRole("status").textContent).toContain("尚未建立连接");
    await user.click(screen.getByRole("tab", { name: "MCP 配置" }));
    await user.click(screen.getByRole("button", { name: "复制配置" }));
    expect(copyText).toHaveBeenLastCalledWith('{"mcpServers":{}}');
    expect(prepareConnectionMaterial).toHaveBeenCalledTimes(1);
  });
  it("keeps missing grant fail closed; clipboard failure never claims copied", async () => {
    const user = userEvent.setup();
    const prepareConnectionMaterial = vi
      .fn()
      .mockRejectedValueOnce(new Error("scope unavailable"))
      .mockResolvedValue({ mcp: "{}", shell: null, prompt: "local" });
    const copyText = vi.fn().mockRejectedValue(new Error("clipboard denied"));
    window.oneMemory = {
      prepareConnectionMaterial,
      copyText,
    } as unknown as Window["oneMemory"];
    render(<ConnectionFallback />);
    await user.click(screen.getByText("连接其他工具"));
    expect(prepareConnectionMaterial).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "生成并复制" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByLabelText("本机连接材料")).toBeNull();
    expect(copyText).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "生成并复制" }));
    await screen.findByLabelText("本机连接材料");
    expect(screen.getByRole("tab", { name: "命令" })).toHaveProperty(
      "disabled",
      true,
    );
    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toContain("复制失败"),
    );
    expect(screen.getByRole("status").textContent).not.toContain("已复制");
  });
  it("does not invent runtime paths in the browser", () => {
    render(<ConnectionFallback />);
    expect(screen.queryByLabelText("本机连接材料")).toBeNull();
    expect(screen.getByText("本机连接材料仅在桌面 App 中可用。")).toBeTruthy();
  });
});
