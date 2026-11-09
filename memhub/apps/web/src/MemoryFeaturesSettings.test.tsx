// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { getMemoryFeatures, setMemoryFeature } from "./api";
import { MemoryFeaturesSettings } from "./MemoryFeaturesSettings";

vi.mock("./api", () => ({
  getMemoryFeatures: vi.fn(),
  setMemoryFeature: vi.fn(),
}));

const feature = (name: string, enabled: boolean) => ({
  name,
  enabled,
  configured: null,
  default: false,
  active: enabled,
  reason: enabled ? null : "disabled",
  cost: "none",
  requires_mode: "pro",
  requires_feature: null,
  description: `${name} description`,
});

const state = (features: ReturnType<typeof feature>[]) => ({
  mode: "pro",
  features,
  non_default: {},
  changed: [],
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("renders every SDK feature with its current value", async () => {
  vi.mocked(getMemoryFeatures).mockResolvedValue(
    state([feature("graph_rerank", false), feature("few_shot", true)]),
  );

  render(<MemoryFeaturesSettings />);

  await waitFor(() => expect(screen.getByRole("switch", { name: "graph_rerank" })).toBeDefined());
  expect(screen.getByRole("switch", { name: "few_shot" }).getAttribute("aria-checked")).toBe("true");
  expect(screen.getByRole("switch", { name: "graph_rerank" }).getAttribute("aria-checked")).toBe("false");
});

it("writes through the settings endpoint when a switch is toggled", async () => {
  vi.mocked(getMemoryFeatures).mockResolvedValue(state([feature("graph_rerank", false)]));
  vi.mocked(setMemoryFeature).mockResolvedValue(state([feature("graph_rerank", true)]));

  render(<MemoryFeaturesSettings />);
  const toggle = await screen.findByRole("switch", { name: "graph_rerank" });
  await userEvent.click(toggle);

  await waitFor(() =>
    expect(setMemoryFeature).toHaveBeenCalledWith("graph_rerank", true),
  );
  expect(screen.getByRole("switch", { name: "graph_rerank" }).getAttribute("aria-checked")).toBe("true");
});

it("reports a failed read instead of showing an empty list as success", async () => {
  vi.mocked(getMemoryFeatures).mockRejectedValue(new Error("offline"));

  render(<MemoryFeaturesSettings />);

  await waitFor(() => expect(screen.getByRole("alert")).toBeDefined());
});
