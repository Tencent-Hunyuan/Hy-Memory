import type { ProviderKind } from "@one-memory/contracts";
import { ChevronDown, ChevronUp } from "lucide-react";
import type { ReactNode, Ref } from "react";
import { OptionMenu } from "./OptionMenu";
import { PROVIDER_PRESETS } from "./provider-presets";

export interface GenerationModelValues {
  provider_kind: ProviderKind;
  model: string;
  base_url: string;
  timeout_seconds: string;
  max_concurrency: string;
  context_window: string;
  disable_thinking: boolean;
}

export type GenerationModelField = keyof GenerationModelValues | "api_key";
export type GenerationModelErrors = Partial<
  Record<GenerationModelField, string>
>;

export const GENERATION_PROTOCOLS: readonly {
  value: ProviderKind;
  label: string;
  supported: boolean;
}[] = [
  {
    value: "openai_compatible",
    label: "OpenAI-compatible",
    supported: true,
  },
  { value: "ollama", label: "Ollama（本机）", supported: true },
  { value: "anthropic", label: "Anthropic（暂不支持）", supported: false },
  { value: "hy_default", label: "HY 托管（当前不可用）", supported: false },
];

export function presetForGeneration(profile: {
  provider_kind: string;
  base_url: string | null;
}): string {
  return (
    PROVIDER_PRESETS.find(
      (preset) =>
        preset.provider === profile.provider_kind &&
        endpointKey(preset.baseUrl) === endpointKey(profile.base_url ?? ""),
    )?.id ?? "custom"
  );
}

export function generationProviderLabel(provider: ProviderKind): string {
  return (
    GENERATION_PROTOCOLS.find((item) => item.value === provider)?.label ??
    provider
  );
}

export function generationSummary(
  presetId: string,
  provider: ProviderKind,
  model: string,
  text: (value: string) => string,
): string {
  const preset = PROVIDER_PRESETS.find((item) => item.id === presetId);
  return `${preset ? text(preset.label) : text(generationProviderLabel(provider))} · ${
    model.trim() || text("模型待填写")
  }`;
}

