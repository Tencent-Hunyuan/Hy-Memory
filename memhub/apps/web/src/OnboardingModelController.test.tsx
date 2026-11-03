// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { OnboardingModelController } from "./OnboardingModelController";

const { models } = vi.hoisted(() => ({
  models: {
    profiles: [
      {
        profile_id: "active-profile",
        revision: 3,
        name: "Current model",
        provider_kind: "openai_compatible",
        model: "model-1",
        base_url: "https://models.example/v1",
        disable_thinking: true,
        embedding_provider_kind: "local_onnx",
        embedding_model: "bge-small-zh-v1.5",
        embedding_base_url: null,
        timeout_seconds: 90,
        max_concurrency: 4,
        context_window: null,
        has_api_key: true,
        is_active: true,
      },
    ],
    pendingProbe: false,
    probeStorageFailed: false,
    pendingCreation: false,
    creationStorageFailed: false,
    error: null,
    create: vi.fn(),
    update: vi.fn(),
    probeProfile: vi.fn(),
    activate: vi.fn(),
  },
}));

vi.mock("./use-model-profiles", () => ({
  useModelProfiles: () => models,
}));
vi.mock("./use-embedding-switch", () => ({
  useEmbeddingSwitch: () => ({
    current: null,
    plan: null,
    busy: null,
    errorCode: null,
    refreshFailed: false,
    supported: false,
    planActivation: vi.fn(),
    startPlannedSwitch: vi.fn(),
    shouldUseLegacyActivation: () => true,
    clearPlan: vi.fn(),
    clearError: vi.fn(),
  }),
}));
vi.mock("./OnboardingModelStep", () => ({
  OnboardingModelStep: (props: {
    probe?: { message: string } | null;
    enableActionLabel?: string;
    onSaveAndEnable(): Promise<void>;
  }) => (
    <section>
      <p>{props.probe?.message}</p>
      <button type="button" onClick={() => void props.onSaveAndEnable()}>
        {props.enableActionLabel}
      </button>
    </section>
  ),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("resumes onboarding with an already active model after a restart", async () => {
  const enabled = vi.fn();
  render(
    <OnboardingModelController
      sourceNames={[]}
      onBack={vi.fn()}
      onEnabled={enabled}
    />,
  );

  expect(
    await screen.findByText("当前模型已经启用，可以继续完成首次设置。"),
  ).toBeTruthy();
  await userEvent.click(
    screen.getByRole("button", { name: "继续使用当前模型" }),
  );

  await waitFor(() => expect(enabled).toHaveBeenCalledOnce());
  expect(models.activate).not.toHaveBeenCalled();
});
