// @vitest-environment jsdom

/** A-33: the width mismatch has to be a question, and the answer must default
 * to keeping what the user already has.
 *
 * The machine this ships to has a 768-dim library and a 512-dim built-in
 * model, so this prompt is not an edge case — it is the first screen after
 * install. Getting the default wrong here archives a live graph, which is the
 * accident this whole path exists to prevent.
 */

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GraphWidthPrompt } from "./GraphWidthPrompt";
import { setUiLanguage } from "./ui-language";

const { getGraphWidth, decideGraphWidth, apiErrorCode } = vi.hoisted(() => ({
  getGraphWidth: vi.fn(),
  decideGraphWidth: vi.fn(),
  apiErrorCode: vi.fn((): string | null => null),
}));

vi.mock("./api", () => ({
  apiErrorCode,
  getGraphWidth,
  decideGraphWidth,
}));

const MISMATCH = {
  mismatch: {
    stored_width: 768,
    expected_width: 512,
    graph_path: "/Users/me/.one-memory/hy-memory/graph.kuzu",
  },
  memory_count: 427,
};

describe("GraphWidthPrompt", () => {
  afterEach(async () => {
    cleanup();
    await setUiLanguage("zh-CN");
  });

  beforeEach(() => {
    vi.clearAllMocks();
    apiErrorCode.mockReturnValue(null);
    getGraphWidth.mockResolvedValue(MISMATCH);
    decideGraphWidth.mockResolvedValue({
      archived_path: null,
      exported_path: null,
      mismatch: null,
    });
  });

  it("moves focus into the modal and keeps Tab inside it", async () => {
    const user = userEvent.setup();
    render(
      <>
        <button type="button">Background action</button>
        <GraphWidthPrompt />
      </>,
    );
    const dialog = await screen.findByRole("dialog", {
      name: "现有记忆库和内置模型对不上",
    });
    const keep = screen.getByRole("radio", { name: /保留现有记忆库/ });
    const confirm = screen.getByRole("button", { name: "就这么办" });
    await waitFor(() => expect(document.activeElement).toBe(keep));

    await user.tab({ shift: false });
    expect(dialog.contains(document.activeElement)).toBe(true);
    await user.tab({ shift: false });
    expect(dialog.contains(document.activeElement)).toBe(true);
    await user.tab({ shift: false });
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(confirm).toBeTruthy();
  });

  describe("先导出再决定", () => {
    beforeEach(() => {
      decideGraphWidth.mockResolvedValue({
        archived_path: null,
        exported_path: "/Users/me/one-memory-graph-20260910T021341Z",
        // Exporting settles nothing, so the core still reports the mismatch.
        mismatch: MISMATCH.mismatch,
      });
    });

    async function exportFirst() {
      const user = userEvent.setup();
      render(<GraphWidthPrompt />);
      await user.click(
        await screen.findByRole("radio", { name: /先导出再决定/ }),
      );
      await user.click(screen.getByRole("button", { name: "就这么办" }));
      return user;
    }

    it("把备份真正落在哪里原样告诉用户", async () => {
      await exportFirst();

      expect(
        await screen.findByText(
          /\/Users\/me\/one-memory-graph-20260910T021341Z/,
        ),
      ).toBeTruthy();
    });

    it("导完还留在三选一上，不把人停在半路", async () => {
      await exportFirst();

      await screen.findByText(/one-memory-graph-/);
      expect(
        screen.getByRole("dialog", { name: "现有记忆库和内置模型对不上" }),
      ).toBeTruthy();
      expect(screen.getByRole("radio", { name: /改用内置模型/ })).toBeTruthy();
    });

    it("导出之后再选重建，走的还是明说的那一次", async () => {
      const user = await exportFirst();
      await screen.findByText(/one-memory-graph-/);
      decideGraphWidth.mockResolvedValue({
        archived_path: "/Users/me/graph.dim768.bak",
        exported_path: null,
        mismatch: null,
      });

      await user.click(screen.getByRole("radio", { name: /改用内置模型/ }));
      await user.click(screen.getByRole("button", { name: "就这么办" }));

      await waitFor(() => {
        expect(decideGraphWidth).toHaveBeenLastCalledWith(
          "rebuild_with_new",
          expect.any(String),
        );
      });
      await waitFor(() => {
        expect(screen.queryByRole("dialog")).toBeNull();
      });
    });
  });

  it("keeping the existing library is what is selected before anyone clicks", async () => {
    render(<GraphWidthPrompt />);

    const keep = await screen.findByRole("radio", { name: /保留现有记忆库/ });
    expect((keep as HTMLInputElement).checked).toBe(true);
    const rebuild = screen.getByRole("radio", { name: /改用内置模型/ });
    expect((rebuild as HTMLInputElement).checked).toBe(false);
  });

  it("confirming without touching anything keeps the graph", async () => {
    const user = userEvent.setup();
    render(<GraphWidthPrompt />);

    await user.click(await screen.findByRole("button", { name: "就这么办" }));

    await waitFor(() => {
      expect(decideGraphWidth).toHaveBeenCalledWith(
        "keep_existing",
        expect.any(String),
      );
    });
  });

  it("says what a rebuild costs, in memories rather than in dimensions", async () => {
    render(<GraphWidthPrompt />);

    const rebuild = await screen.findByRole("radio", {
      name: /改用内置模型/,
    });
    expect(rebuild.getAttribute("aria-label") ?? "").toContain("427");
  });

  it("names both widths so the reader can tell which library is which", async () => {
    render(<GraphWidthPrompt />);

    const prompt = await screen.findByRole("dialog", {
      name: "现有记忆库和内置模型对不上",
    });
    expect(prompt.textContent).toContain("768");
    expect(prompt.textContent).toContain("512");
  });

  it("sends the rebuild only when the user picked it by name", async () => {
    const user = userEvent.setup();
    render(<GraphWidthPrompt />);

    await user.click(
      await screen.findByRole("radio", { name: /改用内置模型/ }),
    );
    await user.click(screen.getByRole("button", { name: "就这么办" }));

    await waitFor(() => {
      expect(decideGraphWidth).toHaveBeenCalledWith(
        "rebuild_with_new",
        expect.any(String),
      );
    });
  });

  it("stays out of the way when the widths agree", async () => {
    getGraphWidth.mockResolvedValue({ mismatch: null, memory_count: 427 });

    render(<GraphWidthPrompt />);

    await waitFor(() => {
      expect(getGraphWidth).toHaveBeenCalled();
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("closes once the choice has been carried out", async () => {
    const user = userEvent.setup();
    render(<GraphWidthPrompt />);

    await user.click(await screen.findByRole("button", { name: "就这么办" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  it("switches the dialog and aria labels without changing the default or choice", async () => {
    const user = userEvent.setup();
    render(<GraphWidthPrompt />);
    const keep = await screen.findByRole("radio", { name: /保留现有记忆库/ });
    expect((keep as HTMLInputElement).checked).toBe(true);
    await act(() => setUiLanguage("en"));
    const dialog = screen.getByRole("dialog", {
      name: "Existing memory library does not match the built-in model",
    });
    expect(dialog.textContent).toContain("768 dimensions");
    expect(dialog.textContent).toContain("512");
    const defaultChoice = screen.getByRole("radio", {
      name: /Keep existing library \(default\)/,
    });
    expect((defaultChoice as HTMLInputElement).checked).toBe(true);
    expect(
      screen.getByRole("radio", {
        name: /Archive \(do not delete\).*427 memories/,
      }),
    ).toBeTruthy();
    await user.click(
      screen.getByRole("radio", { name: /Export first, then decide/ }),
    );
    await act(() => setUiLanguage("zh-CN"));
    expect(
      (screen.getByRole("radio", { name: /先导出再决定/ }) as HTMLInputElement)
        .checked,
    ).toBe(true);
    await user.click(screen.getByRole("button", { name: "就这么办" }));
    expect(decideGraphWidth).toHaveBeenCalledWith(
      "export_first",
      expect.any(String),
    );
  });

  it("localizes export receipt while retaining the exact exported path", async () => {
    const user = userEvent.setup();
    const path = "/fixture/backup-原样";
    decideGraphWidth.mockResolvedValue({
      archived_path: null,
      exported_path: path,
      mismatch: MISMATCH.mismatch,
    });
    render(<GraphWidthPrompt />);
    await user.click(
      await screen.findByRole("radio", { name: /先导出再决定/ }),
    );
    await user.click(screen.getByRole("button", { name: "就这么办" }));
    expect(await screen.findByText(path)).toBeTruthy();
    await act(() => setUiLanguage("en"));
    expect(screen.getByText(path).textContent).toBe(path);
    expect(screen.getByText(/Exported to/).textContent).toContain(
      "original library is unchanged",
    );
  });

  it("localizes known export failure without presenting an uncreated backup as safe", async () => {
    const user = userEvent.setup();
    apiErrorCode.mockReturnValue("graph_export_failed");
    decideGraphWidth.mockRejectedValue(
      new Error("Request failed with status code 500"),
    );
    render(<GraphWidthPrompt />);
    await user.click(await screen.findByRole("button", { name: "就这么办" }));
    expect(await screen.findByText(/导出失败，未生成备份/)).toBeTruthy();
    await act(() => setUiLanguage("en"));
    expect(
      screen.getByText(/Export failed and no backup was created/),
    ).toBeTruthy();
    expect(
      (
        screen.getByRole("radio", {
          name: /Keep existing library/,
        }) as HTMLInputElement
      ).checked,
    ).toBe(true);
  });

  it("localizes a refused export without claiming a backup exists", async () => {
    const user = userEvent.setup();
    apiErrorCode.mockReturnValue("export_root_required");
    decideGraphWidth.mockRejectedValue(
      Object.assign(new Error("export_root_required"), {
        definiteRefusal: true,
      }),
    );
    render(<GraphWidthPrompt />);
    await user.click(await screen.findByRole("button", { name: "就这么办" }));
    expect(await screen.findByText(/缺少导出位置，未生成备份/)).toBeTruthy();
    await act(() => setUiLanguage("en"));
    expect(
      screen.getByText(
        /No export location was provided, so no backup was created/,
      ),
    ).toBeTruthy();
  });

  it("localizes unknown outcomes and retains raw external refusal details", async () => {
    const user = userEvent.setup();
    decideGraphWidth.mockRejectedValueOnce(new Error("network interrupted"));
    render(<GraphWidthPrompt />);
    await user.click(await screen.findByRole("button", { name: "就这么办" }));
    expect(await screen.findByText(/宽度选择结果尚未确认/)).toBeTruthy();
    await act(() => setUiLanguage("en"));
    expect(
      screen.getByText(/result of your choice is unconfirmed/),
    ).toBeTruthy();
    const raw = "remote-code-原样";
    decideGraphWidth.mockRejectedValueOnce(
      Object.assign(new Error(raw), { definiteRefusal: true }),
    );
    await user.click(screen.getByRole("button", { name: "Confirm choice" }));
    expect(
      await screen.findByText(`Could not complete the width choice: ${raw}`),
    ).toBeTruthy();
    await act(() => setUiLanguage("zh-CN"));
    expect(screen.getByText(`宽度选择未能完成：${raw}`)).toBeTruthy();
  });
});
