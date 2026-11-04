import type { ProviderKind } from "@one-memory/contracts";

/** Strings preserve partial numeric edits. The owner validates and maps to API payloads. */
export interface OnboardingModelDraft {
  provider_id: string;
  name: string;
  provider_kind: ProviderKind;
  model: string;
  base_url: string;
  api_key: string;
  disable_thinking: boolean;
  /** Retained in controller state only for persisted-profile recovery; cold start never renders or writes it. */
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
  focusToken?: number;
  onActionError?(action: Action): void;
}

export { OnboardingModelStepNext as OnboardingModelStep } from "./OnboardingModelStepNext";
