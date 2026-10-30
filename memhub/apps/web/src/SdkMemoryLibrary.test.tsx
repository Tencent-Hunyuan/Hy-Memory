// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { deleteSdkMemory, getSdkMemory, getSdkMemoryLibrary, updateSdkMemory } =
  vi.hoisted(() => ({
    deleteSdkMemory: vi.fn(),
    getSdkMemory: vi.fn(),
    getSdkMemoryLibrary: vi.fn(),
    updateSdkMemory: vi.fn(),
  }));

vi.mock("./api", () => ({
  deleteSdkMemory,
  getSdkMemory,
  getSdkMemoryLibrary,
  updateSdkMemory,
}));

import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "./gui-language";
import { SdkMemoryLibrary } from "./SdkMemoryLibrary";

const memory = {
  memory_id: "memory-1",
  content: "original",
  layer: "l2_fact",
  status: "active" as const,
  is_latest: true,
  supersedes: [],
  superseded_by: [],
  tags: [],
  user_id: "local",
  agent_id: "one-memory",
  session_id: "session",
  source: "cursor",
  memory_at: null,
  gmt_created: null,
};

describe("SDK memory library", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getSdkMemoryLibrary.mockResolvedValue({
      memories: [memory],
      total: 1,
      offset: 0,
      limit: 100,
    });
    getSdkMemory.mockResolvedValue(memory);
    updateSdkMemory.mockImplementation((_id, content) =>
      Promise.resolve({ ...memory, content }),
    );
    deleteSdkMemory.mockResolvedValue(undefined);
  });
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it("localizes controls while leaving memory content unchanged", async () => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
    render(
      <LanguageProvider>
        <SdkMemoryLibrary active />
      </LanguageProvider>,
    );
    expect(
      await screen.findByRole("navigation", {
        name: "Estado de la memoria",
      }),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "original" })).toBeTruthy();
    expect(screen.getByText("1 memoria")).toBeTruthy();
  });

  it("edits the same memory id and physically deletes without revision or undo", async () => {
    render(<SdkMemoryLibrary active />);
    await userEvent.click(
      await screen.findByRole("button", { name: "original" }),
    );
    const input = screen.getByRole("textbox", { name: "记忆内容" });
    await userEvent.clear(input);
    await userEvent.type(input, "changed");
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() =>
      expect(updateSdkMemory).toHaveBeenCalledWith(
        "memory-1",
        "changed",
        undefined,
        undefined,
      ),
    );
    await userEvent.click(screen.getByRole("button", { name: "删除记忆" }));
    await waitFor(() =>
      expect(deleteSdkMemory).toHaveBeenCalledWith(
        "memory-1",
        undefined,
        undefined,
      ),
    );
    expect(screen.queryByText("撤销")).toBeNull();
    expect(screen.queryByText(/版本/)).toBeNull();
    expect(screen.queryByRole("button", { name: "已删除" })).toBeNull();
  });

  it("pages 20 at a time with a type badge for each scene", async () => {
    const rows = Array.from({ length: 20 }, (_, index) => ({
      ...memory,
      memory_id: `m-${index}`,
      content: `row ${index}`,
      ...(index === 1
        ? {
            scene: "working" as const,
            kind: "experience" as const,
            variant: "working" as const,
          }
        : index === 2
          ? {
              scene: "coding" as const,
              kind: "fact" as const,
              variant: "coding" as const,
            }
          : { scene: "chat" as const, kind: "chat" as const }),
    }));
    getSdkMemoryLibrary.mockImplementation(
      async ({ offset }: { offset: number }) => ({
        memories: rows,
        total: 45,
        offset,
        limit: 20,
      }),
    );
    render(<SdkMemoryLibrary active />);
    expect(await screen.findByText("第 1 / 3 页")).toBeTruthy();
    expect(getSdkMemoryLibrary).toHaveBeenLastCalledWith({
      statuses: ["active"],
      offset: 0,
      limit: 20,
    });
    const badges = document.querySelectorAll(".sdk-memory-scene");
    expect([...badges].slice(0, 3).map((badge) => badge.textContent)).toEqual([
      "对话",
      "工作",
      "编码",
    ]);
    const previous = screen.getByRole("button", { name: "上一页" });
    const next = screen.getByRole("button", { name: "下一页" });
    expect((previous as HTMLButtonElement).disabled).toBe(true);
    await userEvent.click(next);
    expect(await screen.findByText("第 2 / 3 页")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(await screen.findByText("第 3 / 3 页")).toBeTruthy();
    await waitFor(() =>
      expect(
        (screen.getByRole("button", { name: "下一页" }) as HTMLButtonElement)
          .disabled,
      ).toBe(true),
    );
    expect(getSdkMemoryLibrary).toHaveBeenLastCalledWith({
      statuses: ["active"],
      offset: 40,
      limit: 20,
    });
    await userEvent.click(screen.getByRole("button", { name: "上一页" }));
    expect(await screen.findByText("第 2 / 3 页")).toBeTruthy();
  });

  it("edits an experience's solution and task by its kind and variant", async () => {
    const experience = {
      ...memory,
      memory_id: "exp-1",
      content: "Fix tests\nPin seed",
      layer: "experience",
      scene: "coding" as const,
      kind: "experience" as const,
      variant: "coding" as const,
      task: "Fix tests",
      solution: "Pin seed",
    };
    getSdkMemoryLibrary.mockResolvedValue({
      memories: [experience],
      total: 1,
      offset: 0,
      limit: 20,
    });
    getSdkMemory.mockResolvedValue(experience);
    updateSdkMemory.mockImplementation((_id, content, _trace, edit) =>
      Promise.resolve({
        ...experience,
        solution: content,
        task: edit?.task ?? experience.task,
      }),
    );
    render(<SdkMemoryLibrary active />);
    await userEvent.click(
      await screen.findByRole("button", { name: /Fix tests/ }),
    );
    expect(getSdkMemory).toHaveBeenCalledWith("exp-1", {
      kind: "experience",
      variant: "coding",
    });
    const solution = screen.getByRole("textbox", { name: "做法" });
    expect((solution as HTMLTextAreaElement).value).toBe("Pin seed");
    const task = screen.getByRole("textbox", { name: "任务" });
    await userEvent.clear(task);
    await userEvent.type(task, "Fix flaky tests");
    await userEvent.clear(solution);
    await userEvent.type(solution, "Pin the seed");
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() =>
      expect(updateSdkMemory).toHaveBeenCalledWith(
        "exp-1",
        "Pin the seed",
        undefined,
        { kind: "experience", variant: "coding", task: "Fix flaky tests" },
      ),
    );
    await userEvent.click(screen.getByRole("button", { name: "删除记忆" }));
    await waitFor(() =>
      expect(deleteSdkMemory).toHaveBeenCalledWith("exp-1", undefined, {
        kind: "experience",
        variant: "coding",
      }),
    );
  });
});
