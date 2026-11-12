// @vitest-environment jsdom

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { OnboardingModelController } from "./OnboardingModelController";

const { embedding, getModelProfiles, models, step } = vi.hoisted(() => ({
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
        credential_state: "available" as
          | "available"
          | "missing"
          | "unavailable",
        is_active: true,
        capabilities: {
          endpoint_reachable: true,
          model_available: true,
          embedding_available: true,
          structured_output: false,
        },
      },
    ],
    pendingProbe: null as null | {
      operationId: string;
      profileId: string;
      inputRevision: number;
    },
    probeStorageFailed: false,
    pendingCreation: false,
    creationMissing: null,
    creationStorageFailed: false,
    refreshing: false,
    error: null,
    create: vi.fn(),
    update: vi.fn(),
    probeProfile: vi.fn(),
    activate: vi.fn(),
    reconcileCreation: vi.fn(),
    discardMissingCreation: vi.fn(),
  },
  getModelProfiles: vi.fn(),
  embedding: {
    current: null as null | {
      writes_paused: boolean;
      last_finished?: { job_id: string; state: string };
    },
    plan: null,
    busy: null,
    errorCode: null,
    refreshFailed: false,
    supported: false as boolean | null,
    planActivation: vi.fn(),
    startPlannedSwitch: vi.fn(),
    shouldUseLegacyActivation: (() => true) as () => boolean,
    clearPlan: vi.fn(),
    clearError: vi.fn(),
  },
  step: { current: null as Record<string, unknown> | null },
}));

vi.mock("./api", () => ({
  getModelProfiles: (...args: unknown[]) => getModelProfiles(...args),
}));
vi.mock("./use-model-profiles", () => ({
  useModelProfiles: () => models,
}));
vi.mock("./use-embedding-switch", () => ({
  useEmbeddingSwitch: () => embedding,
}));
vi.mock("./OnboardingModelStep", () => ({
  OnboardingModelStep: (props: Record<string, unknown>) => {
    step.current = props;
    const probe = props.probe as { message: string } | null | undefined;
    return (
      <section>
        <p>{probe?.message}</p>
        <p>{String(props.error ?? "")}</p>
        <button
          type="button"
          onClick={() => void (props.onSaveAndEnable as () => Promise<void>)()}
        >
          {String(props.enableActionLabel ?? "")}
        </button>
        <button
          type="button"
          disabled={!props.canTest}
          onClick={() => void (props.onTest as () => Promise<void>)()}
        >
          {String(props.testActionLabel ?? "测试模型连通性")}
        </button>
        {props.onRecoverCreation ? (
          <button
            type="button"
            onClick={() =>
              void (props.onRecoverCreation as () => Promise<void>)()
            }
          >
            核对创建结果
          </button>
        ) : null}
      </section>
    );
  },
}));

function currentStep(): Record<string, unknown> {
  if (!step.current) throw new Error("onboarding_model_step_not_rendered");
  return step.current;
}

const activeProfile = { ...models.profiles[0] };

beforeEach(() => {
  models.profiles = [{ ...activeProfile }];
  models.pendingCreation = false;
  models.pendingProbe = null;
  models.creationMissing = null;
  embedding.current = null;
  embedding.supported = false;
  embedding.shouldUseLegacyActivation = () => true;
  step.current = null;
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("does not claim or auto-complete an arbitrary active profile without an onboarding id", async () => {
  const enabled = vi.fn();
  render(
    <OnboardingModelController
      sourceNames={[]}
      onBack={vi.fn()}
      onModelActiveConfirmed={enabled}
    />,
  );

  await waitFor(() => expect(step.current).not.toBeNull());
  expect(currentStep().profile).toBeNull();
  expect(enabled).not.toHaveBeenCalled();
  expect(models.activate).not.toHaveBeenCalled();
});

it("resumes and confirms the active profile explicitly owned by onboarding", async () => {
  const enabled = vi.fn();
  render(
    <OnboardingModelController
      sourceNames={[]}
      initialProfileId="active-profile"
      onBack={vi.fn()}
      onModelActiveConfirmed={enabled}
    />,
  );
  expect(
    await screen.findByText("当前模型已经启用，可以继续完成首次设置。"),
  ).toBeTruthy();
  await waitFor(() => expect(enabled).toHaveBeenCalledOnce());
  expect(enabled).toHaveBeenCalledWith(1, "active-profile");
});

it("does not complete with an active profile whose credential is unavailable", async () => {
  models.profiles = [
    {
      ...activeProfile,
      credential_state: "unavailable",
    },
  ];
  const enabled = vi.fn();
  render(
    <OnboardingModelController
      sourceNames={[]}
      initialProfileId="active-profile"
      onBack={vi.fn()}
      onModelActiveConfirmed={enabled}
    />,
  );

  await waitFor(() => expect(step.current).not.toBeNull());
  expect(enabled).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: "继续使用当前模型" }),
  );
  expect(enabled).not.toHaveBeenCalled();
  expect(screen.getByText(/模型凭据或连接状态已变化/)).toBeTruthy();
});

