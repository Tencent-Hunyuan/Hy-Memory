// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { DesktopFrame } from "./DesktopFrame";
import { R6ColdStartFrame } from "./R6ColdStartFrame";

const onboardingCss = readFileSync(
  join(process.cwd(), "src", "onboarding-r6.css"),
  "utf8",
);

function declaration(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return (
    onboardingCss.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`))?.[1] ?? ""
  );
}

const originalBridge = window.oneMemory;
afterEach(() => {
  cleanup();
  window.oneMemory = originalBridge;
});

describe("desktop window frame", () => {
  it.each([1, 2, 3] as const)(
    "uses no imitation window controls in cold-start step %s",
    (step) => {
      window.oneMemory = {} as NonNullable<typeof window.oneMemory>;
      const { container } = render(
        <DesktopFrame>
          <R6ColdStartFrame
            step={step}
            title="Test"
            eyebrow="Test"
            footer={<button type="button">Continue</button>}
          >
            <p>Content</p>
          </R6ColdStartFrame>
        </DesktopFrame>,
      );
      expect(container.querySelectorAll(".window-drag-region")).toHaveLength(1);
      expect(
        container.querySelectorAll(".onboarding-r6-titlebar"),
      ).toHaveLength(1);
      expect(container.querySelector(".onboarding-r6-traffic")).toBeNull();
      // Branding stays removed; only the explicit GUI language selector
      // occupies the native titlebar, outside the draggable area.
      expect(screen.queryByText("MemHub")).toBeNull();
      expect(screen.queryByText("One Memory")).toBeNull();
      expect(screen.queryByText("第一次见面")).toBeNull();
      expect(
        container.querySelector(".onboarding-r6-titlebar")?.children.length,
      ).toBe(1);
      expect(screen.getByRole("combobox", { name: "界面语言" })).toBeTruthy();
      const titlebar = declaration(".onboarding-r6-titlebar");
      expect(titlebar).toMatch(/flex:\s*0 0 auto/);
      expect(titlebar).toMatch(/min-height:\s*58px/);
      expect(titlebar).toMatch(/-webkit-app-region:\s*drag/);
    },
  );
  it("leaves browser previews without native chrome", () => {
    window.oneMemory = undefined;
    const { container } = render(
      <DesktopFrame>
        <main>Preview</main>
      </DesktopFrame>,
    );
    expect(container.querySelector(".window-drag-region")).toBeNull();
    expect(container.firstElementChild?.tagName).toBe("MAIN");
  });

  it("keeps the drag strip outside interactive content on every screen", async () => {
    window.oneMemory = {} as NonNullable<typeof window.oneMemory>;
    let clicked = 0;
    const { container, rerender } = render(
      <DesktopFrame>
        <main className="cold-start">
          <button type="button" onClick={() => clicked++}>
            Continue
          </button>
        </main>
      </DesktopFrame>,
    );
    const drag = container.querySelector(".window-drag-region");
    expect(drag?.getAttribute("aria-hidden")).toBe("true");
    expect(drag?.children.length).toBe(0);
    expect(drag?.nextElementSibling?.className).toBe("desktop-frame-content");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(clicked).toBe(1);
    expect(onboardingCss).toMatch(
      /\.onboarding-r6\s+:is\([^}]*button[^}]*input[^}]*select[^}]*textarea[^}]*a[^}]*\)\s*\{[^}]*-webkit-app-region:\s*no-drag/s,
    );
    rerender(
      <DesktopFrame>
        <main className="app-shell">Workspace</main>
      </DesktopFrame>,
    );
    expect(container.querySelectorAll(".window-drag-region")).toHaveLength(1);
    expect(container.querySelector(".window-drag-region")).toBe(drag);
  });
});
