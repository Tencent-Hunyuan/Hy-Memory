import type {
  ModelProbeResult,
  ModelProfile,
  ModelProfileInput,
  ModelProfilePatch,
  ProbeCode,
  ProviderKind,
} from "@one-memory/contracts";
import { Check, Pencil, Plus, RefreshCw } from "lucide-react";
import {
  type FormEvent,
  type Ref,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { OptionMenu } from "./OptionMenu";
import { useOnboardingTranslation } from "./onboarding-language";
import { PROVIDER_PRESETS } from "./provider-presets";
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
  | "base-url"
  | "api-key"
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
    errors["base-url"] = text("endpoint") ?? FIELD_PROBE_TEXT.unreachable;
  }
  if (!result.capabilities.model_available) {
    if (stages.get("model")?.code === "unauthorized") {
      errors["api-key"] = FIELD_PROBE_TEXT.unauthorized;
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

function presetForProfile(profile: {
  provider_kind: string;
  base_url: string | null;
}): string {
  return (
    PROVIDER_PRESETS.find(
      (item) =>
        item.provider === profile.provider_kind &&
        endpointKey(item.baseUrl) === endpointKey(profile.base_url ?? ""),
    )?.id ?? "custom"
  );
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

function ready(
  profile: ModelProfile,
  probe: ModelProbeResult | null = null,
  error: string | null = null,
): boolean {
  return (
    !error &&
    (!probe ||
      (probe.profile_id === profile.profile_id &&
        probe.profile_revision === profile.revision &&
        probe.capabilities.endpoint_reachable &&
        probe.capabilities.model_available &&
        probe.capabilities.embedding_available)) &&
    profile.credential_state !== "unavailable" &&
    !(profile.has_configured_api_key && !profile.has_api_key) &&
    profile.capabilities.endpoint_reachable &&
    profile.capabilities.model_available &&
    profile.capabilities.embedding_available
  );
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

export function ModelSettings({
  controlsRef,
  onProfilesChanged,
  compactSingle = false,
  onSetupComplete,
}: {
  controlsRef?: Ref<ModelSettingsActions>;
  onProfilesChanged?(): void;
  compactSingle?: boolean;
  onSetupComplete?(): void;
} = {}) {
  const { text } = useOnboardingTranslation();
  const models = useModelProfiles();
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
  const flight = useRef(false);
  const alive = useRef(true);
  const savedCard = useRef<HTMLElement>(null);
  const formEl = useRef<HTMLFormElement>(null);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useLayoutEffect(() => {
    if (saved) savedCard.current?.focus({ preventScroll: true });
  }, [saved]);
  useLayoutEffect(() => {
    const first = Object.keys(fields).find((key) => fields[key as FieldName]);
    if (advancedEmbedding && first?.startsWith("embedding-")) {
      formEl.current
        ?.querySelector<HTMLInputElement>(`[name="${first}"]`)
        ?.focus();
    }
  }, [advancedEmbedding, fields]);
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
  const isReady = (profile: ModelProfile) =>
    ready(profile, models.probeFor(profile), models.probeErrorFor(profile));

  function editProfile(profile: ModelProfile) {
    if (ownerLocked) return;
    setEditing({ ...profile });
    setEmbeddingModeChanged(false);
    setSaved(null);
    setCustomOpen(true);
    setName(profile.name);
    setNameEdited(true);
    setProvider(profile.provider_kind);
    setProviderPreset(presetForProfile(profile));
    setBaseUrl(profile.base_url ?? "");
    setModel(profile.model);
    setDisableThinking(profile.disable_thinking);
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
      if (!profile || ownerLocked) return false;
      editProfile(profile);
      return true;
    },
    editProfile(profile) {
      if (ownerLocked) return false;
      editProfile(profile);
      return true;
    },
    add() {
      if (ownerLocked) return false;
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
  const profileSnapshot = JSON.stringify(models.profiles);
  const previousProfileSnapshot = useRef(profileSnapshot);
  useEffect(() => {
    if (previousProfileSnapshot.current === profileSnapshot) return;
    previousProfileSnapshot.current = profileSnapshot;
    onProfilesChanged?.();
  }, [profileSnapshot, onProfilesChanged]);

  async function finishSetup(profile: ModelProfile): Promise<boolean> {
    setSetupPhase("testing");
    const result = await models.probeProfile(profile.profile_id);
    if (!alive.current) return false;
    if (
      result &&
      result.profile_id === profile.profile_id &&
      result.profile_revision !== profile.revision
    ) {
      // A newer revision used to be accepted here, and then activation turned
      // on whatever the configuration had become. The user is told the
      // settings they filled in passed, so this hands them a model they never
      // tested. `ready` below cannot catch it either: by then the probe and
      // the activated profile are both on the new revision and agree.
      setSetupError(
        result.profile_revision > profile.revision
          ? "这份配置在测试期间被改过，刚才测的是改动后的版本。配置已保存但未启用，请重新测试并启用。"
          : "测试结果来自这份配置更早的版本。配置已保存但未启用，请重新测试并启用。",
      );
      return false;
    }
    if (
      !result ||
      result.profile_id !== profile.profile_id ||
      result.profile_revision !== profile.revision ||
      !result.capabilities.endpoint_reachable ||
      !result.capabilities.model_available ||
      !result.capabilities.embedding_available
    ) {
      setSetupError(
        "配置已保存，连接测试未通过。请核对配置后重试，历史对话尚未发送。",
      );
      setFields(probeFieldErrors(result));
      return false;
    }
    setSetupPhase("activating");
    const active = await models.activate(profile.profile_id);
    if (!alive.current) return false;
    if (
      !active ||
      active.profile_id !== profile.profile_id ||
      !active.is_active ||
      !ready(active, result)
    ) {
      setSetupError("配置已保存，尚未确认启用成功。请核对配置后重试。");
      return false;
    }
    if (profile.name !== PRESET_PROFILE_NAME) setSaved(active);
    onSetupComplete?.();
    return true;
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
    if (!model.trim()) errors.model = "请填写生成模型名。";
    if (!validEndpoint(baseUrl))
      errors["base-url"] = "请填写完整的 http:// 或 https:// 接口地址。";
    const credentialError = keyError(apiKey, keyRequired);
    if (credentialError) errors["api-key"] = credentialError;
    if (embeddingMode === "custom") {
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
    try {
      const patch: ModelProfilePatch = editing
        ? { expected_revision: editing.revision }
        : { expected_revision: 0 };
      if (editing) {
        if (input.name !== editing.name) patch.name = input.name;
        if (input.model !== editing.model) patch.model = input.model;
        if (input.base_url !== editing.base_url)
          patch.base_url = input.base_url;
        if (input.disable_thinking !== editing.disable_thinking)
          patch.disable_thinking = input.disable_thinking;
        if (apiKey) patch.api_key = apiKey;
        if (embeddingMode === "custom") {
          if (embeddingModel !== editing.embedding_model)
            patch.embedding_model = embeddingModel;
          if (embeddingProvider !== editing.embedding_provider_kind)
            patch.embedding_provider_kind = embeddingProvider;
          if (
            embeddingBaseUrl !==
            (editing.embedding_base_url ?? editing.base_url ?? "")
          )
            patch.embedding_base_url = embeddingBaseUrl;
        } else if (
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
          <h2 id="models-title">{text("记忆模型")}</h2>
        </div>
        <p>
          {text(
            "只需配置生成模型。向量嵌入已内置在本机，不为 System 1、System 2 分开选模型。",
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
        <div className="model-refresh-notice" role="status">
          <p>{text(models.refreshError)}</p>
          <button
            type="button"
            className="secondary"
            disabled={locked}
            onClick={() => void models.refreshProfiles()}
          >
            <RefreshCw size={16} aria-hidden="true" />
            {text("刷新配置列表")}
          </button>
        </div>
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
            {savedProfile.model} · {text(endpointLabel(savedProfile.base_url))}{" "}
            · {text("版本")} {savedProfile.revision}
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
          <div className="model-form-field">
            {text("服务商预设")}
            <OptionMenu
              ariaLabel={text("服务商预设")}
              describedBy="model-preset-help"
              value={providerPreset}
              disabled={formLocked}
              options={[
                ...PROVIDER_PRESETS.map((item) => ({
                  value: item.id,
                  label: item.label,
                })),
                { value: "custom", label: text("自定义兼容服务") },
              ]}
              onChange={(value) => {
                const next = PROVIDER_PRESETS.find((item) => item.id === value);
                setProviderPreset(value);
                if (!next) return;
                setProvider(next.provider);
                setBaseUrl(next.baseUrl);
                setModel(next.model);
                setApiKey("");
                setDisableThinking(true);
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
            {text("协议")}
            <select
              name="provider"
              aria-label={text("协议")}
              aria-describedby="model-provider-help"
              value={provider}
              disabled={formLocked}
              onChange={(event) => {
                const next = event.target.value as ProviderKind;
                setProvider(next);
                setProviderPreset("custom");
                if (!nameEdited) setName(suggestedName(model, next));
              }}
            >
              {/* Only what the backend will actually activate. These used to be
               * listed and disabled, which put two dead ends on screen and made
               * the reader parse a parenthesis to find out they were dead. */}
              {PROVIDERS.filter((item) => item.activatable).map((item) => (
                <option key={item.kind} value={item.kind}>
                  {text(item.label)}
                </option>
              ))}
            </select>
            <small id="model-provider-help">
              {text("与模型服务提供的接口协议保持一致。")}
            </small>
          </label>
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
          <label>
            {text("生成模型名")}
            <input
              name="model"
              aria-label={text("生成模型名")}
              aria-describedby={`model-identifier-help${fields.model ? " model-field-error" : ""}`}
              aria-invalid={!!fields.model}
              autoComplete="off"
              required
              maxLength={256}
              value={model}
              disabled={formLocked}
              onChange={(event) => {
                setModel(event.target.value);
                setProviderPreset("custom");
                if (!nameEdited) setName(suggestedName(event.target.value));
              }}
              placeholder={text("例如：gpt-4o-mini…")}
            />
            <small id="model-identifier-help">
              {text("填写服务方提供的完整模型标识。")}
            </small>
            {fields.model && (
              <small id="model-field-error" className="model-field-error">
                {text(fields.model)}
              </small>
            )}
          </label>
          {/* Disclosure only changes visibility; mode determines what is saved. */}
          <div className="model-advanced">
            <button
              type="button"
              className="quiet"
              aria-expanded={advancedEmbedding}
              disabled={formLocked}
              onClick={() => setAdvancedEmbedding(!advancedEmbedding)}
            >
              {text("高级：自行配置向量模型")}
            </button>
            {advancedEmbedding ? (
              <>
                <label>
                  {text("向量配置模式")}
                  <select
                    name="embedding-mode"
                    value={embeddingMode}
                    disabled={formLocked}
                    onChange={(event) => {
                      setEmbeddingMode(
                        event.target.value as "local" | "custom",
                      );
                      setEmbeddingModeChanged(true);
                      setFields((previous) => ({
                        ...previous,
                        "embedding-model": undefined,
                        "embedding-base-url": undefined,
                      }));
                    }}
                  >
                    <option value="local">{text("本地内置")}</option>
                    <option value="custom">{text("自定义向量模型")}</option>
                  </select>
                </label>
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
                    <label>
                      {text("向量模型来源")}
                      <select
                        name="embedding-provider"
                        value={embeddingProvider}
                        disabled={formLocked}
                        onChange={(event) => {
                          const next = event.target.value as
                            | "openai_compatible"
                            | "ollama";
                          setEmbeddingProvider(next);
                        }}
                      >
                        <option value="ollama">{text("本机 Ollama")}</option>
                        <option value="openai_compatible">
                          {text("同源 OpenAI-compatible（共用 API Key）")}
                        </option>
                      </select>
                    </label>
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
                    {text("使用内置本地向量模型；不会发送内容给远程向量服务。")}
                  </small>
                )}
              </>
            ) : null}
            {!advancedEmbedding && embeddingMode === "custom" ? (
              <p className="model-help">
                {text("自定义向量")}: {embeddingModel || text("尚未填写模型")} ·{" "}
                {embeddingBaseUrl || text("尚未填写地址")}
              </p>
            ) : null}
          </div>
          <label>
            Base URL
            <input
              name="base-url"
              aria-label="Base URL"
              aria-describedby={`model-url-help${fields["base-url"] ? " model-url-error" : ""}`}
              aria-invalid={!!fields["base-url"]}
              type="url"
              inputMode="url"
              autoComplete="off"
              required={provider !== "hy_default"}
              disabled={formLocked || provider === "hy_default"}
              value={baseUrl}
              onChange={(event) => {
                setBaseUrl(event.target.value);
                setProviderPreset("custom");
              }}
            />
            <small id="model-url-help">
              {text("填写服务接口地址，不是聊天网页地址。")}
            </small>
            {fields["base-url"] && (
              <small id="model-url-error" className="model-field-error">
                {text(fields["base-url"])}
              </small>
            )}
          </label>
          <label>
            API Key{keyRequired ? "" : text("（可选）")}
            <input
              name="api-key"
              aria-label={`API Key${keyRequired ? "" : text("（可选）")}`}
              aria-describedby={`model-key-help${fields["api-key"] ? " model-key-error" : ""}`}
              aria-invalid={!!fields["api-key"]}
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
            {fields["api-key"] && (
              <small id="model-key-error" className="model-field-error">
                {text(fields["api-key"])}
              </small>
            )}
          </label>
          <label>
            <input
              name="disable-thinking"
              type="checkbox"
              checked={disableThinking}
              disabled={formLocked}
              onChange={(event) => setDisableThinking(event.target.checked)}
            />
            {text("关闭思考链（向支持的厂商下发 thinking-off 参数）")}
          </label>
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
                    {text(providerLabel(profile.provider_kind))} ·{" "}
                    {profile.model} ·
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
                        onClick={() => models.activate(profile.profile_id)}
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
            disabled={busy || !canEnable || !ready(profile, probe, probeError)}
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
