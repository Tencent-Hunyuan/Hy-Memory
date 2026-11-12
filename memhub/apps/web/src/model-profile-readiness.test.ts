import type { ModelProbeResult, ModelProfile } from "@one-memory/contracts";
import { expect, it } from "vitest";
import { modelProfileReady, profileFromProbe } from "./model-profile-readiness";

const profile: ModelProfile = {
  profile_id: "saved-1",
  name: "Saved model",
  provider_kind: "openai_compatible",
  base_url: "https://models.example/v1",
  model: "model-1",
  embedding_model: null,
  embedding_provider_kind: null,
  embedding_base_url: null,
  embedding_dimensions: null,
  context_window: null,
  disable_thinking: false,
  has_api_key: true,
  timeout_seconds: 60,
  max_concurrency: 1,
  revision: 1,
  is_active: false,
  created_at: "2026-09-14T00:00:00Z",
  updated_at: "2026-09-14T00:00:00Z",
  capabilities: {
    endpoint_reachable: false,
    model_available: false,
    embedding_available: false,
    structured_output: false,
  },
};

function probe(
  inputRevision: number | null | undefined = 1,
  outputRevision = 2,
): ModelProbeResult {
  return {
    profile_id: profile.profile_id,
    input_profile_revision: inputRevision,
    profile_revision: outputRevision,
    stages: [],
    capabilities: {
      endpoint_reachable: true,
      model_available: true,
      embedding_available: true,
      structured_output: false,
    },
  };
}

it("accepts a measured one-revision bump before or after profile refresh", () => {
  const receipt = probe();
  const beforeRefresh = profileFromProbe(profile, receipt);
  const afterRefresh = profileFromProbe({ ...profile, revision: 2 }, receipt);

  expect(beforeRefresh).toMatchObject({ revision: 2 });
  expect(afterRefresh).toMatchObject({ revision: 2 });
  expect(modelProfileReady(beforeRefresh as ModelProfile, receipt)).toBe(true);
});

it("rejects a bump without input evidence, a skipped revision and another profile", () => {
  expect(profileFromProbe(profile, probe(null, 2))).toBeNull();
  expect(profileFromProbe(profile, probe(1, 3))).toBeNull();
  expect(
    profileFromProbe(profile, {
      ...probe(),
      profile_id: "another-profile",
    }),
  ).toBeNull();
});

it("keeps unavailable credentials from becoming ready", () => {
  const receipt = probe();
  const tested = profileFromProbe(
    { ...profile, credential_state: "unavailable" },
    receipt,
  );
  expect(tested).not.toBeNull();
  expect(modelProfileReady(tested as ModelProfile, receipt)).toBe(false);
});
