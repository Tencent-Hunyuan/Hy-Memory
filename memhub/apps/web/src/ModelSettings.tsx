import type {
  ModelProbeResult,
  ModelProfile,
  ModelProfileInput,
  ModelProfilePatch,
  ProbeCode,
  ProviderKind,
} from "@one-memory/contracts";
import { Check, Pencil, Plus } from "lucide-react";
import {
  type FormEvent,
  type Ref,
  useEffect,
  useId,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { EmbeddingSwitchConfirmDialog } from "./EmbeddingSwitchExperience";
import {
  type GenerationModelValues,
  GenerationTechnicalDisclosure,
  GenerationTechnicalFields,
  generationSummary,
  ProviderPresetField,
  presetForGeneration,
  validateGenerationModel,
} from "./GenerationModelPrimitives";
import { modelProfileReady, profileFromProbe } from "./model-profile-readiness";
import { OptionMenu } from "./OptionMenu";
import { useOnboardingTranslation } from "./onboarding-language";
import { PROVIDER_PRESETS } from "./provider-presets";
import { R6StateRecovery } from "./R6StateRecovery";
import { useEmbeddingSwitch } from "./use-embedding-switch";
import { useModelProfiles } from "./use-model-profiles";
import "./model-setup-next.css";

/** The vectoriser that ships with the app. Not a choice, so not a setting. */
const BUILTIN_EMBEDDING_KIND = "local_onnx";

/**
 * The name `seed_preset_profile` gives the profile an internal build creates.
 * Matched by name because that is all the contract carries: an `is_preset`
 * field would put a build-time distinction into the public API, and the API
 * contract is not something this change is allowed to move.
 */
const PRESET_PROFILE_NAME = "预配置模型服务";

/**
 * A configured distribution may point to public provider documentation.
 */
function applyUrl(baseUrl: string | null): string | null {
  if (!baseUrl) return null;
  try {
    const url = new URL(baseUrl);
    return `${url.protocol}//${url.hostname.replace(/^api\./, "")}`;
  } catch {
    return null;
  }
}

function presetKeyLabel(): string {
  return "粘贴 API Key";
}

const PROVIDERS: Array<{
  kind: ProviderKind;
  label: string;
  baseUrl: string;
  keyRequired: boolean;
  // 后端 ModelProfileStore.activate() 目前只放行 openai_compatible 与 ollama，
  // 其余会抛 model_provider_not_supported。这里标记出来并在下拉里禁用，
  // 避免用户填完整个表单、创建成功、最后在激活那一步才撞墙。
  activatable: boolean;
}> = [
  {
    kind: "openai_compatible",
    label: "OpenAI-compatible",
    baseUrl: "https://api.openai.com/v1",
    keyRequired: true,
    activatable: true,
  },
  {
    kind: "anthropic",
    label: "Anthropic Messages（暂不支持）",
    baseUrl: "https://api.anthropic.com",
    keyRequired: true,
    activatable: false,
  },
  {
    kind: "ollama",
    label: "Ollama（本机）",
    baseUrl: "http://127.0.0.1:11434",
    keyRequired: false,
    activatable: true,
  },
  {
    kind: "hy_default",
    label: "HY Memory 托管模型（当前不可用）",
    baseUrl: "",
    keyRequired: false,
    activatable: false,
  },
];

type FieldName =
  | "model"
  | "base_url"
  | "api_key"
  | "timeout_seconds"
  | "max_concurrency"
  | "context_window"
  | "embedding-model"
  | "embedding-base-url";

function keyError(value: string, required: boolean): string | undefined {
  if (!value.trim()) return required ? "请填写 API Key。" : undefined;
  if (/^(?:[a-z][a-z\d+.-]*:\/\/|www\.|\/\/)/i.test(value.trim()))
    return "这里需要 API Key，不是网址。请填写服务方提供的密钥。";
  return undefined;
}

const FIELD_PROBE_TEXT: Partial<Record<ProbeCode, string>> = {
  unauthorized:
    "服务商拒绝了这个 API Key（401）。请确认密钥属于当前填写的服务地址。",
  model_not_found: "服务商没有这个模型名，请对照服务商的模型列表核对。",
  unreachable: "连不上接口地址，请检查网络和服务地址是否正确。",
};

function probeFieldErrors(
  result: ModelProbeResult | null | undefined,
): Partial<Record<FieldName, string>> {
  if (!result) return {};
  const stages = new Map(result.stages.map((stage) => [stage.stage, stage]));
  const errors: Partial<Record<FieldName, string>> = {};
  const text = (stage: string) => {
    const stageResult = stages.get(stage);
    return stageResult && stageResult.code !== "ok"
      ? (FIELD_PROBE_TEXT[stageResult.code] ?? undefined)
      : undefined;
  };
  if (!result.capabilities.endpoint_reachable) {
    errors.base_url = text("endpoint") ?? FIELD_PROBE_TEXT.unreachable;
  }
  if (!result.capabilities.model_available) {
    if (stages.get("model")?.code === "unauthorized") {
      errors.api_key = FIELD_PROBE_TEXT.unauthorized;
    } else {
      errors.model = text("model") ?? FIELD_PROBE_TEXT.model_not_found;
    }
  }
  if (!result.capabilities.embedding_available) {
    errors["embedding-model"] ??=
      text("embedding") ?? FIELD_PROBE_TEXT.embedding_unavailable;
  }
  return errors;
}

function endpointKey(value: string): string {
  return value.trim().replace(/\/+$/, "").toLowerCase();
}

function validEndpoint(value: string): boolean {
  try {
    const url = new URL(value);
    return (
      ["http:", "https:"].includes(url.protocol) &&
      !!url.hostname &&
      !url.username &&
      !url.password
    );
  } catch {
    return false;
  }
}

function endpointLabel(value: string | null): string {
  if (!value) return "未提供接口地址";
  try {
    const endpoint = new URL(value);
    if (
      endpoint.username ||
      endpoint.password ||
      endpoint.search ||
      endpoint.hash
    )
      return "接口地址待核对";
    return `${endpoint.origin}${endpoint.pathname.replace(/\/$/, "")}`;
  } catch {
    return "接口地址待核对";
  }
}

export interface ModelSettingsActions {
  edit(profileId: string): boolean;
  editProfile(profile: ModelProfile): boolean;
  add(): boolean;
  test(profileId: string): Promise<unknown>;
}

export type ModelSettingsFocusSection = "generation" | "embedding";

/** What the owner asked for before the fence said no. */
type ModelOpenIntent =
  | { kind: "add" }
  | { kind: "edit"; profile: ModelProfile };

export function ModelSettings({
  controlsRef,
  onProfilesChanged,
  compactSingle = false,
  focusSection,
  onSetupComplete,
}: {
  controlsRef?: Ref<ModelSettingsActions>;
  onProfilesChanged?(): void;
  compactSingle?: boolean;
  focusSection?: ModelSettingsFocusSection;
  onSetupComplete?(): void;
} = {}) {
  const { text, t } = useOnboardingTranslation();
  const fieldId = useId();
  const models = useModelProfiles();
  const embeddingSwitch = useEmbeddingSwitch();
  const [provider, setProvider] = useState<ProviderKind>("openai_compatible");
  const [providerPreset, setProviderPreset] = useState("custom");
  const [advancedEmbedding, setAdvancedEmbedding] = useState(false);
  const [embeddingMode, setEmbeddingMode] = useState<"local" | "custom">(
    "local",
  );
  const [embeddingModeChanged, setEmbeddingModeChanged] = useState(false);
  const [embeddingProvider, setEmbeddingProvider] = useState<
    "openai_compatible" | "ollama"
  >("openai_compatible");
  const [name, setName] = useState("");
  const [nameEdited, setNameEdited] = useState(false);
  const [baseUrl, setBaseUrl] = useState(PROVIDERS[0].baseUrl);
  const [embeddingBaseUrl, setEmbeddingBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [embeddingModel, setEmbeddingModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [disableThinking, setDisableThinking] = useState(true);
  const [technicalOpen, setTechnicalOpen] = useState(true);
  const [timeoutSeconds, setTimeoutSeconds] = useState("");
  const [maxConcurrency, setMaxConcurrency] = useState("");
  const [contextWindow, setContextWindow] = useState("");
  const [customOpen, setCustomOpen] = useState(false);
  const [saved, setSaved] = useState<ModelProfile | null>(null);
  const [editing, setEditing] = useState<ModelProfile | null>(null);
  const [saving, setSaving] = useState(false);
  const [fields, setFields] = useState<Partial<Record<FieldName, string>>>({});
  const [saveError, setSaveError] = useState<string | null>(null);
  const [unknownCreation, setUnknownCreation] = useState(false);
  const [setupPhase, setSetupPhase] = useState<"testing" | "activating" | null>(
    null,
  );
  const [setupError, setSetupError] = useState<string | null>(null);
  const [switchTarget, setSwitchTarget] = useState<ModelProfile | null>(null);
  const [switchJobId, setSwitchJobId] = useState<string | null>(null);
  const [switchRefreshCompleted, setSwitchRefreshCompleted] = useState<
    string | null
  >(null);
  const settledSwitchJob = useRef<string | null>(null);
  const flight = useRef(false);
  const alive = useRef(true);
  const queuedOpen = useRef<ModelOpenIntent | null>(null);
  const savedCard = useRef<HTMLElement>(null);
  const formEl = useRef<HTMLFormElement>(null);
  const modelInput = useRef<HTMLInputElement>(null);
  const technicalTouched = useRef(
    new Set<
      | "timeout_seconds"
      | "max_concurrency"
      | "context_window"
      | "disable_thinking"
    >(),
  );
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    const finished = embeddingSwitch.current?.last_finished;
    if (
      !switchTarget ||
      !switchJobId ||
      !finished ||
      finished.job_id !== switchJobId ||
      settledSwitchJob.current === finished.job_id
    )
      return;
    settledSwitchJob.current = finished.job_id;
    if (finished.state === "done") {
      setSetupPhase("activating");
      setSetupError(null);
      void models.refreshProfiles().then(() => {
        if (alive.current) setSwitchRefreshCompleted(finished.job_id);
      });
      onProfilesChanged?.();
      return;
    }
    if (finished.state === "rolled_back") {
      setSetupError(
        finished.error_code === "embedding_switch_cancelled"
          ? "索引更新已取消，仍在使用原来的搜索模型。"
          : "索引更新没有完成，仍在使用原来的搜索模型，记忆没有丢失。",
      );
      setSwitchTarget(null);
      setSwitchJobId(null);
      setSwitchRefreshCompleted(null);
    }
  }, [
    embeddingSwitch.current?.last_finished,
    models.refreshProfiles,
    onProfilesChanged,
    switchJobId,
    switchTarget,
  ]);
  useEffect(() => {
    const finished = embeddingSwitch.current?.last_finished;
    if (
      !switchTarget ||
      !switchJobId ||
      finished?.job_id !== switchJobId ||
      finished.state !== "done" ||
      switchRefreshCompleted !== switchJobId
    )
      return;
    const active = models.profiles.find(
      (profile) =>
        profile.profile_id === switchTarget.profile_id &&
        profile.revision >= switchTarget.revision &&
        profile.is_active,
    );
    if (!active) {
      setSetupPhase(null);
      setSetupError("配置已保存，尚未确认启用成功。请核对配置后重试。");
      setSwitchTarget(null);
      setSwitchJobId(null);
      setSwitchRefreshCompleted(null);
      return;
    }
    if (active.name !== PRESET_PROFILE_NAME) setSaved(active);
    setSetupPhase(null);
    setSetupError(null);
    setSwitchTarget(null);
    setSwitchJobId(null);
    setSwitchRefreshCompleted(null);
    onSetupComplete?.();
  }, [
    embeddingSwitch.current?.last_finished,
    models.profiles,
    onSetupComplete,
    switchJobId,
    switchRefreshCompleted,
    switchTarget,
  ]);
  useLayoutEffect(() => {
    if (saved) savedCard.current?.focus({ preventScroll: true });
  }, [saved]);
  useLayoutEffect(() => {
    const first = Object.keys(fields).find((key) => fields[key as FieldName]);
    if (!first) return;
    if (
      [
        "model",
        "base_url",
        "api_key",
        "timeout_seconds",
        "max_concurrency",
        "context_window",
      ].includes(first)
    ) {
      if (!technicalOpen && first !== "api_key") {
        setTechnicalOpen(true);
        return;
      }
      formEl.current
        ?.querySelector<HTMLInputElement>(`[name="${first}"]`)
        ?.focus({ preventScroll: true });
    } else if (advancedEmbedding && first.startsWith("embedding-")) {
      formEl.current
        ?.querySelector<HTMLInputElement>(`[name="${first}"]`)
        ?.focus({ preventScroll: true });
    }
  }, [advancedEmbedding, fields, technicalOpen]);
  useEffect(() => {
    if (technicalOpen && !model.trim())
      modelInput.current?.focus({ preventScroll: true });
  }, [technicalOpen, model]);
  const selected =
    PROVIDERS.find((item) => item.kind === provider) ?? PROVIDERS[0];
  const presetProfiles = models.profiles.filter(
    (profile) => profile.name === PRESET_PROFILE_NAME,
  );
  // With no preset there is nothing else to configure with, so the form is
  // the page rather than an alternative to it.
  const formOpen = !saved && (!presetProfiles.length || customOpen);
  const savedProfile =
    saved &&
    (models.profiles.find(
      (item) =>
        item.profile_id === saved.profile_id && item.revision >= saved.revision,
    ) ??
      saved);
  const editorLocked =
    saving ||
    switchJobId !== null ||
    models.busy !== null ||
    !!models.pendingCreation ||
    !!models.pendingProbe ||
    models.probeStorageFailed === true ||
    models.creationStorageFailed === true;
  const locked = editorLocked;
  const ownerLocked = editorLocked;
  const formLocked = ownerLocked;
  const editedCurrent =
    editing &&
    models.profiles.find((item) => item.profile_id === editing.profile_id);
  const editChanged =
    !!editing &&
    (!editedCurrent || editedCurrent.revision !== editing.revision);
  const endpointMoved =
    !!editing &&
    (provider !== editing.provider_kind ||
      endpointKey(baseUrl) !== endpointKey(editing.base_url ?? ""));
  const keyRequired =
    selected.keyRequired &&
    !(
      editing &&
      (editing.has_configured_api_key ?? editing.has_api_key) &&
      !endpointMoved
    );
  const suggestedName = (nextModel: string, nextProvider = provider) =>
    nextModel.trim()
      ? `${providerLabel(nextProvider)} · ${nextModel.trim()}`.slice(0, 128)
      : "";
  const clearInheritedRuntimeForNewProfile = (nextProvider: ProviderKind) => {
    if (!editing || nextProvider === editing.provider_kind) return;
    setTimeoutSeconds("");
    setMaxConcurrency("");
    setContextWindow("");
    setDisableThinking(true);
    technicalTouched.current.clear();
  };
  const isReady = (profile: ModelProfile) =>
    modelProfileReady(
      profile,
      models.probeFor(profile),
      models.probeErrorFor(profile),
    );

  function editProfile(profile: ModelProfile) {
    if (ownerLocked) return;
    setEditing({ ...profile });
    setEmbeddingModeChanged(false);
    setSaved(null);
    setCustomOpen(true);
    setName(profile.name);
    setNameEdited(true);
    setProvider(profile.provider_kind);
    setProviderPreset(presetForGeneration(profile));
    setBaseUrl(profile.base_url ?? "");
    setModel(profile.model);
    setDisableThinking(profile.disable_thinking);
    setTimeoutSeconds(String(profile.timeout_seconds));
    setMaxConcurrency(String(profile.max_concurrency));
    setContextWindow(
      profile.context_window === null ? "" : String(profile.context_window),
    );
    setTechnicalOpen(true);
    technicalTouched.current.clear();
    setApiKey("");
    setAdvancedEmbedding(
      profile.embedding_provider_kind !== BUILTIN_EMBEDDING_KIND &&
        !!profile.embedding_model,
    );
    setEmbeddingMode(
      profile.embedding_provider_kind !== BUILTIN_EMBEDDING_KIND &&
        profile.embedding_model
        ? "custom"
        : "local",
    );
    setEmbeddingProvider(
      profile.embedding_provider_kind === "ollama"
        ? "ollama"
        : "openai_compatible",
    );
    setEmbeddingModel(profile.embedding_model ?? "");
    setEmbeddingBaseUrl(profile.embedding_base_url ?? profile.base_url ?? "");
    setFields({});
    setSaveError(null);
    setSetupError(null);
  }

  function newProfile() {
    if (ownerLocked) return;
    setEditing(null);
    setEmbeddingModeChanged(false);
    setSaved(null);
    setCustomOpen(true);
    setName("");
    setNameEdited(false);
    setProvider("openai_compatible");
    setProviderPreset("custom");
    setBaseUrl(PROVIDERS[0].baseUrl);
    setModel("");
    setApiKey("");
    setDisableThinking(true);
    setTimeoutSeconds("");
    setMaxConcurrency("");
    setContextWindow("");
    setTechnicalOpen(true);
    technicalTouched.current.clear();
    setAdvancedEmbedding(false);
    setEmbeddingMode("local");
    setEmbeddingModel("");
    setEmbeddingBaseUrl("");
    setFields({});
    setSaveError(null);
    setSetupError(null);
  }

  useImperativeHandle(controlsRef, () => ({
    edit(profileId) {
      const profile = models.profiles.find(
        (item) => item.profile_id === profileId,
      );
      if (!profile) return false;
      if (ownerLocked) {
        queuedOpen.current = { kind: "edit", profile };
        return false;
      }
      editProfile(profile);
      return true;
    },
    editProfile(profile) {
      if (ownerLocked) {
        queuedOpen.current = { kind: "edit", profile };
        return false;
      }
      editProfile(profile);
      return true;
    },
    add() {
      if (ownerLocked) {
        queuedOpen.current = { kind: "add" };
        return false;
      }
      newProfile();
      return true;
    },
    async test(profileId) {
      if (
        locked ||
        !models.profiles.some((item) => item.profile_id === profileId)
      )
        throw new Error("model_profile_not_available");
      return models.probeProfile(profileId);
    },
  }));
  // A refused open is not a cancelled one. Replay it once the fence clears so
  // the person lands back in the form they asked for, instead of a blank
  // new-config form that invites a duplicate. Replaying only fills fields in;
  // it never sends a request.
  // biome-ignore lint/correctness/useExhaustiveDependencies: the fence is the only trigger.
  useEffect(() => {
    if (editorLocked) return;
    const intent = queuedOpen.current;
    if (!intent) return;
    queuedOpen.current = null;
    if (intent.kind === "add") newProfile();
    else editProfile(intent.profile);
  }, [editorLocked]);
  const profileSnapshot = JSON.stringify(models.profiles);
  const previousProfileSnapshot = useRef(profileSnapshot);
  useEffect(() => {
    if (previousProfileSnapshot.current === profileSnapshot) return;
    previousProfileSnapshot.current = profileSnapshot;
    onProfilesChanged?.();
  }, [profileSnapshot, onProfilesChanged]);

  async function activateWithPlan(
    profile: ModelProfile,
    result: ModelProbeResult | null,
  ): Promise<boolean> {
    setSetupPhase("activating");
    const switchPlan = embeddingSwitch.shouldUseLegacyActivation()
      ? null
      : await embeddingSwitch.planActivation(
          profile.profile_id,
          profile.revision,
        );
    if (!alive.current) return false;
    if (!switchPlan && !embeddingSwitch.shouldUseLegacyActivation()) {
      setSetupError("暂时无法检查搜索模型更换影响，配置已保存，请稍后重试。");
      return false;
    }
    if (switchPlan?.needed) {
      setSwitchTarget(profile);
      setSetupPhase(null);
      return false;
    }
    const active = await models.activate(profile.profile_id);
    if (!alive.current) return false;
    if (
      !active ||
      active.profile_id !== profile.profile_id ||
      !active.is_active ||
      !modelProfileReady(active, result)
    ) {
      setSetupError("配置已保存，尚未确认启用成功。请核对配置后重试。");
      return false;
    }
    if (profile.name !== PRESET_PROFILE_NAME) setSaved(active);
    onSetupComplete?.();
    return true;
  }

  async function finishSetup(profile: ModelProfile): Promise<boolean> {
    setSetupPhase("testing");
    const result = await models.probeProfile(profile.profile_id);
    if (!alive.current) return false;
    const testedProfile = profileFromProbe(profile, result);
    if (!testedProfile || !modelProfileReady(testedProfile, result ?? null)) {
      setSetupError(
        "配置已保存，连接测试未通过。请核对配置后重试，历史对话尚未发送。",
      );
      setFields(probeFieldErrors(result));
      return false;
    }
    return activateWithPlan(testedProfile, result ?? null);
  }

  async function activateTested(profile: ModelProfile) {
    const result = models.probeFor(profile);
    if (!isReady(profile) || flight.current || locked) return;
    flight.current = true;
    setSaving(true);
    setSetupError(null);
    try {
      await activateWithPlan(profile, result);
    } finally {
      flight.current = false;
      if (alive.current) {
        setSaving(false);
        setSetupPhase(null);
      }
    }
  }

  async function resumeSetup(profile: ModelProfile, key?: string) {
    if (flight.current || locked) return;
    flight.current = true;
    setSaving(true);
    setSetupError(null);
    try {
      const updated =
        key === undefined
          ? profile
          : await models.update(profile.profile_id, {
              expected_revision: profile.revision,
              api_key: key,
            });
      if (!alive.current || !updated) return;
      await finishSetup(updated);
      return updated;
    } catch {
      if (alive.current)
        setSetupError("配置尚未完成测试和启用，请核对后重试。");
    } finally {
      flight.current = false;
      if (alive.current) {
        setSaving(false);
        setSetupPhase(null);
      }
    }
  }

  /**
   * Saving the key without testing it. The preset card's own submit, kept
   * apart from `resumeSetup` because a save that reaches out to the endpoint
   * is a different act from a save that does not, and only the user gets to
   * decide which one they are performing.
   */
  async function saveKey(profile: ModelProfile, key: string) {
    if (flight.current || locked) return;
    flight.current = true;
    setSaving(true);
    setSetupError(null);
    try {
      return await models.update(profile.profile_id, {
        expected_revision: profile.revision,
        api_key: key,
      });
    } finally {
      flight.current = false;
      if (alive.current) setSaving(false);
    }
  }

  /**
   * `thenSetup` is the whole difference between the form's two buttons.
   *
   * Plain save is the default, including the Enter key, because testing sends
   * a request to a third-party endpoint and activation cancels queued work
   * bound to the profile being replaced (`memory_processing` drops tasks whose
   * snapshot no longer matches the active profile). Neither belongs to a
   * keystroke the user reads as "write this down".
   */
  async function persist(thenSetup: boolean) {
    if (
      flight.current ||
      locked ||
      editChanged ||
      (!editing && unknownCreation)
    )
      return;
    const errors: Partial<Record<FieldName, string>> = {};
    if (focusSection !== "embedding") {
      const generationErrors = validateGenerationModel(
        {
          provider_kind: provider,
          model,
          base_url: baseUrl,
          timeout_seconds: timeoutSeconds,
          max_concurrency: maxConcurrency,
          context_window: contextWindow,
          disable_thinking: disableThinking,
        },
        { apiKey, keyRequired },
      );
      if (generationErrors.model) errors.model = "请填写生成模型名。";
      if (generationErrors.base_url)
        errors.base_url = "请填写完整的 http:// 或 https:// 接口地址。";
      if (generationErrors.api_key) errors.api_key = generationErrors.api_key;
      if (generationErrors.timeout_seconds)
        errors.timeout_seconds = t("onboardingIntegerRange", {
          min: 1,
          max: 600,
        });
      if (generationErrors.max_concurrency)
        errors.max_concurrency = t("onboardingIntegerRange", {
          min: 1,
          max: 32,
        });
      if (generationErrors.context_window)
        errors.context_window = t("onboardingIntegerRange", {
          min: 1024,
          max: 10_000_000,
        });
    }
    if (focusSection !== "generation" && embeddingMode === "custom") {
      if (!embeddingModel.trim())
        errors["embedding-model"] = "请填写向量模型名。";
      if (!validEndpoint(embeddingBaseUrl))
        errors["embedding-base-url"] = "请填写完整的向量模型接口地址。";
    }
    setFields(errors);
    setSaveError(null);
    setSetupError(null);
    const first = Object.keys(errors)[0];
    if (first) {
      if (
        [
          "model",
          "base_url",
          "timeout_seconds",
          "max_concurrency",
          "context_window",
        ].includes(first) &&
        !technicalOpen
      ) {
        setTechnicalOpen(true);
        return;
      }
      if (first.startsWith("embedding-") && !advancedEmbedding) {
        setAdvancedEmbedding(true);
        return;
      }
      formEl.current
        ?.querySelector<HTMLInputElement>(`[name="${first}"]`)
        ?.focus();
      return;
    }
    flight.current = true;
    setSaving(true);
    const input: ModelProfileInput = {
      name: name.trim() || suggestedName(model),
      provider_kind: provider,
      model,
      disable_thinking: disableThinking,
      ...(embeddingMode === "custom"
        ? {
            embedding_model: embeddingModel,
            embedding_provider_kind: embeddingProvider,
            embedding_base_url: embeddingBaseUrl,
          }
        : {}),
      ...(provider === "hy_default" ? {} : { base_url: baseUrl }),
      ...(apiKey ? { api_key: apiKey } : {}),
    };
    if (
      technicalTouched.current.has("timeout_seconds") &&
      timeoutSeconds.trim()
    )
      input.timeout_seconds = Number(timeoutSeconds);
    if (
      technicalTouched.current.has("max_concurrency") &&
      maxConcurrency.trim()
    )
      input.max_concurrency = Number(maxConcurrency);
    if (technicalTouched.current.has("context_window"))
      input.context_window = contextWindow.trim()
        ? Number(contextWindow)
        : null;
    try {
      const patch: ModelProfilePatch = editing
        ? { expected_revision: editing.revision }
        : { expected_revision: 0 };
      if (editing) {
        if (focusSection !== "embedding") {
          if (input.name !== editing.name) patch.name = input.name;
          if (input.model !== editing.model) patch.model = input.model;
          if (input.base_url !== editing.base_url)
            patch.base_url = input.base_url;
          if (input.disable_thinking !== editing.disable_thinking)
            patch.disable_thinking = input.disable_thinking;
          if (apiKey) patch.api_key = apiKey;
          if (
            technicalTouched.current.has("timeout_seconds") &&
            input.timeout_seconds !== editing.timeout_seconds
          )
            patch.timeout_seconds = input.timeout_seconds;
          if (
            technicalTouched.current.has("max_concurrency") &&
            input.max_concurrency !== editing.max_concurrency
          )
            patch.max_concurrency = input.max_concurrency;
          if (
            technicalTouched.current.has("context_window") &&
            input.context_window !== editing.context_window
          )
            patch.context_window = input.context_window;
        }
        if (focusSection !== "generation" && embeddingMode === "custom") {
          if (embeddingModel !== editing.embedding_model)
            patch.embedding_model = embeddingModel;
          if (embeddingProvider !== editing.embedding_provider_kind)
            patch.embedding_provider_kind = embeddingProvider;
          // Against the stored value only: the field is prefilled with the
          // generation endpoint, and Core no longer falls back to it.
          if (embeddingBaseUrl !== (editing.embedding_base_url ?? ""))
            patch.embedding_base_url = embeddingBaseUrl;
        } else if (
          focusSection !== "generation" &&
          embeddingModeChanged &&
          editing.embedding_provider_kind !== BUILTIN_EMBEDDING_KIND
        ) {
          // Core clears the remote endpoint when local_onnx is selected.
          patch.embedding_provider_kind = BUILTIN_EMBEDDING_KIND;
          patch.embedding_model = "bge-small-zh-v1.5";
          patch.embedding_dimensions = 512;
        }
      }
      const created =
        editing && provider === editing.provider_kind
          ? await models.update(editing.profile_id, patch)
          : await models.create(input);
      if (!alive.current) return;
      if (created) {
        if (thenSetup) {
          // Retrying after probe, activation, or switch confirmation failed
          // must update this saved revision instead of creating a duplicate.
          setEditing(created);
          // The probe decides whether this form closes. A rejected key or a
          // failed endpoint leaves the filled-in form on screen with the
          // reason; only a passing test returns to the saved card.
          const passed = await finishSetup(created);
          if (!alive.current) return;
          if (passed) {
            setSaved(created);
            setEditing(null);
            setCustomOpen(false);
            setApiKey("");
          }
        } else {
          setSaved(created);
          setEditing(null);
          setCustomOpen(false);
          setApiKey("");
        }
      } else {
        setSaveError(
          created === null
            ? "配置未保存，请检查模型地址和参数后重试；已填写内容保留。"
            : "配置尚未保存成功，已填写内容保留，可核对后重试。",
        );
      }
    } catch {
      if (alive.current) {
        setSaveError("配置尚未保存成功，已填写内容保留，可核对后重试。");
      }
    } finally {
      flight.current = false;
      if (alive.current) {
        setSaving(false);
        setSetupPhase(null);
      }
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void persist(false);
  }

  return (
    <section className="model-settings" aria-labelledby="models-title">
      <div className="section-heading">
        <div>
          <p className="section-index">02</p>
          <h2 id="models-title">
            {text(
              focusSection === "generation"
                ? "记忆整理"
                : focusSection === "embedding"
                  ? "记忆搜索"
                  : "记忆模型",
            )}
          </h2>
        </div>
        <p>
          {text(
            focusSection === "generation"
              ? "配置用于理解会话、整理记忆的模型。"
              : focusSection === "embedding"
                ? "推荐使用本地内置搜索；需要连接自定义服务时再展开配置。"
                : "只需配置生成模型。向量嵌入已内置在本机，不为 System 1、System 2 分开选模型。",
          )}
        </p>
      </div>

      {models.error ? (
        <p className="error-banner" role="alert">
          {text(models.error)}
        </p>
      ) : null}
      {(models.pendingProbe || models.probeStorageFailed) && (
        <div role="status" className="model-refresh-notice">
          <p>
            {text(
              models.probeStorageFailed
                ? "无法读取测试恢复记录，暂不发送新的测试。"
                : "上次模型测试结果待核对，暂不修改或启用配置。",
            )}
          </p>
          {models.pendingProbe && (
            <button
              type="button"
              disabled={
                models.busy !== null || saving || models.probeStorageFailed
              }
              onClick={async () => {
                const pending = models.pendingProbe;
                if (!pending) return;
                await models.probeProfile(pending.profileId);
                if (alive.current) onProfilesChanged?.();
              }}
            >
              {text("核对测试结果")}
            </button>
          )}
        </div>
      )}
      {(models.pendingCreation || models.creationStorageFailed) && (
        <div role="status" className="model-refresh-notice">
          <p>
            {text(
              models.creationMissing
                ? "未找到上次创建记录。确认放弃旧记录后可重新配置；系统不会自动重试。"
                : "上次模型创建结果待核对，暂不开始新的操作。",
            )}
          </p>
          <button
            type="button"
            disabled={models.busy !== null || saving}
            onClick={async () => {
              const recovered = await models.reconcileCreation();
              if (!alive.current || recovered === undefined) return;
              setUnknownCreation(false);
              setSaveError(null);
              setEditing(null);
              setApiKey("");
              setSaved(recovered);
              onProfilesChanged?.();
            }}
          >
            {text("核对创建结果")}
          </button>
          {models.pendingCreation && (
            <button
              type="button"
              className="secondary"
              disabled={models.busy !== null || saving}
              onClick={async () => {
                if (!(await models.discardMissingCreation()) || !alive.current)
                  return;
                setUnknownCreation(false);
                setSaveError(null);
              }}
            >
              {text("放弃旧记录，重新配置")}
            </button>
          )}
        </div>
      )}
      {setupPhase && (
        <p className="model-test-notice" role="status">
          {text(
            setupPhase === "testing"
              ? "配置已保存，正在测试连接…"
              : "连接通过，正在启用模型…",
          )}
        </p>
      )}
      {setupError && (
        <p className="model-save-error" role="alert">
          {text(setupError)}
        </p>
      )}
      {models.refreshError && (
        <R6StateRecovery
          message={text(models.refreshError)}
          pending={models.refreshing}
          role="status"
          onRetry={() => void models.refreshProfiles()}
        />
      )}

      {/* The paste-one-key path goes first when it exists. Behind it sits the
       * full form, which asks for a protocol, a base URL, a timeout and a
       * concurrency limit — none of which the person with a preset needs to
       * have an opinion about. */}
      {!compactSingle && presetProfiles.length ? (
        <div className="profile-list preset-first" aria-live="polite">
          {presetProfiles.map((profile) => (
            <PresetProfileCard
              key={profile.profile_id}
              profile={profile}
              busy={locked}
              onSaveKey={(key) => saveKey(profile, key)}
              onSaveKeyAndSetup={(key) => resumeSetup(profile, key)}
              onContinue={() => resumeSetup(profile)}
              onTest={() => models.probeProfile(profile.profile_id)}
              onActivate={() => models.activate(profile.profile_id)}
              onRemove={() => models.remove(profile.profile_id)}
              probe={models.probeFor(profile)}
              probeError={models.probeErrorFor(profile)}
            />
          ))}
          <button
            type="button"
            className="quiet"
            aria-expanded={customOpen}
            disabled={locked}
            onClick={() => {
              if (customOpen) {
                setEditing(null);
                setCustomOpen(false);
                setApiKey("");
              } else newProfile();
            }}
          >
            {text("自定义一个模型")}
          </button>
        </div>
      ) : null}

      {!compactSingle && savedProfile && (
        <article
          className="model-saved-card"
          aria-label={text("已保存的模型配置")}
          tabIndex={-1}
          ref={savedCard}
          data-tour-id="model-form"
        >
          <div className="model-saved-heading">
            <Check size={18} aria-hidden="true" />
            <strong>
              {text(
                isReady(savedProfile) && savedProfile.is_active
                  ? "已保存，已启用"
                  : isReady(savedProfile)
                    ? "已保存，连接通过"
                    : models.probeFor(savedProfile)
                      ? "已保存，测试未通过"
                      : "已保存，待测试",
              )}
            </strong>
          </div>
          <h3>{savedProfile.name}</h3>
          <p>
            {generationSummary(
              presetForGeneration(savedProfile),
              savedProfile.provider_kind,
              savedProfile.model,
              text,
            )}{" "}
            · {text(endpointLabel(savedProfile.base_url))} · {text("版本")}{" "}
            {savedProfile.revision}
          </p>
          {savedProfile.credential_state === "unavailable" && (
            <p role="status">
              {text(
                "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”；已保存的配置不会丢失。",
              )}
            </p>
          )}
          <p>{text("测试只向此接口发送测试内容，不发送历史对话。")}</p>
          <div className="source-actions">
            <button
              type="button"
              disabled={locked}
              onClick={() => void resumeSetup(savedProfile)}
            >
              {text(setupError ? "重试测试并启用" : "测试并启用")}
            </button>
            <button
              type="button"
              className="secondary"
              disabled={locked}
              onClick={() => editProfile(savedProfile)}
            >
              <Pencil size={16} aria-hidden="true" />
              {text("编辑配置")}
            </button>
            <button
              type="button"
              className="quiet"
              disabled={locked}
              onClick={newProfile}
            >
              <Plus size={16} aria-hidden="true" />
              {text("新增配置")}
            </button>
          </div>
          <ProbeFeedback
            profile={savedProfile}
            result={models.probeFor(savedProfile)}
            error={models.probeErrorFor(savedProfile)}
          />
        </article>
      )}

      {formOpen ? (
        <form
          className="model-form"
          data-tour-id="model-form"
          ref={formEl}
          onSubmit={submit}
          noValidate
          aria-busy={saving}
        >
          {editing && (
            <p className="model-test-notice">
              {text("编辑")} {editing.name} · {text("版本")} {editing.revision}
              {text("。修改后需重新测试并设为当前模型，内容授权不会自动更新。")}
            </p>
          )}
          {editChanged && (
            <p role="alert" className="model-save-error">
              {text(
                "配置已在其他位置变化，草稿已保留。请取消编辑后重新打开核对。",
              )}
            </p>
          )}
          {saveError && (
            <p className="model-save-error" role="alert">
              {text(saveError)}
            </p>
          )}
          {unknownCreation && !editing && (
            <button
              type="button"
              disabled={formLocked}
              onClick={() => void models.refreshProfiles()}
            >
              {text("刷新配置列表")}
            </button>
          )}
          {focusSection !== "embedding" && (
            <>
              <div className="model-form-field">
                {text("服务商预设")}
                <ProviderPresetField
                  ariaLabel={text("服务商预设")}
                  describedBy="model-preset-help"
                  value={providerPreset}
                  disabled={formLocked}
                  text={text}
                  onChange={(value) => {
                    const next = PROVIDER_PRESETS.find(
                      (item) => item.id === value,
                    );
                    setProviderPreset(value);
                    if (!next) {
                      clearInheritedRuntimeForNewProfile("openai_compatible");
                      setProvider("openai_compatible");
                      setBaseUrl("");
                      setModel("");
                      setApiKey("");
                      setTechnicalOpen(true);
                      if (!nameEdited) setName("");
                      return;
                    }
                    clearInheritedRuntimeForNewProfile(next.provider);
                    setProvider(next.provider);
                    setBaseUrl(next.baseUrl);
                    setModel(next.model);
                    setApiKey("");
                    setDisableThinking(true);
                    setTechnicalOpen(true);
                    if (editing) {
                      setName(next.label);
                      setNameEdited(true);
                    } else if (!nameEdited) {
                      setName(suggestedName(next.model, next.provider));
                    }
                    setFields({});
                    setSaveError(null);
                  }}
                />
                <small id="model-preset-help">
                  {text(
                    "只是帮你填好接口地址和一个模型名，不代表这个模型当前可用。 请对照服务商的模型列表核对后再保存。",
                  )}
                </small>
              </div>
              <label>
                {text("配置名称")}
                <input
                  name="name"
                  aria-label={text("配置名称")}
                  aria-describedby="model-name-help"
                  autoComplete="off"
                  maxLength={128}
                  value={name}
                  disabled={formLocked}
                  onChange={(event) => {
                    setNameEdited(true);
                    setName(event.target.value);
                  }}
                />
                <small id="model-name-help">
                  {text("用于区分配置，不影响模型请求。")}
                </small>
              </label>
              <GenerationTechnicalDisclosure
                open={technicalOpen}
                disabled={formLocked}
                controls="model-generation-technical"
                text={text}
                onChange={setTechnicalOpen}
              >
                <GenerationTechnicalFields
                  idPrefix="model-generation"
                  values={{
                    provider_kind: provider,
                    model,
                    base_url: baseUrl,
                    timeout_seconds: timeoutSeconds,
                    max_concurrency: maxConcurrency,
                    context_window: contextWindow,
                    disable_thinking: disableThinking,
                  }}
                  errors={{
                    model: fields.model,
                    base_url: fields.base_url,
                    timeout_seconds: fields.timeout_seconds,
                    max_concurrency: fields.max_concurrency,
                    context_window: fields.context_window,
                  }}
                  disabled={formLocked}
                  text={text}
                  modelRef={modelInput}
                  onChange={(patch: Partial<GenerationModelValues>) => {
                    if (patch.provider_kind !== undefined) {
                      clearInheritedRuntimeForNewProfile(patch.provider_kind);
                      setProvider(patch.provider_kind);
                      setProviderPreset("custom");
                      if (!nameEdited)
                        setName(suggestedName(model, patch.provider_kind));
                    }
                    if (patch.model !== undefined) {
                      setModel(patch.model);
                      setProviderPreset("custom");
                      if (!nameEdited) setName(suggestedName(patch.model));
                    }
                    if (patch.base_url !== undefined) {
                      setBaseUrl(patch.base_url);
                      setProviderPreset("custom");
                    }
                    if (patch.timeout_seconds !== undefined) {
                      setTimeoutSeconds(patch.timeout_seconds);
                      technicalTouched.current.add("timeout_seconds");
                    }
                    if (patch.max_concurrency !== undefined) {
                      setMaxConcurrency(patch.max_concurrency);
                      technicalTouched.current.add("max_concurrency");
                    }
                    if (patch.context_window !== undefined) {
                      setContextWindow(patch.context_window);
                      technicalTouched.current.add("context_window");
                    }
                    if (patch.disable_thinking !== undefined) {
                      setDisableThinking(patch.disable_thinking);
                      technicalTouched.current.add("disable_thinking");
                    }
                  }}
                />
              </GenerationTechnicalDisclosure>
            </>
          )}
          {/* Disclosure only changes visibility; mode determines what is saved. */}
          {focusSection !== "generation" && (
            <div className="model-advanced">
              {focusSection === "embedding" && (
                <div className="model-search-intro">
                  <strong>{text("搜索方式")}</strong>
                  <p>
                    {text(
                      "本地内置适合大多数人：无需密钥，内容不会发送到远程向量服务。",
                    )}
                  </p>
                </div>
              )}
              {focusSection !== "embedding" && (
                <button
                  type="button"
                  className="quiet"
                  aria-expanded={advancedEmbedding}
                  disabled={formLocked}
                  onClick={() => setAdvancedEmbedding(!advancedEmbedding)}
                >
                  {text("高级：自行配置向量模型")}
                </button>
              )}
              {focusSection === "embedding" || advancedEmbedding ? (
                <>
                  <div className="model-form-field">
                    <label
                      className="model-option-label"
                      id={`${fieldId}-embedding-mode-label`}
                      htmlFor={`${fieldId}-embedding-mode`}
                    >
                      {text(
                        focusSection === "embedding"
                          ? "搜索模型"
                          : "向量配置模式",
                      )}
                    </label>
                    <OptionMenu
                      id={`${fieldId}-embedding-mode`}
                      labelledBy={`${fieldId}-embedding-mode-label`}
                      value={embeddingMode}
                      disabled={formLocked}
                      className="model-option-menu"
                      panelClassName="model-option-menu-panel"
                      options={[
                        {
                          value: "local",
                          label: text("本地内置（推荐）"),
                        },
                        {
                          value: "custom",
                          label: text("自定义搜索模型"),
                        },
                      ]}
                      onChange={(value) => {
                        setEmbeddingMode(value as "local" | "custom");
                        setEmbeddingModeChanged(true);
                        setFields((previous) => ({
                          ...previous,
                          "embedding-model": undefined,
                          "embedding-base-url": undefined,
                        }));
                      }}
                    />
                  </div>
                  <small>{text("技术类型：Embedding")}</small>
                  {embeddingMode === "custom" ? (
                    <>
                      <label>
                        {text("向量模型名")}
                        <input
                          name="embedding-model"
                          autoComplete="off"
                          required
                          maxLength={256}
                          value={embeddingModel}
                          disabled={formLocked}
                          aria-invalid={!!fields["embedding-model"]}
                          aria-describedby={
                            fields["embedding-model"]
                              ? "model-embedding-error"
                              : undefined
                          }
                          onChange={(event) =>
                            setEmbeddingModel(event.target.value)
                          }
                          placeholder={text("例如：text-embedding-3-small…")}
                        />
                        {fields["embedding-model"] && (
                          <small
                            id="model-embedding-error"
                            className="model-field-error"
                          >
                            {text(fields["embedding-model"])}
                          </small>
                        )}
                      </label>
                      <div className="model-form-field">
                        <label
                          className="model-option-label"
                          id={`${fieldId}-embedding-provider-label`}
                          htmlFor={`${fieldId}-embedding-provider`}
                        >
                          {text("向量模型来源")}
                        </label>
                        <OptionMenu
                          id={`${fieldId}-embedding-provider`}
                          labelledBy={`${fieldId}-embedding-provider-label`}
                          value={embeddingProvider}
                          disabled={formLocked}
                          className="model-option-menu"
                          panelClassName="model-option-menu-panel"
                          options={[
                            { value: "ollama", label: text("本机 Ollama") },
                            {
                              value: "openai_compatible",
                              label: text(
                                "同源 OpenAI-compatible（共用 API Key）",
                              ),
                            },
                          ]}
                          onChange={(value) => {
                            const next = value as
                              | "openai_compatible"
                              | "ollama";
                            setEmbeddingProvider(next);
                          }}
                        />
                      </div>
                      <label>
                        {text("向量模型 Base URL")}
                        <input
                          name="embedding-base-url"
                          type="url"
                          inputMode="url"
                          autoComplete="off"
                          required
                          value={embeddingBaseUrl}
                          disabled={formLocked}
                          placeholder={
                            embeddingProvider === "ollama"
                              ? "http://127.0.0.1:11434"
                              : baseUrl
                          }
                          aria-invalid={!!fields["embedding-base-url"]}
                          aria-describedby={
                            fields["embedding-base-url"]
                              ? "model-embedding-url-error"
                              : undefined
                          }
                          onChange={(event) =>
                            setEmbeddingBaseUrl(event.target.value)
                          }
                        />
                        {fields["embedding-base-url"] && (
                          <small
                            id="model-embedding-url-error"
                            className="model-field-error"
                          >
                            {text(fields["embedding-base-url"])}
                          </small>
                        )}
                      </label>
                      {embeddingProvider === "openai_compatible" ? (
                        <small>
                          {text("为避免凭据外泄，向量地址必须与生成模型同源。")}
                        </small>
                      ) : null}
                    </>
                  ) : (
                    <small>
                      {text(
                        "使用内置本地向量模型；不会发送内容给远程向量服务。",
                      )}
                    </small>
                  )}
                </>
              ) : null}
              {focusSection !== "embedding" &&
              !advancedEmbedding &&
              embeddingMode === "custom" ? (
                <p className="model-help">
                  {text("自定义向量")}: {embeddingModel || text("尚未填写模型")}{" "}
                  · {embeddingBaseUrl || text("尚未填写地址")}
                </p>
              ) : null}
            </div>
          )}
          {focusSection !== "embedding" && (
            <>
              <label>
                API Key{keyRequired ? "" : text("（可选）")}
                <input
                  name="api_key"
                  aria-label={`API Key${keyRequired ? "" : text("（可选）")}`}
                  aria-describedby={`model-key-help${fields.api_key ? " model-key-error" : ""}`}
                  aria-invalid={!!fields.api_key}
                  type="password"
                  autoComplete="off"
                  required={keyRequired}
                  value={apiKey}
                  disabled={formLocked}
                  onChange={(event) => setApiKey(event.target.value)}
                  placeholder={text("保存后不会再次显示…")}
                />
                <small id="model-key-help">
                  {text(
                    editing && !endpointMoved
                      ? "留空保留已保存密钥；填写新密钥后需重新测试和启用。"
                      : editing && keyRequired
                        ? "换了服务商后需要填写新的 API Key，已保存的密钥不会带到新地址。"
                        : "保存在系统安全存储中，不随安装包分发。",
                  )}
                </small>
                {fields.api_key && (
                  <small id="model-key-error" className="model-field-error">
                    {text(fields.api_key)}
                  </small>
                )}
              </label>
            </>
          )}
          <div className="form-submit">
            {!compactSingle && (
              <button
                type="submit"
                disabled={
                  locked || editChanged || (!editing && unknownCreation)
                }
              >
                {text(editing ? "保存修改" : "保存配置")}
              </button>
            )}
            <button
              type="button"
              disabled={locked || editChanged || (!editing && unknownCreation)}
              onClick={() => void persist(true)}
            >
              {text(
                setupPhase === "testing"
                  ? "正在测试连接…"
                  : setupPhase === "activating"
                    ? "正在启用配置…"
                    : compactSingle
                      ? "测试并完成配置"
                      : "保存并测试启用",
              )}
            </button>
            {editing && (
              <button
                type="button"
                className="secondary"
                disabled={formLocked}
                onClick={() => {
                  setSaved(editedCurrent ?? editing);
                  setEditing(null);
                  setApiKey("");
                  setSaveError(null);
                  setFields({});
                }}
              >
                {text("取消编辑")}
              </button>
            )}
            <small>
              {text("密钥保存在系统安全存储；测试仅发送测试内容。")}
            </small>
          </div>
        </form>
      ) : null}

      {!compactSingle && (
        <div className="profile-list" aria-live="polite">
          {models.profiles
            .filter(
              (profile) =>
                profile.name !== PRESET_PROFILE_NAME &&
                profile.profile_id !== savedProfile?.profile_id,
            )
            .map((profile) => (
              <article key={profile.profile_id} className="profile-row">
                {profile.credential_state === "unavailable" && (
                  <p className="error-banner" role="status">
                    {text(
                      "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”；已保存的配置不会丢失。",
                    )}
                  </p>
                )}
                <div>
                  <strong>{profile.name}</strong>
                  <p>
                    {generationSummary(
                      presetForGeneration(profile),
                      profile.provider_kind,
                      profile.model,
                      text,
                    )}{" "}
                    ·
                    {/* Core fills in the built-in vector model on every profile,
                     * so printing it unconditionally put a line about
                     * bge-small-zh-v1.5 under a profile whose owner never chose
                     * it. Only a vector model the user went out of their way to
                     * set is worth the room. */}
                    {profile.embedding_model &&
                    profile.embedding_provider_kind &&
                    profile.embedding_provider_kind !== BUILTIN_EMBEDDING_KIND
                      ? ` ${text("自定义向量")}: ${profile.embedding_model} (${text(
                          providerLabel(profile.embedding_provider_kind),
                        )}) ·`
                      : ""}{" "}
                    revision {profile.revision}
                  </p>
                </div>
                <span>{text(profile.is_active ? "当前使用" : "未启用")}</span>
                <p className="model-test-notice">
                  {text("测试目标")}: {text(endpointLabel(profile.base_url))}
                  {text("。仅发送测试内容，不发送历史对话。")}
                </p>
                <div className="source-actions">
                  <button
                    type="button"
                    disabled={locked}
                    onClick={() => void resumeSetup(profile)}
                  >
                    {text(setupError ? "重试测试并启用" : "测试并启用")}
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={locked}
                    onClick={() => editProfile(profile)}
                  >
                    <Pencil size={16} aria-hidden="true" />
                    {text("编辑配置")}
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={locked}
                    onClick={() => models.probeProfile(profile.profile_id)}
                  >
                    {text("测试连接")}
                  </button>
                  {!profile.is_active ? (
                    <>
                      <button
                        type="button"
                        disabled={locked || !isReady(profile)}
                        onClick={() => void activateTested(profile)}
                      >
                        {text("设为当前模型")}
                      </button>
                      <button
                        type="button"
                        className="quiet"
                        disabled={locked}
                        onClick={() => models.remove(profile.profile_id)}
                      >
                        {text("删除")}
                      </button>
                    </>
                  ) : null}
                </div>
                <ProbeFeedback
                  profile={profile}
                  result={models.probeFor(profile)}
                  error={models.probeErrorFor(profile)}
                />
              </article>
            ))}
        </div>
      )}
      {embeddingSwitch.plan?.needed && switchTarget ? (
        <EmbeddingSwitchConfirmDialog
          plan={embeddingSwitch.plan}
          busy={embeddingSwitch.busy === "start"}
          errorCode={embeddingSwitch.errorCode}
          onCancel={() => {
            embeddingSwitch.clearPlan();
            embeddingSwitch.clearError();
            setSwitchTarget(null);
            setSwitchJobId(null);
            setSwitchRefreshCompleted(null);
          }}
          onStart={() => {
            void embeddingSwitch.startPlannedSwitch().then((job) => {
              if (!job || !alive.current) return;
              setSwitchJobId(job.job_id);
              setSwitchRefreshCompleted(null);
              setSetupError(null);
            });
          }}
        />
      ) : null}
    </section>
  );
}

/**
 * The profile the build already filled in, showing only the field it could not.
 *
 * Base URL and model name are deliberately not on the surface. A user who can
 * see them assumes they are things to decide, and the whole point of a preset
 * is that they are not.
 */
function PresetProfileCard({
  profile,
  busy,
  canEnable = true,
  discloseRecipients = false,
  onSaveKey,
  onSaveKeyAndSetup,
  onContinue,
  onTest,
  onActivate,
  onRemove,
  probe,
  probeError,
}: {
  profile: ModelProfile;
  busy: boolean;
  canEnable?: boolean;
  discloseRecipients?: boolean;
  onSaveKey: (key: string) => Promise<unknown>;
  onSaveKeyAndSetup: (key: string) => Promise<unknown>;
  onContinue: () => Promise<unknown>;
  onTest: () => Promise<unknown>;
  onActivate: () => Promise<unknown>;
  onRemove: () => void;
  probe: ModelProbeResult | null;
  probeError: string | null;
}) {
  const { text } = useOnboardingTranslation();
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const flight = useRef(false);
  const alive = useRef(true);
  const keyForm = useRef<HTMLFormElement>(null);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const keyLabel = presetKeyLabel();
  const applyHref = applyUrl(profile.base_url);

  /**
   * Submitting stores the key and stops there; the second button is the one
   * that goes on to contact the endpoint. Same split as the full form, for the
   * same reason — pasting a secret and spending it are separate decisions.
   */
  async function save(thenSetup: boolean) {
    if (busy || flight.current) return;
    const invalid = keyError(key, true);
    setError(invalid ?? null);
    if (invalid) {
      keyForm.current?.querySelector<HTMLInputElement>("input")?.focus();
      return;
    }
    flight.current = true;
    setSaving(true);
    try {
      const saved = await (thenSetup ? onSaveKeyAndSetup(key) : onSaveKey(key));
      if (alive.current) {
        if (saved) setKey("");
        else setError("密钥尚未保存成功，已填写内容保留，可重试。");
      }
    } catch {
      if (alive.current) setError("密钥尚未保存成功，已填写内容保留，可重试。");
    } finally {
      flight.current = false;
      if (alive.current) setSaving(false);
    }
  }

  return (
    <section className="preset-profile" aria-label={profile.name}>
      <div>
        <strong>{profile.name}</strong>
        <p>{text(presetStatus(profile))}</p>
      </div>
      {discloseRecipients && (
        <p>
          {text("生成接收方")}: {text(endpointLabel(profile.base_url))};{" "}
          {text("向量接收方")}:
          {profile.embedding_provider_kind === BUILTIN_EMBEDDING_KIND
            ? text("本机")
            : text(
                endpointLabel(profile.embedding_base_url ?? profile.base_url),
              )}
          {text("。启用此配置后按上方选择处理历史及未来允许来源。")}
        </p>
      )}
      {profile.credential_state === "unavailable" && (
        <p role="status">
          {text(
            "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”；已保存的配置不会丢失。",
          )}
        </p>
      )}
      {profile.has_api_key ? null : (
        <form
          className="preset-key"
          ref={keyForm}
          onSubmit={(event) => {
            event.preventDefault();
            void save(false);
          }}
          noValidate
        >
          <label>
            {text(keyLabel)}
            <input
              name="preset-api-key"
              type="password"
              autoComplete="off"
              required
              disabled={busy || saving}
              aria-invalid={!!error}
              aria-describedby={
                error ? `preset-key-error-${profile.profile_id}` : undefined
              }
              value={key}
              onChange={(event) => setKey(event.target.value)}
              placeholder={text("保存后不会再次显示…")}
            />
            {error && (
              <small
                id={`preset-key-error-${profile.profile_id}`}
                className="model-field-error"
                role="alert"
              >
                {text(error)}
              </small>
            )}
          </label>
          <button type="submit" disabled={busy || saving}>
            {text("保存 key")}
          </button>
          <button
            type="button"
            className="secondary"
            disabled={busy || saving || !canEnable}
            onClick={() => void save(true)}
          >
            {text("保存并测试启用")}
          </button>
          {applyHref ? (
            <a href={applyHref} target="_blank" rel="noreferrer">
              {text("还没有 key？去申请")}
            </a>
          ) : null}
        </form>
      )}
      <ProbeFeedback profile={profile} result={probe} error={probeError} />
      {profile.has_api_key && !profile.is_active ? (
        <div className="source-actions">
          <button
            type="button"
            disabled={busy || !canEnable}
            onClick={onContinue}
          >
            {text("测试并启用")}
          </button>
          <p className="model-test-notice">
            {text("测试目标")}: {text(endpointLabel(profile.base_url))}
            {text("。仅发送测试内容，不发送历史对话。")}
          </p>
          <button type="button" disabled={busy} onClick={onTest}>
            {text("测试连接")}
          </button>
          <button
            type="button"
            disabled={
              busy ||
              !canEnable ||
              !modelProfileReady(profile, probe, probeError)
            }
            onClick={onActivate}
          >
            {text("设为当前模型")}
          </button>
          <button
            type="button"
            className="quiet"
            disabled={busy}
            onClick={onRemove}
          >
            {text("删除")}
          </button>
        </div>
      ) : null}
      {profile.has_api_key ? null : (
        // Same container as the other case. On its own it rendered centred
        // and unattached, reading as a stray control rather than this card's
        // action. It is a real one: a build that ships a preset nobody here
        // can use should be possible to clear away, and the seed is claimed
        // once so it does not come back.
        <div className="source-actions">
          <button
            type="button"
            className="quiet"
            disabled={busy}
            onClick={onRemove}
          >
            {text("删除这张卡")}
          </button>
        </div>
      )}
    </section>
  );
}

/**
 * Waiting for a key and configured wrongly read as the same thing to a user
 * unless they are worded apart. Told apart the only way the contract allows:
 * a profile with no key cannot have been probed, so there is nothing to
 * diagnose yet — only something to paste.
 */
function presetStatus(profile: ModelProfile): string {
  if (profile.credential_state === "unavailable")
    return "密钥已配置，当前读取状态待确认。";
  if (!profile.has_api_key) {
    return "等待 key：其余都已经填好了，贴上 key 就能用。";
  }
  if (profile.is_active) {
    return "当前使用";
  }
  if (!profile.capabilities.model_available) {
    return "key 已保存，还没测过这个模型能不能用。";
  }
  return "未启用";
}

function ProbeFeedback({
  profile,
  result,
  error,
}: {
  profile: ModelProfile;
  result: ModelProbeResult | null;
  error: string | null;
}) {
  const { text } = useOnboardingTranslation();
  const matching =
    result?.profile_id === profile.profile_id &&
    result.profile_revision === profile.revision
      ? result
      : null;
  if (!matching && !error) return null;
  return (
    <section
      className="probe-result"
      aria-label={`${profile.name} ${text("版本")} ${profile.revision} ${text("测试结果")}`}
    >
      <strong>
        {profile.name} · {text("版本")} {profile.revision} · {text("测试连接")}
      </strong>
      <p>
        {text("测试目标")}: {text(endpointLabel(profile.base_url))} ·{" "}
        {profile.model}
      </p>
      {error && <p role="alert">{text(error)}</p>}
      {matching && (
        <ul>
          {matching.stages.map((stage) => (
            <li key={stage.stage}>
              {stage.stage}: {text(probeLabel(stage.code))} · {stage.latency_ms}
              ms
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function providerLabel(provider: ProviderKind): string {
  if (provider === "local_onnx") {
    return "内置 bge-small-zh-v1.5";
  }
  return PROVIDERS.find((item) => item.kind === provider)?.label ?? provider;
}

export function probeLabel(code: ProbeCode): string {
  const labels: Record<ProbeCode, string> = {
    ok: "通过",
    hy_default_unavailable: "HY 托管模型尚不可用",
    missing_api_key: "缺少 API Key",
    unreachable: "服务不可达",
    unauthorized: "认证失败；更新 key 后再检测，会自动接着处理",
    rate_limited: "请求频率受限",
    insufficient_balance:
      "模型账户余额不足；充值或换 key 后再检测，会自动接着处理",
    model_not_found: "没有找到模型",
    model_list_unsupported: "该网关不提供模型列表，已改由生成调用验证",
    invalid_response: "服务返回不符合协议",
    structured_output_unsupported: "结构化输出未通过",
    embedding_model_required: "需要填写 Embedding 模型",
    embedding_unavailable: "Embedding 模型不可用",
  };
  return labels[code];
}
