import type {
  ModelProfile,
  ModelProfileInput,
  ModelProfilePatch,
} from "@one-memory/contracts";
import { useCallback, useEffect, useRef, useState } from "react";
import { EmbeddingSwitchConfirmDialog } from "./EmbeddingSwitchExperience";
import {
  presetForGeneration,
  validateGenerationModel,
} from "./GenerationModelPrimitives";
import { modelProfileReady, profileFromProbe } from "./model-profile-readiness";
import {
  type OnboardingModelDraft,
  OnboardingModelStep,
} from "./OnboardingModelStep";
import { useOnboardingTranslation } from "./onboarding-language";
import { PROVIDER_PRESETS } from "./provider-presets";
import { traceUiAction } from "./ui-trace";
import { useEmbeddingSwitch } from "./use-embedding-switch";
import { useModelProfiles } from "./use-model-profiles";

const defaultPreset = PROVIDER_PRESETS.find(
  (preset) => preset.id === "deepseek",
);
const initial: OnboardingModelDraft = {
  provider_id: "deepseek",
  name: "DeepSeek",
  provider_kind: "openai_compatible",
  model: defaultPreset?.model ?? "",
  base_url: defaultPreset?.baseUrl ?? "",
  api_key: "",
  disable_thinking: true,
  embedding_mode: "local",
  embedding_provider_kind: "openai_compatible",
  embedding_model: "",
  embedding_base_url: "",
  timeout_seconds: "",
  max_concurrency: "",
  context_window: "",
};

