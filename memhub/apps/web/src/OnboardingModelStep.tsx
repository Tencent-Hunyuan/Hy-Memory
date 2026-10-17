import type { ProviderKind } from "@one-memory/contracts";
import { ChevronDown, ChevronUp } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { OptionMenu } from "./OptionMenu";
import { useOnboardingTranslation } from "./onboarding-language";
import { R6ColdStartFrame } from "./R6ColdStartFrame";
import { R6Button } from "./R6Controls";
import "./onboarding-model-r6.css";

/** Strings preserve partial numeric edits. The owner validates and maps to API payloads. */
export interface OnboardingModelDraft {
  provider_id: string;
  name: string;
  provider_kind: ProviderKind;
  model: string;
  base_url: string;
  api_key: string;
  disable_thinking: boolean;
  embedding_mode: "local" | "custom";
  embedding_provider_kind: "openai_compatible" | "ollama";
  embedding_model: string;
  embedding_base_url: string;
  timeout_seconds: string;
  max_concurrency: string;
  context_window: string;
}
export interface OnboardingModelProvider {
  id: string;
  label: string;
  keyRequired: boolean;
  supported: boolean;
}
type Action = "test" | "save-draft" | "save-and-enable";
export interface OnboardingModelStepProps {
  draft: OnboardingModelDraft;
  providers: readonly OnboardingModelProvider[];
  advancedOpen: boolean;
  onAdvancedChange(open: boolean): void;
  onDraftChange(patch: Partial<OnboardingModelDraft>): void;
  phase: "idle" | "testing" | "saving" | "enabling";
  capability: { saveAndEnable: boolean; reason?: string };
  canTest: boolean;
  canSaveDraft: boolean;
  /** Owner validation/current revision gate; absence fails closed. */
  canSaveAndEnable?: boolean;
  enableActionLabel?: string;
  testActionLabel?: string;
  inputsLocked?: boolean;
  fieldErrors?: Partial<Record<keyof OnboardingModelDraft, string>>;
  error?: string | null;
  probe?: { state: "passed" | "failed" | "stale"; message: string } | null;
  profile?: {
    profile_id: string;
    revision: number;
    has_configured_api_key: boolean;
    credential_state?: "available" | "missing" | "unavailable";
  } | null;
  sourceNames: readonly string[];
  importedConversationCount?: number;
  connectionSummary?: string;
  generationRecipient: string;
  embeddingRecipient: string;
  onTest(): Promise<void>;
  onSaveAndEnable(): Promise<void>;
  onSaveDraft(): Promise<void>;
  onRecoverCreation?(): Promise<void>;
  onDiscardMissingCreation?(): Promise<void>;
  onBack(): void;
  onSkip?(): void;
  /** Owner must preserve receipts/unknown results and supply a safe display error. */
  onActionError?(action: Action): void;
}

