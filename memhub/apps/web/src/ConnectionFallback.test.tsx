// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConnectionFallback } from "./ConnectionFallback";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "./gui-language";

it("switches connection prose live, preserves technical bytes and copies only on request", async () => {
  const user = userEvent.setup();
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
  const mcp =
    '{"mcpServers":{"one-memory":{"command":"/synthetic/bin/recall"}}}';
  const shell = "'/synthetic/bin/recall' '查询内容'\n";
  const getConnectionMaterial = vi
    .fn()
    .mockResolvedValue({ mcp, shell, prompt: "synthetic legacy prompt" });
  const copyText = vi.fn().mockResolvedValue(undefined);
  const onLanguagePreferenceChanged = vi.fn().mockReturnValue(() => {});
  window.oneMemory = {
    getConnectionMaterial,
    copyText,
    getLanguagePreference: vi
      .fn()
      .mockResolvedValue({ preference: "es", locale: "es", revision: 1 }),
    onLanguagePreferenceChanged,
  } as unknown as Window["oneMemory"];
  render(
    <LanguageProvider>
      <ConnectionFallback />
    </LanguageProvider>,
  );
  await user.click(await screen.findByText("Conectar otras herramientas"));
  const input = await screen.findByLabelText("Material de conexión local");
  expect((input as HTMLTextAreaElement).value).toContain(
    "No crees un proxy público",
  );
  expect((input as HTMLTextAreaElement).value).toContain(mcp);
  expect((input as HTMLTextAreaElement).value).toContain(shell);
  expect(copyText).not.toHaveBeenCalled();
  await user.click(
    screen.getByRole("button", { name: "Copiar instrucciones" }),
  );
  expect(copyText).toHaveBeenCalledWith((input as HTMLTextAreaElement).value);
  await user.click(screen.getByRole("tab", { name: "Configuración MCP" }));
  expect(input).toHaveProperty("value", mcp);
  const listener = onLanguagePreferenceChanged.mock.calls[0][0];
  listener({ preference: "zh-CN", locale: "zh-CN", revision: 2 });
  expect(await screen.findByRole("tab", { name: "MCP 配置" })).toBeTruthy();
  expect(input).toHaveProperty("value", mcp);
  expect(getConnectionMaterial).toHaveBeenCalledTimes(1);
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  delete window.oneMemory;
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
  it("does not invent runtime paths in the browser", () => {
    render(<ConnectionFallback />);
    expect(screen.queryByLabelText("本机连接材料")).toBeNull();
    expect(screen.getByText("本机连接材料仅在桌面 App 中可用。")).toBeTruthy();
  });
});
