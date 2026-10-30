// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryEvidencePanel } from "./MemoryEvidencePanel";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("OVL-01/02: rapid reversal ignores the old animation completion and restores focus once", async () => {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("max-width"),
    addEventListener() {},
    removeEventListener() {},
  }));
  const animations: { finish(): void; cancel: ReturnType<typeof vi.fn> }[] = [];
  vi.stubGlobal("Animation", class {});
  Object.defineProperty(HTMLElement.prototype, "animate", {
    configurable: true,
    value: vi.fn(() => {
      let resolve = () => {};
      const finished = new Promise<void>((done) => {
        resolve = done;
      });
      const cancel = vi.fn();
      animations.push({ finish: resolve, cancel });
      return { finished, cancel };
    }),
  });
  const trigger = document.createElement("button");
  document.body.append(trigger);
  trigger.focus();
  const content = (open: boolean) => (
    <MemoryEvidencePanel open={open} onClose={() => {}}>
      <button type="button">关闭</button>
      <p>原文</p>
    </MemoryEvidencePanel>
  );
  const view = render(content(false));
  view.rerender(content(true));
  expect(screen.getByRole("dialog").contains(document.activeElement)).toBe(
    true,
  );
  expect(trigger.inert).toBe(true);
  view.rerender(content(false));
  view.rerender(content(true));
  await act(async () => {
    animations[0].finish();
    animations[1].finish();
  });
  expect(screen.getByRole("dialog").dataset.phase).toBe("opening");
  await act(async () => animations[2].finish());
  expect(screen.getByRole("dialog").dataset.phase).toBe("open");
  view.rerender(content(false));
  await act(async () => animations[3].finish());
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(trigger);
  expect(trigger.inert).not.toBe(true);
  view.unmount();
  trigger.remove();
  delete (HTMLElement.prototype as { animate?: unknown }).animate;
});

it("SYS-01: reduced motion skips animation and does not require animationend", () => {
  vi.stubGlobal("matchMedia", () => ({
    matches: true,
    addEventListener() {},
    removeEventListener() {},
  }));
  const view = render(
    <MemoryEvidencePanel open onClose={() => {}}>
      <button type="button">关闭</button>
    </MemoryEvidencePanel>,
  );
  expect(screen.getByRole("dialog").dataset.phase).toBe("open");
  view.rerender(
    <MemoryEvidencePanel open={false} onClose={() => {}}>
      原文
    </MemoryEvidencePanel>,
  );
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("starts a new or resumed detail at the top without resetting the current detail", () => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  const content = (scrollResetKey: string, suspended = false) => (
    <MemoryEvidencePanel
      open
      suspended={suspended}
      scrollResetKey={scrollResetKey}
      onClose={() => {}}
    >
      <p>{scrollResetKey}</p>
    </MemoryEvidencePanel>
  );
  const view = render(content("memory-a"));
  const panel = view.container.querySelector<HTMLElement>(".map-inspector");
  expect(panel).toBeTruthy();
  if (!panel) return;

  panel.scrollTop = 180;
  view.rerender(content("memory-a"));
  expect(panel.scrollTop).toBe(180);

  view.rerender(content("memory-b"));
  expect(panel.scrollTop).toBe(0);

  panel.scrollTop = 120;
  view.rerender(content("memory-b", true));
  expect(panel.scrollTop).toBe(120);
  view.rerender(content("memory-b"));
  expect(panel.scrollTop).toBe(0);
});