it("hydrates a persisted inactive profile without creating a duplicate", async () => {
  models.profiles = [{ ...activeProfile, is_active: false }];
  const active = vi.fn();
  render(
    <OnboardingModelController
      sourceNames={[]}
      initialProfileId="active-profile"
      onBack={vi.fn()}
      onModelActiveConfirmed={active}
    />,
  );

  await waitFor(() =>
    expect(
      (currentStep().profile as { profile_id: string } | null)?.profile_id,
    ).toBe("active-profile"),
  );
  expect(models.create).not.toHaveBeenCalled();
  expect(active).not.toHaveBeenCalled();
});

it("recovers a pending creation and persists its profile id", async () => {
  models.profiles = [];
  models.pendingCreation = true;
  models.reconcileCreation.mockResolvedValueOnce(activeProfile);
  const persisted = vi.fn();
  render(
    <OnboardingModelController
      sourceNames={[]}
      onBack={vi.fn()}
      onProfilePersisted={persisted}
    />,
  );

  await userEvent.click(
    await screen.findByRole("button", { name: "核对创建结果" }),
  );
  await waitFor(() => expect(persisted).toHaveBeenCalledWith("active-profile"));
  expect(models.create).not.toHaveBeenCalled();
});

it("reconciles a pending probe instead of saving or creating again", async () => {
  const inactive = { ...activeProfile, revision: 4, is_active: false };
  models.profiles = [inactive];
  models.pendingProbe = {
    operationId: "probe-operation",
    profileId: "active-profile",
    inputRevision: 3,
  };
  models.probeProfile.mockResolvedValueOnce({
    profile_id: "active-profile",
    input_profile_revision: 3,
    profile_revision: 4,
    capabilities: {
      endpoint_reachable: true,
      model_available: true,
      embedding_available: true,
    },
    stages: [{ ok: true }],
  });
  getModelProfiles.mockResolvedValueOnce([inactive]);
  render(
    <OnboardingModelController
      sourceNames={[]}
      initialProfileId="active-profile"
      onBack={vi.fn()}
    />,
  );

  await userEvent.click(
    await screen.findByRole("button", { name: "核对测试结果" }),
  );
  await waitFor(() =>
    expect(models.probeProfile).toHaveBeenCalledWith("active-profile"),
  );
  await waitFor(() =>
    expect(screen.getByText("模型连接测试通过，可以启用。")).toBeTruthy(),
  );
  expect(currentStep().canSaveAndEnable).toBe(true);
  expect(models.update).not.toHaveBeenCalled();
  expect(models.create).not.toHaveBeenCalled();
});

it("passes the first test when Core advances the tested profile revision", async () => {
  const inactive = { ...activeProfile, revision: 1, is_active: false };
  models.profiles = [inactive];
  models.update.mockResolvedValueOnce(inactive);
  models.probeProfile.mockResolvedValueOnce({
    profile_id: inactive.profile_id,
    input_profile_revision: 1,
    profile_revision: 2,
    capabilities: {
      endpoint_reachable: true,
      model_available: true,
      embedding_available: true,
    },
    stages: [{ ok: false }],
  });
  models.activate.mockResolvedValueOnce({
    ...inactive,
    revision: 2,
    is_active: true,
    capabilities: {
      endpoint_reachable: true,
      model_available: true,
      embedding_available: true,
    },
  });
  embedding.supported = true;
  embedding.current = { writes_paused: false };
  embedding.shouldUseLegacyActivation = () => false;
  embedding.planActivation.mockResolvedValueOnce({ needed: false });
  const confirmed = vi.fn();
  render(
    <OnboardingModelController
      sourceNames={[]}
      initialProfileId={inactive.profile_id}
      onBack={vi.fn()}
      onModelActiveConfirmed={confirmed}
    />,
  );
  await waitFor(() =>
    expect(
      (currentStep().profile as { profile_id: string } | null)?.profile_id,
    ).toBe(inactive.profile_id),
  );

  await act(async () => {
    await (currentStep().onTest as () => Promise<void>)();
  });

  await waitFor(() =>
    expect(screen.getByText("模型连接测试通过，可以启用。")).toBeTruthy(),
  );
  expect(currentStep().canSaveAndEnable).toBe(true);
  expect(currentStep().profile).toMatchObject({ revision: 2 });
  expect(getModelProfiles).not.toHaveBeenCalled();
  await act(async () => {
    await (currentStep().onSaveAndEnable as () => Promise<void>)();
  });
  expect(embedding.planActivation).toHaveBeenCalledWith(inactive.profile_id, 2);
  expect(models.activate).toHaveBeenCalledWith(inactive.profile_id);
  expect(confirmed).toHaveBeenCalledWith(1, inactive.profile_id);
});