export function validGenerationEndpoint(value: string): boolean {
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

export function validateGenerationModel(
  values: GenerationModelValues,
  options: {
    apiKey: string;
    keyRequired: boolean;
    requireRuntimeValues?: boolean;
  },
): GenerationModelErrors {
  const errors: GenerationModelErrors = {};
  if (!values.model.trim()) errors.model = "请填写实际可用的模型 ID。";
  if (!validGenerationEndpoint(values.base_url))
    errors.base_url = "请填写有效的模型接口地址。";
  if (
    options.apiKey.trim() &&
    /^(?:[a-z][a-z\d+.-]*:\/\/|www\.|\/\/)/i.test(options.apiKey.trim())
  )
    errors.api_key = "这里需要 API Key，不是网址。请填写服务方提供的密钥。";
  else if (options.keyRequired && !options.apiKey.trim())
    errors.api_key = "请填写 API Key。";

  const integer = (
    key: "timeout_seconds" | "max_concurrency" | "context_window",
    min: number,
    max: number,
  ) => {
    const raw = values[key].trim();
    if (!raw && !options.requireRuntimeValues) return;
    const value = Number(raw);
    if (!raw || !Number.isInteger(value) || value < min || value > max)
      errors[key] = "onboardingIntegerRange";
  };
  integer("timeout_seconds", 1, 600);
  integer("max_concurrency", 1, 32);
  if (values.context_window.trim()) integer("context_window", 1024, 10_000_000);
  return errors;
}

export function ProviderPresetField({
  id,
  labelledBy,
  ariaLabel,
  describedBy,
  value,
  disabled,
  text,
  includePlaceholder = false,
  onChange,
}: {
  id?: string;
  labelledBy?: string;
  ariaLabel?: string;
  describedBy?: string;
  value: string;
  disabled?: boolean;
  text: (value: string) => string;
  includePlaceholder?: boolean;
  onChange(value: string): void;
}) {
  return (
    <OptionMenu
      id={id}
      labelledBy={labelledBy}
      ariaLabel={ariaLabel}
      describedBy={describedBy}
      value={value}
      disabled={disabled}
      options={[
        ...(includePlaceholder && !value
          ? [{ value: "", label: text("选择服务商"), disabled: true }]
          : []),
        ...PROVIDER_PRESETS.map((preset) => ({
          value: preset.id,
          label: text(preset.label),
        })),
        { value: "custom", label: text("自定义兼容服务") },
      ]}
      onChange={onChange}
    />
  );
}

export function GenerationTechnicalDisclosure({
  open,
  disabled,
  controls,
  text,
  onChange,
  children,
}: {
  open: boolean;
  disabled?: boolean;
  controls: string;
  text: (value: string) => string;
  onChange(open: boolean): void;
  children: ReactNode;
}) {
  return (
    <section className="generation-technical-disclosure">
      <button
        type="button"
        className="generation-technical-toggle"
        aria-expanded={open}
        aria-controls={controls}
        disabled={disabled}
        onClick={() => onChange(!open)}
      >
        {text("配置详情与高级设置")}
        {open ? (
          <ChevronUp size={16} aria-hidden="true" />
        ) : (
          <ChevronDown size={16} aria-hidden="true" />
        )}
      </button>
      <div id={controls} className="generation-technical-fields" hidden={!open}>
        {children}
      </div>
    </section>
  );
}

export function GenerationTechnicalFields({
  idPrefix,
  values,
  errors = {},
  disabled,
  text,
  modelRef,
  onChange,
}: {
  idPrefix: string;
  values: GenerationModelValues;
  errors?: GenerationModelErrors;
  disabled?: boolean;
  text: (value: string) => string;
  modelRef?: Ref<HTMLInputElement>;
  onChange(patch: Partial<GenerationModelValues>): void;
}) {
  const field = (
    key:
      | "model"
      | "base_url"
      | "timeout_seconds"
      | "max_concurrency"
      | "context_window",
    label: string,
    options: {
      type?: string;
      min?: number;
      max?: number;
      placeholder?: string;
      ref?: Ref<HTMLInputElement>;
    } = {},
  ) => {
    const error = errors[key];
    const inputId = `${idPrefix}-${key}`;
    return (
      <label className="generation-model-field" htmlFor={inputId}>
        <span>{text(label)}</span>
        <input
          ref={options.ref}
          id={inputId}
          name={key}
          aria-label={text(label)}
          type={options.type ?? "text"}
          inputMode={options.type === "url" ? "url" : undefined}
          autoComplete="off"
          spellCheck={false}
          value={values[key]}
          disabled={disabled}
          min={options.min}
          max={options.max}
          maxLength={key === "model" ? 256 : undefined}
          placeholder={
            options.placeholder ? text(options.placeholder) : undefined
          }
          aria-invalid={!!error}
          aria-describedby={error ? `${inputId}-error` : undefined}
          onChange={(event) => onChange({ [key]: event.target.value })}
        />
        {error ? (
          <small id={`${inputId}-error`} className="generation-model-error">
            {text(error)}
          </small>
        ) : null}
      </label>
    );
  };

  return (
    <>
      <label
        className="generation-model-field"
        htmlFor={`${idPrefix}-protocol`}
      >
        <span>{text("协议")}</span>
        <select
          id={`${idPrefix}-protocol`}
          name="provider"
          aria-label={text("协议")}
          value={values.provider_kind}
          disabled={disabled}
          onChange={(event) =>
            onChange({ provider_kind: event.target.value as ProviderKind })
          }
        >
          {GENERATION_PROTOCOLS.map((protocol) => (
            <option
              key={protocol.value}
              value={protocol.value}
              disabled={!protocol.supported}
            >
              {text(protocol.label)}
            </option>
          ))}
        </select>
      </label>
      {field("model", "生成模型名", {
        placeholder: "例如：gpt-4o-mini…",
        ref: modelRef,
      })}
      {field("base_url", "Base URL", { type: "url" })}
      <div className="generation-runtime-fields">
        {field("timeout_seconds", "超时（秒）", {
          type: "number",
          min: 1,
          max: 600,
          placeholder: "自动 / 可选",
        })}
        {field("max_concurrency", "最大并发", {
          type: "number",
          min: 1,
          max: 32,
          placeholder: "自动 / 可选",
        })}
        {field("context_window", "上下文窗口", {
          type: "number",
          min: 1024,
          max: 10_000_000,
          placeholder: "自动 / 可选",
        })}
      </div>
      <small className="generation-runtime-note">
        {text("留空使用 Core 默认值。")}
      </small>
      <label className="generation-thinking">
        <input
          name="disable-thinking"
          type="checkbox"
          checked={values.disable_thinking}
          disabled={disabled}
          onChange={(event) =>
            onChange({ disable_thinking: event.target.checked })
          }
        />
        {text("关闭思考模式")}
      </label>
    </>
  );
}

function endpointKey(value: string): string {
  return value.trim().replace(/\/+$/, "").toLowerCase();
}
