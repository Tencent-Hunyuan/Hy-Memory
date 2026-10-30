import type { ModelProfile } from "@one-memory/contracts";
import type { CoreReader } from "./core-client.js";
import { validateModelProfiles } from "./core-responses.js";
import { PRODUCT } from "./version.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

interface SafeModelProfile {
  profile_id: string;
  name: string;
  provider_kind: ModelProfile["provider_kind"];
  model: string;
  generation_endpoint: "default" | "custom";
  embedding_model: string | null;
  embedding_provider_kind: ModelProfile["embedding_provider_kind"];
  embedding_mode: "local" | "remote" | "unconfigured";
  embedding_dimensions: number | null;
  credential_state: "available" | "missing" | "unavailable";
  timeout_seconds: number;
  max_concurrency: number;
  context_window: number | null;
  disable_thinking: boolean;
  capabilities: ModelProfile["capabilities"];
  revision: number;
  is_active: boolean;
}

export type ModelReportDocument =
  | {
      schema_version: 1;
      product: string;
      state: "ok";
      profiles: SafeModelProfile[];
    }
  | {
      schema_version: 1;
      product: string;
      state: "ok";
      profile: SafeModelProfile;
    }
  | {
      schema_version: 1;
      product: string;
      state: "denied" | "unavailable" | "not_found" | "invalid" | "rejected";
      code: string;
    };

export async function readModelProfiles(
  reader: CoreReader,
): Promise<ModelReportDocument> {
  const outcome = await reader<unknown>("/api/v1/model-profiles");
  if (outcome.state !== "ok") {
    return failure(outcome.state, outcome.code);
  }
  const profiles = validateModelProfiles(outcome.data);
  if (!profiles) return failure("invalid", "model_profiles_response_invalid");
  return {
    schema_version: 1,
    product: PRODUCT,
    state: "ok",
    profiles: profiles.map(project),
  };
}

export async function readModelProfile(
  reader: CoreReader,
  profileId: string,
): Promise<ModelReportDocument> {
  if (!UUID.test(profileId)) return failure("invalid", "invalid_profile_id");
  const all = await readModelProfiles(reader);
  if (all.state !== "ok" || !("profiles" in all)) return all;
  const matches = all.profiles.filter(
    (profile) => profile.profile_id === profileId,
  );
  return matches.length === 1
    ? {
        schema_version: 1,
        product: PRODUCT,
        state: "ok",
        profile: matches[0],
      }
    : failure("not_found", "model_profile_not_found");
}

export function modelReportExitCode(document: ModelReportDocument): number {
  return document.state === "ok" ? 0 : 1;
}

export function renderModelReportText(document: ModelReportDocument): string {
  if (document.state !== "ok") {
    return `模型配置不可读：${document.state} (${document.code})\n`;
  }
  const profiles =
    "profiles" in document ? document.profiles : [document.profile];
  if (profiles.length === 0) return "尚未配置模型。\n";
  return `${profiles
    .map(
      (profile) =>
        `${profile.is_active ? "*" : "-"} ${profile.name} (${profile.profile_id}) ` +
        `${profile.provider_kind}/${profile.model} embedding=${profile.embedding_mode} ` +
        `credential=${profile.credential_state} rev=${profile.revision}`,
    )
    .join("\n")}\n`;
}

function project(profile: ModelProfile): SafeModelProfile {
  const effectiveEmbeddingProvider =
    profile.embedding_provider_kind ?? profile.provider_kind;
  const embeddingMode =
    effectiveEmbeddingProvider === "local_onnx"
      ? "local"
      : profile.embedding_model
        ? "remote"
        : "unconfigured";
  return {
    profile_id: profile.profile_id,
    name: profile.name,
    provider_kind: profile.provider_kind,
    model: profile.model,
    generation_endpoint: profile.base_url ? "custom" : "default",
    embedding_model: profile.embedding_model,
    embedding_provider_kind: profile.embedding_provider_kind,
    embedding_mode: embeddingMode,
    embedding_dimensions: profile.embedding_dimensions,
    credential_state:
      profile.credential_state ??
      (profile.has_api_key ? "available" : "missing"),
    timeout_seconds: profile.timeout_seconds,
    max_concurrency: profile.max_concurrency,
    context_window: profile.context_window,
    disable_thinking: profile.disable_thinking,
    capabilities: { ...profile.capabilities },
    revision: profile.revision,
    is_active: profile.is_active,
  };
}

function failure(
  state: "denied" | "unavailable" | "not_found" | "invalid" | "rejected",
  code: string,
): ModelReportDocument {
  return { schema_version: 1, product: PRODUCT, state, code };
}