it("keeps an active resumed profile on the model step while writes are paused", async () => {
  embedding.supported = true;
  embedding.current = { writes_paused: true };
  const active = vi.fn();
  render(
    <OnboardingModelController
      sourceNames={[]}
      initialProfileId="active-profile"
      onBack={vi.fn()}
      onModelActiveConfirmed={active}
    />,
  );

  expect(
    await screen.findByText("模型写入暂时关闭。请等待索引维护完成后重试。"),
  ).toBeTruthy();
  expect(active).not.toHaveBeenCalled();
});

it("does not confirm a late embedding gate opening after generation invalidation", async () => {
  embedding.supported = true;
  embedding.current = { writes_paused: true };
  const active = vi.fn();
  let current = true;
  const view = render(
    <OnboardingModelController
      sourceNames={[]}
      initialProfileId="active-profile"
      generationToken={7}
      isGenerationCurrent={() => current}
      onBack={vi.fn()}
      onModelActiveConfirmed={active}
    />,
  );
  await screen.findByText("当前模型已经启用，可以继续完成首次设置。");

  current = false;
  embedding.current = {
    writes_paused: false,
    last_finished: { job_id: "late-job", state: "done" },
  };
  view.rerender(
    <OnboardingModelController
      sourceNames={[]}
      initialProfileId="active-profile"
      generationToken={7}
      isGenerationCurrent={() => current}
      onBack={vi.fn()}
      onModelActiveConfirmed={active}
    />,
  );
  await waitFor(() => expect(active).not.toHaveBeenCalled());
});

it("creates a cold-start profile without embedding or Core-owned runtime defaults", async () => {
  models.profiles = [];
  const created = {
    ...activeProfile,
    profile_id: "created-profile",
    is_active: false,
    timeout_seconds: 240,
  };
  models.create.mockResolvedValueOnce(created);
  render(<OnboardingModelController sourceNames={[]} onBack={vi.fn()} />);

  await act(async () => {
    (currentStep().onDraftChange as (patch: Record<string, unknown>) => void)({
      api_key: "test-key",
    });
  });
  await act(async () => {
    await (currentStep().onSaveDraft as () => Promise<void>)();
  });

  expect(models.create).toHaveBeenCalledOnce();
  const input = models.create.mock.calls[0][0];
  expect(input).toMatchObject({
    provider_kind: "openai_compatible",
    model: "deepseek-flash",
    base_url: "https://api.deepseek.com",
    api_key: "test-key",
  });
  for (const key of [
    "embedding_provider_kind",
    "embedding_model",
    "embedding_base_url",
    "embedding_dimensions",
    "timeout_seconds",
    "max_concurrency",
    "context_window",
  ])
    expect(input).not.toHaveProperty(key);
});

it("clears inherited runtime display on provider switch and creates with Core defaults", async () => {
  models.profiles = [];
  models.create.mockResolvedValueOnce({
    ...activeProfile,
    profile_id: "ollama-profile",
    provider_kind: "ollama",
    base_url: "http://127.0.0.1:11434",
    model: "qwen2.5",
    timeout_seconds: 240,
    is_active: false,
  });
  render(<OnboardingModelController sourceNames={[]} onBack={vi.fn()} />);
  await act(async () => {
    (currentStep().onDraftChange as (patch: Record<string, unknown>) => void)({
      timeout_seconds: "90",
    });
  });
  await act(async () => {
    (currentStep().onDraftChange as (patch: Record<string, unknown>) => void)({
      provider_id: "ollama",
    });
  });
  expect(
    (currentStep().draft as { timeout_seconds: string }).timeout_seconds,
  ).toBe("");
  await act(async () => {
    (currentStep().onDraftChange as (patch: Record<string, unknown>) => void)({
      model: "qwen2.5",
    });
  });
  await act(async () => {
    await (currentStep().onSaveDraft as () => Promise<void>)();
  });
  expect(models.create.mock.calls[0][0]).toMatchObject({
    provider_kind: "ollama",
    base_url: "http://127.0.0.1:11434",
    model: "qwen2.5",
  });
  expect(models.create.mock.calls[0][0]).not.toHaveProperty("timeout_seconds");
});

it("updates generation without overwriting an existing custom embedding", async () => {
  const custom = {
    ...activeProfile,
    is_active: false,
    embedding_provider_kind: "ollama" as const,
    embedding_model: "nomic-embed-text",
    embedding_base_url: null,
    embedding_dimensions: 768,
  };
  models.profiles = [custom];
  models.update.mockResolvedValueOnce({ ...custom, model: "model-2" });
  render(
    <OnboardingModelController
      sourceNames={[]}
      initialProfileId={custom.profile_id}
      onBack={vi.fn()}
    />,
  );
  await waitFor(() =>
    expect(
      (currentStep().profile as { profile_id: string } | null)?.profile_id,
    ).toBe(custom.profile_id),
  );

  await act(async () => {
    (currentStep().onDraftChange as (patch: Record<string, unknown>) => void)({
      model: "model-2",
    });
  });
  await act(async () => {
    await (currentStep().onSaveDraft as () => Promise<void>)();
  });

  expect(models.update).toHaveBeenCalledWith(custom.profile_id, {
    expected_revision: custom.revision,
    model: "model-2",
  });
});
