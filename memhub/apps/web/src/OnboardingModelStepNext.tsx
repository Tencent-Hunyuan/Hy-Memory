import { useEffect, useId, useRef, useState } from "react";
import {
  GenerationTechnicalDisclosure,
  GenerationTechnicalFields,
  generationSummary,
  ProviderPresetField,
} from "./GenerationModelPrimitives";
import type {
  OnboardingModelDraft,
  OnboardingModelStepProps,
} from "./OnboardingModelStep";
import { useOnboardingTranslation } from "./onboarding-language";
import { R6ColdStartFrame } from "./R6ColdStartFrame";
import { R6Button } from "./R6Controls";
import "./onboarding-model-r6.css";

type Action = "test" | "save-and-enable";

export function OnboardingModelStepNext(props: OnboardingModelStepProps) {
  const { text, t } = useOnboardingTranslation();
  const id = useId();
  const modelRef = useRef<HTMLInputElement>(null);
  const formRef = useRef<HTMLElement>(null);
  const flight = useRef(false);
  const alive = useRef(true);
  const [pending, setPending] = useState<Action | null>(null);
  const [callbackFailed, setCallbackFailed] = useState(false);
  const provider = props.providers.find(
    (candidate) => candidate.id === props.draft.provider_id,
  );
  const locked = props.phase !== "idle" || pending !== null;
  const fieldsLocked = locked || props.inputsLocked === true;
  const canEnable =
    (props.probe?.state === "passed" ||
      props.enableActionLabel === "核对提交状态") &&
    props.capability.saveAndEnable &&
    props.canSaveAndEnable === true &&
    !!props.generationRecipient.trim() &&
    provider?.supported;

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    const firstError = (
      [
        "api_key",
        "model",
        "base_url",
        "timeout_seconds",
        "max_concurrency",
        "context_window",
      ] as const
    ).find((key) => props.fieldErrors?.[key]);
    const technicalError = firstError && firstError !== "api_key";
    if (technicalError && !props.advancedOpen) {
      props.onAdvancedChange(true);
      return;
    }
    if (firstError === "api_key" && props.advancedOpen) {
      props.onAdvancedChange(false);
      return;
    }
    if (firstError && (!technicalError || props.advancedOpen)) {
      formRef.current
        ?.querySelector<HTMLElement>(`[name="${firstError}"]`)
        ?.focus({ preventScroll: true });
      return;
    }
    if (props.advancedOpen && !props.draft.model.trim())
      modelRef.current?.focus({ preventScroll: true });
  }, [
    props.advancedOpen,
    props.draft.model,
    props.fieldErrors,
    props.onAdvancedChange,
  ]);

  async function run(action: Action) {
    if (flight.current || locked) return;
    if (action === "test" && (!props.canTest || !provider?.supported)) return;
    if (action === "save-and-enable" && !canEnable) return;
    flight.current = true;
    setPending(action);
    setCallbackFailed(false);
    try {
      await (action === "test" ? props.onTest() : props.onSaveAndEnable());
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

  const displayedPhase =
    props.phase !== "idle"
      ? props.phase
      : pending === "test"
        ? "testing"
        : pending
          ? "enabling"
          : "idle";
  const sourceSummary =
    props.sourceNames.length <= 3
      ? props.sourceNames.join(" · ")
      : t("onboardingSourcesSummary", {
          names: props.sourceNames.slice(0, 3).join(" · "),
          count: props.sourceNames.length,
        });
  const runtimeErrors = Object.fromEntries(
    Object.entries(props.fieldErrors ?? {}).filter(([key]) =>
      [
        "provider_kind",
        "model",
        "base_url",
        "timeout_seconds",
        "max_concurrency",
        "context_window",
      ].includes(key),
    ),
  ) as Partial<Record<keyof OnboardingModelDraft, string>>;

  return (
    <div className="onboarding-model-r6">
      <R6ColdStartFrame
        step={3}
        eyebrow={text("智能记忆 Pro · 配置记忆整理")}
        title={text("配置记忆整理模型")}
        focusToken={props.focusToken}
        footer={
          <>
            <p className="onboarding-model-footer-note">
              {text(
                props.probe?.state === "passed"
                  ? "模型连接测试通过，可以启用并开始沉淀记忆。"
                  : "先测试模型连通性；测试通过后再启用。",
              )}
            </p>
            <div className="onboarding-model-footer-actions">
              <R6Button
                disabled={locked}
                onClick={() => {
                  if (!flight.current && !locked) props.onBack();
                }}
              >
                {text("返回选择模式")}
              </R6Button>
              <R6Button
                disabled={locked || !props.canTest || !provider?.supported}
                onClick={() => void run("test")}
              >
                {text(
                  displayedPhase === "testing"
                    ? "正在测试模型…"
                    : (props.testActionLabel ?? "测试连接"),
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
                    : (props.enableActionLabel ?? "启用并进入"),
                )}
              </R6Button>
            </div>
          </>
        }
      >
        <div className="onboarding-model-layout">
          <section
            ref={formRef}
            className="onboarding-model-form"
            aria-label={text("模型配置")}
            data-advanced={props.advancedOpen}
          >
            <div className="onboarding-model-group">
              <p className="onboarding-model-intro">
                {text("选择服务商，填入 API Key。核对已有配置后再继续。")}
              </p>
              <label
                className="onboarding-model-field onboarding-model-provider"
                htmlFor={`${id}-provider`}
              >
                <span id={`${id}-provider-label`}>{text("模型服务商")}</span>
                <ProviderPresetField
                  id={`${id}-provider`}
                  labelledBy={`${id}-provider-label`}
                  value={props.draft.provider_id}
                  disabled={fieldsLocked}
                  text={text}
                  onChange={(provider_id) =>
                    props.onDraftChange({ provider_id })
                  }
                />
              </label>
              <label
                className="onboarding-model-field onboarding-model-key"
                htmlFor={`${id}-api-key`}
              >
                <span>
                  API Key{provider?.keyRequired ? "" : text("（可选）")}
                </span>
                <input
                  id={`${id}-api-key`}
                  name="api_key"
                  aria-label={`API Key${provider?.keyRequired ? "" : text("（可选）")}`}
                  type="password"
                  autoComplete="off"
                  value={props.draft.api_key}
                  disabled={fieldsLocked}
                  required={
                    provider?.keyRequired &&
                    !props.profile?.has_configured_api_key
                  }
                  aria-invalid={!!props.fieldErrors?.api_key}
                  aria-describedby={
                    props.fieldErrors?.api_key
                      ? `${id}-api-key-error`
                      : `${id}-api-key-help`
                  }
                  placeholder={text(
                    props.profile?.has_configured_api_key
                      ? "已安全保存，留空保留"
                      : "输入服务商提供的 API Key",
                  )}
                  onChange={(event) =>
                    props.onDraftChange({ api_key: event.target.value })
                  }
                />
                <small id={`${id}-api-key-help`}>
                  {text(
                    props.profile?.credential_state === "unavailable"
                      ? "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”。"
                      : props.profile?.has_configured_api_key
                        ? "留空保留已保存密钥；填写新密钥后需重新测试。"
                        : "密钥保存在系统安全存储中，不写入对话或日志。",
                  )}
                </small>
                {props.fieldErrors?.api_key ? (
                  <small
                    id={`${id}-api-key-error`}
                    className="generation-model-error"
                  >
                    {text(props.fieldErrors.api_key)}
                  </small>
                ) : null}
              </label>
              <div className="onboarding-model-default-summary">
                <span>{text("默认模型")}</span>
                <strong>
                  {generationSummary(
                    props.draft.provider_id,
                    props.draft.provider_kind,
                    props.draft.model,
                    text,
                  )}
                </strong>
              </div>
              <GenerationTechnicalDisclosure
                open={props.advancedOpen}
                disabled={fieldsLocked}
                controls={`${id}-technical`}
                text={text}
                onChange={props.onAdvancedChange}
              >
                <GenerationTechnicalFields
                  idPrefix={`${id}-generation`}
                  values={props.draft}
                  errors={runtimeErrors}
                  disabled={fieldsLocked}
                  text={text}
                  modelRef={modelRef}
                  onChange={(patch) =>
                    props.onDraftChange(patch as Partial<OnboardingModelDraft>)
                  }
                />
              </GenerationTechnicalDisclosure>
            </div>

            <div className="onboarding-model-recovery-actions">
              {props.onRecoverCreation ? (
                <R6Button
                  disabled={locked}
                  onClick={() => void props.onRecoverCreation?.()}
                >
                  {text("核对创建结果")}
                </R6Button>
              ) : null}
              {props.onDiscardMissingCreation ? (
                <R6Button
                  variant="secondary"
                  disabled={locked}
                  onClick={() => void props.onDiscardMissingCreation?.()}
                >
                  {text("放弃旧记录，重新配置")}
                </R6Button>
              ) : null}
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
              {props.probe ? (
                <p
                  className="onboarding-model-probe"
                  data-state={props.probe.state}
                  role="status"
                >
                  {text(props.probe.message)}
                </p>
              ) : null}
              {props.error || callbackFailed ? (
                <p className="onboarding-model-error" role="alert">
                  {text(
                    props.error ||
                      "操作结果未确认，已填写内容保留，请核对后重试。",
                  )}
                </p>
              ) : null}
              {!props.capability.saveAndEnable ? (
                <p role="status">
                  {text(props.capability.reason || "当前不能在此启用模型。")}
                </p>
              ) : null}
            </div>
          </section>
          <aside
            className="onboarding-model-summary"
            aria-label={text("记忆整理")}
          >
            <div className="onboarding-model-summary-heading">
              <p>{text("记忆整理")}</p>
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
              <small>
                {text("处理服务：")}
                {text(provider?.label ?? "待选择")}
              </small>
              <small>
                {text("相关对话会发送给所选服务商。")}
                <br />
                {text("测试只向此接口发送测试内容，不发送历史对话。")}
              </small>
            </div>
          </aside>
        </div>
      </R6ColdStartFrame>
    </div>
  );
}