export function OnboardingModelStep(props: OnboardingModelStepProps) {
  const { text, t, locale } = useOnboardingTranslation();
  const { draft, advancedOpen, onDraftChange, fieldErrors = {}, phase } = props;
  const id = useId();
  const fields = useRef<HTMLElement>(null);
  const advancedToggle = useRef<HTMLButtonElement>(null);
  const restoreToggleFocus = useRef(false);
  const flight = useRef(false);
  const alive = useRef(true);
  const [pending, setPending] = useState<Action | null>(null);
  const [callbackFailed, setCallbackFailed] = useState(false);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const locked = phase !== "idle" || pending !== null;
  const fieldsLocked = locked || props.inputsLocked === true;
  // biome-ignore lint/correctness/useExhaustiveDependencies: Restore only after the controlled mode change has rendered its replacement toggle.
  useEffect(() => {
    if (!restoreToggleFocus.current) return;
    restoreToggleFocus.current = false;
    if (fields.current) fields.current.scrollTop = 0;
    advancedToggle.current?.focus({ preventScroll: true });
  }, [advancedOpen]);
  function changeAdvanced(open: boolean) {
    restoreToggleFocus.current = true;
    props.onAdvancedChange(open);
  }
  const provider = props.providers.find(
    (item) => item.id === draft.provider_id,
  );
  const canEnable =
    (props.probe?.state === "passed" ||
      props.enableActionLabel === "核对提交状态") &&
    props.capability.saveAndEnable &&
    props.canSaveAndEnable === true &&
    !!props.generationRecipient.trim() &&
    !!props.embeddingRecipient.trim() &&
    provider?.supported;
  const sourceSummary =
    props.sourceNames.length <= 3
      ? props.sourceNames.join(" · ")
      : t("onboardingSourcesSummary", {
          names: props.sourceNames.slice(0, 3).join(" · "),
          count: props.sourceNames.length,
        });
  async function run(action: Action) {
    if (flight.current || locked) return;
    if (action === "save-and-enable" && !canEnable) return;
    if (action === "test" && (!props.canTest || !provider?.supported)) return;
    if (action === "save-draft" && !props.canSaveDraft) return;
    flight.current = true;
    setPending(action);
    setCallbackFailed(false);
    try {
      await (action === "test"
        ? props.onTest()
        : action === "save-draft"
          ? props.onSaveDraft()
          : props.onSaveAndEnable());
    } catch {
      if (alive.current) {
        setCallbackFailed(true);
        props.onActionError?.(action);
      }
    } finally {
      flight.current = false;
      if (alive.current) setPending(null);
    }
  }
  function field(
    key: keyof OnboardingModelDraft,
    label: string,
    options: {
      type?: string;
      min?: number;
      max?: number;
      maxLength?: number;
      required?: boolean;
      placeholder?: string;
    } = {},
  ) {
    const error = fieldErrors[key];
    return (
      <label className="onboarding-model-field" htmlFor={`${id}-${key}`}>
        <span>{text(label)}</span>
        <input
          id={`${id}-${key}`}
          name={key}
          aria-label={text(label)}
          type={options.type ?? "text"}
          autoComplete="off"
          spellCheck={false}
          value={String(draft[key])}
          disabled={fieldsLocked}
          aria-invalid={!!error}
          aria-describedby={error ? `${id}-${key}-error` : undefined}
          min={options.min}
          max={options.max}
          maxLength={options.maxLength}
          required={options.required}
          placeholder={
            options.placeholder ? text(options.placeholder) : undefined
          }
          onChange={(event) => {
            if (!flight.current && !fieldsLocked)
              onDraftChange({ [key]: event.target.value });
          }}
        />
        {error && (
          <small id={`${id}-${key}-error`} className="onboarding-model-error">
            {text(error)}
          </small>
        )}
      </label>
    );
  }
  const displayedPhase =
    phase !== "idle"
      ? phase
      : pending === "test"
        ? "testing"
        : pending === "save-draft"
          ? "saving"
          : pending
            ? "enabling"
            : "idle";
  const connectionFailed = props.probe?.state === "failed";
  const pageTitle = advancedOpen
    ? "配置详情，按需要调整"
    : connectionFailed
      ? "连接未通过，配置已保留"
      : "选好模型，让记忆开始沉淀";
  const connectionReceipt = props.connectionSummary?.includes("已启用")
    ? "连接设置已保存\n成功项保留，不重复执行。"
    : props.connectionSummary === "持续同步 · 一键接入"
      ? "已选择：持续同步 · 一键接入"
      : props.connectionSummary;
  return (
    <div
      className="onboarding-model-r6"
      data-integration={
        props.capability.saveAndEnable
          ? "capability-provided"
          : "not-integrated"
      }
    >
      <R6ColdStartFrame
        step={3}
        eyebrow={text("沉淀记忆 · 使用你选择的模型")}
        title={text(pageTitle)}
        footer={
          <>
            <p className="onboarding-model-footer-note">
              {text(
                !props.capability.saveAndEnable
                  ? "当前无法启用模型，已填写内容保留。"
                  : props.probe?.state === "passed"
                    ? "模型连接测试通过，可以启用并开始沉淀记忆。"
                    : "先测试模型连通性；测试通过后再启用。",
              )}
            </p>
            <div className="onboarding-model-footer-actions">
              {locale === "es" && (
                <span id={`${id}-skip-destination`} hidden>
                  Omitir y entrar a Aplicaciones
                </span>
              )}
              <R6Button
                aria-describedby={
                  locale === "es" ? `${id}-skip-destination` : undefined
                }
                disabled={locked}
                onClick={() => {
                  if (!flight.current && !locked)
                    (props.onSkip ?? props.onBack)();
                }}
              >
                {text("跳过，先进入应用")}
              </R6Button>
              <R6Button
                disabled={locked || !props.canTest}
                onClick={() => void run("test")}
              >
                {text(
                  displayedPhase === "testing"
                    ? "正在测试模型…"
                    : (props.testActionLabel ?? "测试模型连通性"),
                )}
              </R6Button>
              <R6Button
                variant="primary"
                disabled={locked || !canEnable}
                onClick={() => void run("save-and-enable")}
              >
                {text(
                  displayedPhase === "enabling"
                    ? "正在启用…"
                    : (props.enableActionLabel ?? "确认启用"),
                )}
              </R6Button>
            </div>
          </>
        }
      >
        <div className="onboarding-model-layout">
          <section
            className="onboarding-model-form"
            aria-label={text("模型配置")}
            data-advanced={advancedOpen}
          >
            <section
              ref={fields}
              id={`${id}-fields`}
              className="onboarding-model-fields"
              aria-label={text("模型参数")}
              // biome-ignore lint/a11y/noNoninteractiveTabindex: The parameter pane needs keyboard scrolling.
              tabIndex={0}
            >
              {!advancedOpen ? (
                <>
                  <p className="onboarding-model-intro">
                    {text("选择服务商，填入 API Key。核对已有配置后再继续。")}
                  </p>
                  <label
                    className="onboarding-model-field onboarding-model-provider"
                    htmlFor={`${id}-provider`}
                  >
                    <span id={`${id}-provider-label`}>
                      {text("模型服务商")}
                    </span>
                    <OptionMenu
                      id={`${id}-provider`}
                      labelledBy={`${id}-provider-label`}
                      value={draft.provider_id}
                      disabled={fieldsLocked}
                      options={[
                        ...(!provider
                          ? [{ value: "", label: text("选择服务商") }]
                          : []),
                        ...props.providers.map((item) => ({
                          value: item.id,
                          label: text(item.label),
                          disabled: !item.supported,
                        })),
                      ]}
                      onChange={(value) => {
                        if (!flight.current)
                          onDraftChange({ provider_id: value });
                      }}
                    />
                  </label>
                  <div className="onboarding-model-key">
                    {field("api_key", "API Key", {
                      type: "password",
                      required:
                        provider?.keyRequired &&
                        !props.profile?.has_configured_api_key,
                      placeholder: props.profile?.has_configured_api_key
                        ? "已安全保存，留空保留"
                        : "输入服务商提供的 API Key",
                    })}
                  </div>
                  <p
                    className="onboarding-model-key-note"
                    data-state={connectionFailed ? "failed" : "idle"}
                  >
                    {text(
                      connectionFailed
                        ? "连接未通过，请检查 API Key、模型与接口地址后重试。配置已保留。"
                        : props.profile?.credential_state === "unavailable"
                          ? "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”。"
                          : props.profile?.has_configured_api_key
                            ? "留空保留已保存密钥；填写新密钥后需重新测试。"
                            : "密钥保存在系统安全存储中，不写入对话或日志。",
                    )}
                  </p>
                  <button
                    ref={advancedToggle}
                    type="button"
                    className="onboarding-model-toggle"
                    aria-expanded={false}
                    aria-controls={`${id}-fields`}
                    disabled={fieldsLocked}
                    onClick={() => changeAdvanced(true)}
                  >
                    {text("配置详情与高级设置")}{" "}
                    <ChevronDown size={16} aria-hidden="true" />
                  </button>
                  <p className="onboarding-model-profile">
                    {provider ? text(provider.label) : text("服务商待选择")} ·{" "}
                    {draft.model || text("模型待填写")}
                    <br />
                    {text(safeEndpoint(draft.base_url))} ·{" "}
                    {draft.embedding_mode === "local"
                      ? text("本地向量模型")
                      : draft.embedding_model || text("向量模型待填写")}{" "}
                    ·{" "}
                    {text(draft.disable_thinking ? "思考已关闭" : "思考未关闭")}
                  </p>
                </>
              ) : (
                <>
                  <div className="onboarding-model-advanced-head">
                    <p>
                      {text("预设已填好，只有自定义服务或特殊需求才需要修改。")}
                    </p>
                    <button
                      ref={advancedToggle}
                      type="button"
                      className="onboarding-model-toggle"
                      aria-expanded={true}
                      aria-controls={`${id}-fields`}
                      disabled={fieldsLocked}
                      onClick={() => changeAdvanced(false)}
                    >
                      {text("返回服务商选择")}{" "}
                      <ChevronUp size={16} aria-hidden="true" />
                    </button>
                  </div>
                  <div className="onboarding-model-advanced-grid">
                    {field("name", "配置名称", { maxLength: 128 })}
                    {field("model", "模型名称", {
                      required: true,
                      maxLength: 256,
                    })}
                    <div className="onboarding-model-wide">
                      {field("base_url", "Base URL", {
                        type: "url",
                        required: true,
                      })}
                    </div>
                    <div className="onboarding-model-wide onboarding-model-key">
                      {field("api_key", "API Key", {
                        type: "password",
                        required:
                          provider?.keyRequired &&
                          !props.profile?.has_configured_api_key,
                        placeholder: props.profile?.has_configured_api_key
                          ? "已安全保存，留空保留"
                          : "输入服务商提供的 API Key",
                      })}
                      <p
                        className="onboarding-model-key-note"
                        data-state={connectionFailed ? "failed" : "idle"}
                      >
                        {text(
                          props.profile?.has_configured_api_key
                            ? "留空保留已保存密钥；填写新密钥后需重新测试。"
                            : "密钥保存在系统安全存储中，不写入对话或日志。",
                        )}
                      </p>
                    </div>
                    <label className="onboarding-model-field">
                      <span>{text("协议")}</span>
                      <select
                        aria-label={text("协议")}
                        value={draft.provider_kind}
                        disabled={fieldsLocked}
                        onChange={(e) => {
                          if (!flight.current)
                            onDraftChange({
                              provider_kind: e.target.value as ProviderKind,
                            });
                        }}
                      >
                        <option value="openai_compatible">
                          OpenAI-compatible
                        </option>
                        <option value="ollama">Ollama</option>
                        <option value="anthropic" disabled>
                          {text("Anthropic（暂不支持）")}
                        </option>
                        <option value="hy_default" disabled>
                          {text("HY 托管（当前不可用）")}
                        </option>
                      </select>
                    </label>
                    <label className="onboarding-model-field">
                      <span>{text("向量模型")}</span>
                      <select
                        aria-label={text("向量模型")}
                        value={draft.embedding_mode}
                        disabled={fieldsLocked}
                        onChange={(e) => {
                          if (!flight.current)
                            onDraftChange({
                              embedding_mode: e.target.value as
                                | "local"
                                | "custom",
                            });
                        }}
                      >
                        <option value="local">
                          {text("本地 ONNX · 内置")}
                        </option>
                        <option value="custom">{text("自定义向量")}</option>
                      </select>
                    </label>
                    <div className="onboarding-model-numbers">
                      {field("timeout_seconds", "超时（秒）", {
                        type: "number",
                        min: 1,
                        max: 600,
                      })}
                      {field("max_concurrency", "最大并发", {
                        type: "number",
                        min: 1,
                        max: 32,
                      })}
                      {field("context_window", "上下文窗口", {
                        type: "number",
                        min: 1024,
                        max: 10000000,
                        placeholder: "自动 / 可选",
                      })}
                    </div>
                    <label className="onboarding-model-thinking">
                      <input
                        type="checkbox"
                        checked={draft.disable_thinking}
                        disabled={fieldsLocked}
                        onChange={(e) => {
                          if (!flight.current)
                            onDraftChange({
                              disable_thinking: e.target.checked,
                            });
                        }}
                      />
                      {text("关闭思考模式")}
                    </label>
                    <p className="onboarding-model-vector-note">
                      {text(
                        "自定义向量配置可展开；远程向量服务须遵守凭据边界。",
                      )}
                      <br />
                      {text("切换服务商或模型后，需要重新测试并启用。")}
                    </p>
                    {draft.embedding_mode === "custom" && (
                      <>
                        {field("embedding_model", "向量模型名称", {
                          required: true,
                          maxLength: 256,
                        })}
                        <label className="onboarding-model-field">
                          <span>{text("向量协议")}</span>
                          <select
                            aria-label={text("向量协议")}
                            value={draft.embedding_provider_kind}
                            disabled={fieldsLocked}
                            onChange={(e) => {
                              if (!flight.current)
                                onDraftChange({
                                  embedding_provider_kind: e.target.value as
                                    | "ollama"
                                    | "openai_compatible",
                                });
                            }}
                          >
                            <option value="openai_compatible">
                              {text("同源 OpenAI-compatible（共用 Key）")}
                            </option>
                            <option value="ollama">
                              {text("本机 Ollama")}
                            </option>
                          </select>
                        </label>
                        <div className="onboarding-model-wide">
                          {field("embedding_base_url", "向量 Base URL", {
                            type: "url",
                            required: true,
                          })}
                          <small>
                            {text(
                              "远程向量地址须与生成服务同源；本机地址仍需校验。",
                            )}
                          </small>
                        </div>
                      </>
                    )}
                  </div>
                </>
              )}
            </section>
            <div className="onboarding-model-recovery-actions">
              {props.onRecoverCreation && (
                <R6Button
                  disabled={locked}
                  onClick={() => void props.onRecoverCreation?.()}
                >
                  {text("核对创建结果")}
                </R6Button>
              )}
              {props.onDiscardMissingCreation && (
                <R6Button
                  variant="secondary"
                  disabled={locked}
                  onClick={() => void props.onDiscardMissingCreation?.()}
                >
                  {text("放弃旧记录，重新配置")}
                </R6Button>
              )}
            </div>
            <div className="onboarding-model-feedback" tabIndex={-1}>
              {(displayedPhase === "testing" ||
                displayedPhase === "enabling") && (
                <p role="status" aria-label={text("模型提交状态")}>
                  {text(
                    displayedPhase === "testing"
                      ? "正在测试连接…"
                      : "正在提交保存与启用，请等待回执…",
                  )}
                </p>
              )}
              {props.probe && (
                <p
                  className="onboarding-model-probe"
                  data-state={props.probe.state}
                  role="status"
                >
                  {text(props.probe.message)}
                </p>
              )}
              {(props.error || callbackFailed) && (
                <p className="onboarding-model-error" role="alert">
                  {text(
                    props.error ||
                      "操作结果未确认，已填写内容保留，请核对后重试。",
                  )}
                </p>
              )}
            </div>
          </section>
          <aside
            className="onboarding-model-summary"
            aria-label={text("本次沉淀")}
            data-full-height={advancedOpen || connectionFailed}
            // biome-ignore lint/a11y/noNoninteractiveTabindex: Keyboard users must be able to scroll the full permission disclosure.
            tabIndex={0}
          >
            <div className="onboarding-model-summary-heading">
              <p>{text("本次沉淀")}</p>
              <h2>{text("让记忆从这里开始")}</h2>
            </div>
            <div className="onboarding-model-connections">
              <p>{text("对话来源")}</p>
              <strong>{sourceSummary || text("尚无已选来源")}</strong>
              <small>
                {t("onboardingImportedConversations", {
                  count: props.importedConversationCount ?? 0,
                })}
              </small>
            </div>
            <div className="onboarding-model-summary-status">
              {connectionReceipt && (
                <small className="onboarding-model-connection-receipt">
                  {text(connectionReceipt)}
                </small>
              )}
              <small>
                {text("处理服务：")}
                {text(provider?.label ?? "待选择")}
                <br />
                {text("相关对话会发送给所选服务商。")}
              </small>
              <small>
                {text(
                  "测试连接只发送测试内容。启用后会使用此模型整理已导入和之后同步的会话。",
                )}
              </small>
            </div>
            {!props.capability.saveAndEnable && (
              <p role="status">
                {text(props.capability.reason || "当前不能在此启用模型。")}
              </p>
            )}
          </aside>
        </div>
      </R6ColdStartFrame>
    </div>
  );
}

function safeEndpoint(value: string): string {
  try {
    const url = new URL(value);
    if (
      !["http:", "https:"].includes(url.protocol) ||
      url.username ||
      url.password ||
      url.search ||
      url.hash
    )
      return "接口地址待核对";
    return `${url.origin}${url.pathname.replace(/\/$/, "")}`;
  } catch {
    return "接口地址待核对";
  }
}
