// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryModeChoiceList } from "./MemoryModeChoiceList";

afterEach(cleanup);

const modes = [
  { mode: "lite" as const, title: "Lite", description: "Raw conversations" },
  { mode: "pro" as const, title: "Pro", description: "Extracted memories" },
];

it("supports native radio keyboard selection and reports the chosen control", async () => {
  const changed = vi.fn();
  function Harness() {
    const [selected, setSelected] = useState<"lite" | "pro">("lite");
    return (
      <MemoryModeChoiceList
        aria-label="Memory mode"
        modes={modes}
        selected={selected}
        onChange={(mode, control) => {
          changed(mode, control);
          setSelected(mode as "lite" | "pro");
        }}
      />
    );
  }

  const user = userEvent.setup();
  render(<Harness />);
  const lite = screen.getByRole("radio", { name: /Lite/ });
  lite.focus();
  await user.keyboard("{ArrowDown}");

  const pro = screen.getByRole<HTMLInputElement>("radio", { name: /Pro/ });
  expect(pro.checked).toBe(true);
  expect(changed).toHaveBeenCalledWith("pro", pro);
});

it("exposes current and recommended badges while respecting per-mode disabled state", () => {
  render(
    <MemoryModeChoiceList
      aria-label="Memory mode"
      modes={modes}
      current="lite"
      selected="lite"
      currentBadge="Current"
      recommended={{ mode: "pro", badge: "Recommended" }}
      disabled={(mode) => mode === "pro"}
      onChange={vi.fn()}
    />,
  );

  expect(screen.getByText("Current")).toBeTruthy();
  expect(screen.getByText("Recommended")).toBeTruthy();
  expect(screen.getByRole("radio", { name: /Pro/ })).toHaveProperty(
    "disabled",
    true,
  );
});
