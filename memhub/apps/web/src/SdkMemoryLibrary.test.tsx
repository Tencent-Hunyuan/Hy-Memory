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
  afterEach(cleanup);

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
      expect(updateSdkMemory).toHaveBeenCalledWith("memory-1", "changed"),
    );
    await userEvent.click(screen.getByRole("button", { name: "删除记忆" }));
    await waitFor(() =>
      expect(deleteSdkMemory).toHaveBeenCalledWith("memory-1"),
    );
    expect(screen.queryByText("撤销")).toBeNull();
    expect(screen.queryByText(/版本/)).toBeNull();
    expect(screen.queryByRole("button", { name: "已删除" })).toBeNull();
  });
});