export function OnboardingModelController({
  sourceNames,
  importedConversationCount = 0,
  onBack,
  onProfilePersisted,
  onModelActiveConfirmed,
  generationToken = 1,
  isGenerationCurrent = () => true,
  initialProfileId = null,
  flowError = null,
  connectionAlreadyApplied = false,
}: {
  sourceNames: readonly string[];
  importedConversationCount?: number;
  onBack(): void;
  onProfilePersisted?(profileId: string): void;
  onModelActiveConfirmed?(token: number, ownerProfileId: string): void;
  generationToken?: number;
  isGenerationCurrent?(token: number): boolean;
  initialProfileId?: string | null;
  flowError?: string | null;
  connectionAlreadyApplied?: boolean;
}) {
  const { text, t } = useOnboardingTranslation();
  const models = useModelProfiles();
  const embeddingSwitch = useEmbeddingSwitch();
  const [draft, setDraft] = useState(initial);
  const [advanced, setAdvanced] = useState(false);
  const [saved, setSaved] = useState<ModelProfile | null>(null);
  const [phase, setPhase] = useState<
    "idle" | "saving" | "testing" | "enabling"
  >("idle");
  const [error, setError] = useState<string | null>(null);
  const [fields, setFields] = useState<
    Partial<Record<keyof OnboardingModelDraft, string>>
  >({});
  const [probe, setProbe] = useState<{
    state: "passed" | "failed" | "stale";
    message: string;
  } | null>(null);
  const [switchPendingJobId, setSwitchPendingJobId] = useState<string | null>(
    null,
  );
  const flight = useRef(false);
  const draftTouched = useRef(false);
  const technicalTouched = useRef(
    new Set<
      | "timeout_seconds"
      | "max_concurrency"
      | "context_window"
      | "disable_thinking"
    >(),
  );
  const autoConfirmed = useRef<string | null>(null);
  const confirmReadyProfile = useCallback(
    (profile: ModelProfile, owner = generationToken) => {
      if (!isGenerationCurrent(owner) || !modelProfileReady(profile)) {
        setError("模型凭据或连接状态已变化，请重新测试后再完成首次设置。");
        return false;
      }
      onModelActiveConfirmed?.(owner, profile.profile_id);
      return true;
    },
    [generationToken, isGenerationCurrent, onModelActiveConfirmed],
  );

  useEffect(() => {
    const profile = initialProfileId
      ? (models.profiles ?? []).find(
          (candidate) => candidate.profile_id === initialProfileId,
        )
      : undefined;
    if (!profile || saved || draftTouched.current) return;
    setSaved(profile);
    setDraft((current) => ({
      ...current,
      provider_id: presetForGeneration(profile),
      name: profile.name,
      provider_kind: profile.provider_kind,
      model: profile.model,
      base_url: profile.base_url ?? "",
      disable_thinking: profile.disable_thinking,
      embedding_mode:
        profile.embedding_provider_kind === "local_onnx" ? "local" : "custom",
      embedding_provider_kind:
        profile.embedding_provider_kind === "ollama"
          ? "ollama"
          : "openai_compatible",
      embedding_model: profile.embedding_model ?? "",
      embedding_base_url: profile.embedding_base_url ?? "",
      timeout_seconds: String(profile.timeout_seconds),
      max_concurrency: String(profile.max_concurrency),
      context_window:
        profile.context_window === null ? "" : String(profile.context_window),
    }));
    if (profile.is_active)
      setProbe({
        state: "passed",
        message: "当前模型已经启用，可以继续完成首次设置。",
      });
  }, [initialProfileId, models.profiles, saved]);

  useEffect(() => {
    if (
      !saved?.is_active ||
      (embeddingSwitch.supported !== false && !embeddingSwitch.current) ||
      embeddingSwitch.current?.writes_paused
    )
      return;
    const key = `${generationToken}:${saved.profile_id}`;
    if (autoConfirmed.current === key) return;
    if (confirmReadyProfile(saved, generationToken))
      autoConfirmed.current = key;
  }, [
    embeddingSwitch.current,
    embeddingSwitch.supported,
    confirmReadyProfile,
    generationToken,
    saved,
  ]);

  useEffect(() => {
    const finished = embeddingSwitch.current?.last_finished;
    if (
      !switchPendingJobId ||
      !finished ||
      finished.job_id !== switchPendingJobId
    )
      return;
    if (finished.state === "done") {
      flight.current = false;
      setSwitchPendingJobId(null);
      setPhase("idle");
      if (saved) confirmReadyProfile(saved, generationToken);
    } else if (finished.state === "rolled_back") {
      flight.current = false;
      setSwitchPendingJobId(null);
      setPhase("idle");
      setError("索引更新没有完成，仍在使用原来的搜索模型。请检查连接后重试。");
    }
  }, [
    embeddingSwitch.current?.last_finished,
    confirmReadyProfile,
    generationToken,
    saved,
    switchPendingJobId,
  ]);

  function edit(patch: Partial<OnboardingModelDraft>) {
    if (flight.current) return;
    draftTouched.current = true;
    for (const key of [
      "timeout_seconds",
      "max_concurrency",
      "context_window",
      "disable_thinking",
    ] as const) {
      if (key in patch) technicalTouched.current.add(key);
    }
    if (patch.provider_id && patch.provider_id !== draft.provider_id) {
      const preset = PROVIDER_PRESETS.find(
        (item) => item.id === patch.provider_id,
      );
      patch = {
        ...patch,
        provider_kind: preset?.provider ?? "openai_compatible",
        base_url: preset?.baseUrl ?? "",
        model: preset?.model ?? "",
        name: preset?.label ?? "自定义兼容服务",
        api_key: "",
        timeout_seconds: "",
        max_concurrency: "",
        context_window: "",
        disable_thinking: true,
      };
      technicalTouched.current.clear();
      setSaved(null);
      setAdvanced(!preset?.model);
    }
    setDraft((value) => ({ ...value, ...patch }));
    setFields({});
    if (probe)
      setProbe({ state: "stale", message: "配置已修改，需要重新测试。" });
  }

  function input(): ModelProfileInput | null {
    const next: Partial<Record<keyof OnboardingModelDraft, string>> =
      validateGenerationModel(draft, {
        apiKey: draft.api_key,
        keyRequired:
          draft.provider_kind !== "ollama" &&
          !(saved?.has_configured_api_key ?? saved?.has_api_key),
      });
    if (Object.keys(next).length) {
      setFields(next);
      if (Object.keys(next).some((key) => key !== "api_key" && key !== "name"))
        setAdvanced(true);
      return null;
    }
    const value: ModelProfileInput = {
      name: draft.name.trim(),
      model: draft.model.trim(),
      provider_kind: draft.provider_kind,
      base_url: draft.base_url.trim(),
      api_key: draft.api_key.trim() || undefined,
      disable_thinking: draft.disable_thinking,
    };
    if (
      technicalTouched.current.has("timeout_seconds") &&
      draft.timeout_seconds.trim()
    )
      value.timeout_seconds = Number(draft.timeout_seconds);
    if (
      technicalTouched.current.has("max_concurrency") &&
      draft.max_concurrency.trim()
    )
      value.max_concurrency = Number(draft.max_concurrency);
    if (technicalTouched.current.has("context_window"))
      value.context_window = draft.context_window.trim()
        ? Number(draft.context_window)
        : null;
    return value;
  }

  async function save(test: boolean) {
    if (flight.current) return;
    const value = input();
    if (!value) return;
    flight.current = true;
    setError(null);
    setPhase("saving");
    const owner = generationToken;
    try {
      const { provider_kind: providerKind } = value;
      const patch: ModelProfilePatch = {
        expected_revision: saved?.revision ?? 0,
      };
      if (saved) {
        if (value.name !== saved.name) patch.name = value.name;
        if (value.model !== saved.model) patch.model = value.model;
        if (value.base_url !== saved.base_url) patch.base_url = value.base_url;
        if (draft.api_key.trim()) patch.api_key = draft.api_key.trim();
        if (
          technicalTouched.current.has("disable_thinking") &&
          value.disable_thinking !== saved.disable_thinking
        )
          patch.disable_thinking = value.disable_thinking;
        if (
          technicalTouched.current.has("timeout_seconds") &&
          value.timeout_seconds !== saved.timeout_seconds
        )
          patch.timeout_seconds = value.timeout_seconds;
        if (
          technicalTouched.current.has("max_concurrency") &&
          value.max_concurrency !== saved.max_concurrency
        )
          patch.max_concurrency = value.max_concurrency;
        if (
          technicalTouched.current.has("context_window") &&
          value.context_window !== saved.context_window
        )
          patch.context_window = value.context_window;
      }
      const profile =
        saved && saved.provider_kind === providerKind
          ? await models.update(saved.profile_id, patch)
          : await models.create(value);
      if (!profile) {
        setError("配置结果尚未确认，请在模型设置中核对后重试。");
        return;
      }
      onProfilePersisted?.(profile.profile_id);
      setSaved(profile);
      technicalTouched.current.clear();
      setDraft((current) => ({
        ...current,
        api_key: "",
        timeout_seconds: String(profile.timeout_seconds),
        max_concurrency: String(profile.max_concurrency),
        context_window:
          profile.context_window === null ? "" : String(profile.context_window),
        disable_thinking: profile.disable_thinking,
      }));
      if (!test || !isGenerationCurrent(owner)) return;
      setPhase("testing");
      const result = await models.probeProfile(profile.profile_id);
      if (!isGenerationCurrent(owner)) return;
      const current = profileFromProbe(profile, result);
      const passed = Boolean(
        current && modelProfileReady(current, result ?? null),
      );
      if (current) setSaved(current);
      setProbe({
        state: passed ? "passed" : "failed",
        message: passed
          ? "模型连接测试通过，可以启用。"
          : "连接未通过，请核对模型、密钥和接收方。",
      });
    } catch {
      setError("配置或测试结果暂未确认，请核对后重试。");
    } finally {
      flight.current = false;
      setPhase("idle");
    }
  }

  async function testModel() {
    const pending = models.pendingProbe;
    if (!pending) {
      await save(true);
      return;
    }
    if (
      flight.current ||
      !saved ||
      pending.profileId !== saved.profile_id ||
      !isGenerationCurrent(generationToken)
    )
      return;
    const owner = generationToken;
    flight.current = true;
    setError(null);
    setPhase("testing");
    try {
      const result = await models.probeProfile(saved.profile_id);
      if (!isGenerationCurrent(owner)) return;
      const current = profileFromProbe(saved, result);
      const passed = Boolean(
        current && modelProfileReady(current, result ?? null),
      );
      if (current) setSaved(current);
      setProbe({
        state: passed ? "passed" : "failed",
        message: passed
          ? "模型连接测试通过，可以启用。"
          : "连接未通过，请核对模型、密钥和接收方。",
      });
    } catch {
      setError("上次模型测试结果仍待核对，请稍后重试。");
    } finally {
      flight.current = false;
      setPhase("idle");
    }
  }

  async function enable() {
    if (flight.current || probe?.state !== "passed" || !saved) return;
    if (saved.is_active) {
      if (
        embeddingSwitch.current?.writes_paused ||
        (embeddingSwitch.supported !== false && !embeddingSwitch.current)
      ) {
        setError("模型写入暂时关闭。请等待索引维护完成后重试。");
        return;
      }
      confirmReadyProfile(saved, generationToken);
      return;
    }
    const owner = generationToken;
    flight.current = true;
    setPhase("enabling");
    setError(null);
    const trace = traceUiAction("memory", "submit");
    try {
      const plan = embeddingSwitch.shouldUseLegacyActivation()
        ? null
        : await embeddingSwitch.planActivation(
            saved.profile_id,
            saved.revision,
          );
      if (!isGenerationCurrent(owner)) return;
      if (!plan && !embeddingSwitch.shouldUseLegacyActivation())
        throw new Error("embedding_switch_plan_unconfirmed");
      if (plan?.needed) {
        trace.done("succeeded", { reason: "embedding_switch_confirmation" });
        return;
      }
      const active = await models.activate(saved.profile_id);
      if (!isGenerationCurrent(owner)) return;
      if (
        !active?.is_active ||
        active.profile_id !== saved.profile_id ||
        active.revision !== saved.revision ||
        !modelProfileReady(active)
      )
        throw new Error("model_activation_unconfirmed");
      trace.done("succeeded");
      confirmReadyProfile(active, owner);
    } catch {
      trace.done("unknown", { reason: "model_enable_unconfirmed" });
      setError("模型未能确认启用，请在设置中核对。");
    } finally {
      flight.current = false;
      setPhase("idle");
    }
  }

  return (
    <>
      <OnboardingModelStep
        draft={draft}
        providers={[
          ...PROVIDER_PRESETS.map((preset) => ({
            id: preset.id,
            label: preset.label,
            keyRequired: preset.provider !== "ollama",
            supported: true,
          })),
          {
            id: "custom",
            label: "自定义兼容服务",
            keyRequired: true,
            supported: true,
          },
        ]}
        advancedOpen={advanced}
        onAdvancedChange={setAdvanced}
        onDraftChange={edit}
        phase={phase}
        capability={{ saveAndEnable: true }}
        canSaveAndEnable={probe?.state === "passed" && !!saved}
        enableActionLabel={saved?.is_active ? "继续使用当前模型" : undefined}
        canTest={
          !models.probeStorageFailed &&
          (!models.pendingProbe ||
            models.pendingProbe.profileId === saved?.profile_id)
        }
        testActionLabel={models.pendingProbe ? "核对测试结果" : undefined}
        canSaveDraft={
          !models.pendingCreation &&
          !models.creationStorageFailed &&
          (!initialProfileId || !!saved)
        }
        inputsLocked={flight.current}
        fieldErrors={Object.fromEntries(
          Object.entries(fields).map(([key, value]) => [
            key,
            value === "onboardingIntegerRange"
              ? t(
                  "onboardingIntegerRange",
                  key === "timeout_seconds"
                    ? { min: 1, max: 600 }
                    : key === "max_concurrency"
                      ? { min: 1, max: 32 }
                      : { min: 1024, max: 10_000_000 },
                )
              : text(value),
          ]),
        )}
        error={
          (error ? text(error) : null) ??
          (flowError ? text(flowError) : null) ??
          (embeddingSwitch.current?.writes_paused
            ? text("模型写入暂时关闭。请等待索引维护完成后重试。")
            : null) ??
          (initialProfileId && !saved && !models.refreshing
            ? text("已保存的模型配置暂时无法读取，不会重复创建。请重试刷新。")
            : null) ??
          (models.error ? text(models.error) : null)
        }
        probe={probe}
        profile={
          saved
            ? {
                profile_id: saved.profile_id,
                revision: saved.revision,
                has_configured_api_key:
                  saved.has_configured_api_key ?? saved.has_api_key,
                credential_state: saved.credential_state,
              }
            : null
        }
        sourceNames={sourceNames}
        importedConversationCount={importedConversationCount}
        connectionSummary={
          connectionAlreadyApplied ? "所选连接已启用" : "本次未选择应用连接"
        }
        generationRecipient={draft.base_url || "尚未填写接收方"}
        embeddingRecipient={
          draft.embedding_mode === "local"
            ? "本机内置向量模型"
            : draft.embedding_base_url || draft.base_url || "尚未填写接收方"
        }
        onSaveDraft={() => save(false)}
        onTest={testModel}
        onSaveAndEnable={enable}
        onRecoverCreation={
          models.pendingCreation
            ? async () => {
                const profile = await models.reconcileCreation();
                if (!profile) return;
                onProfilePersisted?.(profile.profile_id);
                technicalTouched.current.clear();
                setSaved(profile);
                setDraft((current) => ({
                  ...current,
                  provider_id: presetForGeneration(profile),
                  name: profile.name,
                  provider_kind: profile.provider_kind,
                  model: profile.model,
                  base_url: profile.base_url ?? "",
                  api_key: "",
                  disable_thinking: profile.disable_thinking,
                  timeout_seconds: String(profile.timeout_seconds),
                  max_concurrency: String(profile.max_concurrency),
                  context_window:
                    profile.context_window === null
                      ? ""
                      : String(profile.context_window),
                }));
              }
            : undefined
        }
        onDiscardMissingCreation={
          models.creationMissing
            ? async () => {
                await models.discardMissingCreation();
              }
            : undefined
        }
        onBack={onBack}
        focusToken={generationToken}
        onActionError={() => setError("操作未确认，草稿已保留。")}
      />
      {embeddingSwitch.plan?.needed && saved ? (
        <EmbeddingSwitchConfirmDialog
          plan={embeddingSwitch.plan}
          busy={embeddingSwitch.busy === "start"}
          errorCode={embeddingSwitch.errorCode}
          onCancel={() => {
            embeddingSwitch.clearPlan();
            embeddingSwitch.clearError();
          }}
          onStart={() => {
            flight.current = true;
            setPhase("enabling");
            void embeddingSwitch.startPlannedSwitch().then((job) => {
              if (job) {
                setSwitchPendingJobId(job.job_id);
                return;
              }
              flight.current = false;
              setPhase("idle");
            });
          }}
        />
      ) : null}
    </>
  );
}
