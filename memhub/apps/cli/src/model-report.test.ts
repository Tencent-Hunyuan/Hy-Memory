import { describe, expect, it, vi } from "vitest";

import type { CoreReader } from "./core-client.js";
import { readModelProfile, readModelProfiles } from "./model-report.js";

const PROFILE = {
  profile_id: "22222222-2222-4222-8222-222222222222",
  name: "Synthetic",
  provider_kind: "openai_compatible",
  base_url: "https://internal.example.invalid/v1",
  model: "synthetic-model",
  embedding_model: "local-model",
  embedding_provider_kind: "local_onnx",
  embedding_base_url: null,
  embedding_dimensions: 768,
  has_api_key: true,
  credential_state: "available",
  timeout_seconds: 30,
  max_concurrency: 2,
  context_window: 32_000,
  disable_thinking: true,
  capabilities: {
    endpoint_reachable: true,
    model_available: true,
    structured_output: false,
    embedding_available: true,
  },
  revision: 4,
  is_active: true,
  created_at: "2026-09-19T00:00:00Z",
  updated_at: "2026-09-19T00:00:00Z",
};

describe("model profile reports", () => {
  it("lists bounded safe configuration and distinguishes local embedding", async () => {
    const document = await readModelProfiles(
      vi.fn().mockResolvedValue({ state: "ok", data: [PROFILE] }) as CoreReader,
    );
    expect(document).toMatchObject({
      state: "ok",
      profiles: [
        {
          profile_id: PROFILE.profile_id,
          generation_endpoint: "custom",
          embedding_mode: "local",
          credential_state: "available",
          revision: 4,
          is_active: true,
        },
      ],
    });
    expect(JSON.stringify(document)).not.toContain("internal.example.invalid");
  });

  it("reports the output-token field, max_tokens from an older Core", async () => {
    const chosen = await readModelProfiles(
      vi.fn().mockResolvedValue({
        state: "ok",
        data: [{ ...PROFILE, output_token_parameter: "max_completion_tokens" }],
      }) as CoreReader,
    );
    expect(chosen).toMatchObject({
      profiles: [{ output_token_parameter: "max_completion_tokens" }],
    });
    const older = await readModelProfiles(
      vi.fn().mockResolvedValue({ state: "ok", data: [PROFILE] }) as CoreReader,
    );
    expect(older).toMatchObject({
      profiles: [{ output_token_parameter: "max_tokens" }],
    });
  });

  it("shows one exact profile and rejects malformed success DTOs", async () => {
    const reader = vi.fn().mockResolvedValue({ state: "ok", data: [PROFILE] });
    const shown = await readModelProfile(
      reader as CoreReader,
      PROFILE.profile_id,
    );
    expect(shown).toMatchObject({
      state: "ok",
      profile: { profile_id: PROFILE.profile_id, max_concurrency: 2 },
    });

    const malformed = await readModelProfiles(
      vi.fn().mockResolvedValue({
        state: "ok",
        data: [{ ...PROFILE, model: { secret: "must-not-leak" } }],
      }) as CoreReader,
    );
    expect(malformed).toMatchObject({
      state: "invalid",
      code: "model_profiles_response_invalid",
    });
    expect(JSON.stringify(malformed)).not.toContain("must-not-leak");
  });
});
