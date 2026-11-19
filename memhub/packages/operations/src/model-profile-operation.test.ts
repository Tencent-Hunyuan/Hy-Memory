import type { ModelProfile, ModelProfileInput } from "@one-memory/contracts";
import { describe, expect, it } from "vitest";
import {
  buildModelProfileCreateRequest,
  buildModelProfileUpdateRequest,
  ModelProfileInputError,
  parseModelProfileReceipt,
} from "./model-profile-operation";

const OP = "11111111-2222-4333-8444-555555555555";

function input(patch: Partial<ModelProfileInput> = {}): ModelProfileInput {
  return {
    name: "Isolated model",
    provider_kind: "openai_compatible",
    base_url: "https://generation.example/v1",
    model: "generation-model",
    timeout_seconds: 30,
    max_concurrency: 1,
    ...patch,
  };
}

describe("model profile create request", () => {
  it("matches the CLI body: config plus creation id, credential separate", () => {
    const { path, body } = buildModelProfileCreateRequest(
      input({ api_key: "sk-test" }),
      OP,
    );
    expect(path).toBe("/api/v1/model-profiles");
    expect(body).toEqual({
      name: "Isolated model",
      provider_kind: "openai_compatible",
      base_url: "https://generation.example/v1",
      model: "generation-model",
      timeout_seconds: 30,
      max_concurrency: 1,
      creation_operation_id: OP,
      api_key: "sk-test",
    });
  });

  it("omits api_key when the caller has no credential", () => {
    const { body } = buildModelProfileCreateRequest(input(), OP);
    expect(body).not.toHaveProperty("api_key");
    expect(body.creation_operation_id).toBe(OP);
  });

  it("rejects a stray config key rather than silently dropping it", () => {
    // The CLI's allow-list would refuse the body; mirroring it here means the
    // UI fails with a specific field instead of Core's generic error.
    expect(() =>
      buildModelProfileCreateRequest(
        { ...input(), unexpected: true } as ModelProfileInput,
        OP,
      ),
    ).toThrow(ModelProfileInputError);
  });

  it("enforces the same bounds as the CLI", () => {
    const cases: Array<[Partial<ModelProfileInput>, string]> = [
      [{ name: "" }, "name_invalid"],
      [{ provider_kind: "bedrock" as never }, "provider_kind_invalid"],
      [{ timeout_seconds: 601 }, "timeout_seconds_invalid"],
      [{ max_concurrency: 33 }, "max_concurrency_invalid"],
      [{ context_window: 512 }, "context_window_invalid"],
      [{ embedding_dimensions: 0 }, "embedding_dimensions_invalid"],
      [{ disable_thinking: "yes" as never }, "disable_thinking_invalid"],
      [
        { output_token_parameter: "max_output_tokens" as never },
        "output_token_parameter_invalid",
      ],
    ];
    for (const [patch, code] of cases) {
      expect(() => buildModelProfileCreateRequest(input(patch), OP)).toThrow(
        code,
      );
    }
  });

  it("carries the profile's output-token field (#287)", () => {
    const { body } = buildModelProfileCreateRequest(
      input({ output_token_parameter: "max_completion_tokens" }),
      OP,
    );
    expect(body.output_token_parameter).toBe("max_completion_tokens");
    const patch = buildModelProfileUpdateRequest("setup-1", {
      expected_revision: 2,
      output_token_parameter: "max_completion_tokens",
    });
    expect(patch.body).toMatchObject({
      output_token_parameter: "max_completion_tokens",
    });
  });

  it("requires a well-formed creation operation id", () => {
    expect(() => buildModelProfileCreateRequest(input(), "short")).toThrow(
      "creation_operation_id_invalid",
    );
  });
});

const PROFILE: ModelProfile = {
  profile_id: "22222222-3333-4444-8555-666666666666",
  name: "Isolated model",
  provider_kind: "openai_compatible",
  base_url: "https://generation.example/v1",
  model: "generation-model",
  embedding_model: null,
  embedding_provider_kind: null,
  embedding_base_url: null,
  embedding_dimensions: null,
  has_api_key: false,
  credential_state: "missing",
  timeout_seconds: 30,
  max_concurrency: 1,
  context_window: null,
  disable_thinking: false,
  capabilities: {
    endpoint_reachable: true,
    model_available: true,
    structured_output: false,
    embedding_available: false,
  },
  revision: 1,
  is_active: true,
  created_at: "2026-09-30T00:00:00Z",
  updated_at: "2026-09-30T00:00:00Z",
};

describe("model profile write receipt", () => {
  it("accepts a well-formed profile", () => {
    expect(parseModelProfileReceipt(PROFILE)).toEqual(PROFILE);
  });

  it("accepts a profile whose credential_state is absent or null", () => {
    const { credential_state: _drop, ...withoutState } = PROFILE;
    expect(parseModelProfileReceipt(withoutState)).toEqual(withoutState);
    expect(
      parseModelProfileReceipt({ ...PROFILE, credential_state: null }),
    ).not.toBeNull();
  });

  it("rejects a body that is not a model profile", () => {
    for (const bad of [null, [], "profile", 42, {}]) {
      expect(parseModelProfileReceipt(bad)).toBeNull();
    }
  });

  // Each case is a real CLI-side rejection from `validateModelProfile` /
  // `validateModelProfiles`; a malformed 2xx must become an unknown result,
  // never a rendered profile.
  it("rejects every field the CLI rejects", () => {
    const cases: Array<[string, Partial<ModelProfile>]> = [
      ["name", { name: "x".repeat(257) }],
      ["provider_kind", { provider_kind: "bedrock" as never }],
      ["model", { model: "x".repeat(257) }],
      ["has_api_key", { has_api_key: "yes" as never }],
      ["is_active", { is_active: 1 as never }],
      ["credential_state", { credential_state: "unsure" as never }],
      ["profile_id", { profile_id: "" }],
      ["profile_id", { profile_id: "x".repeat(129) }],
      ["revision", { revision: 0 }],
      ["revision", { revision: 1.5 }],
      ["revision", { revision: "1" as never }],
    ];
    for (const [field, patch] of cases) {
      expect(
        parseModelProfileReceipt({ ...PROFILE, ...patch }),
        `expected ${field}=${JSON.stringify(patch[field as keyof ModelProfile])} to be rejected`,
      ).toBeNull();
    }
  });

  // `core-responses.ts` `text()` bounds only the maximum, so an empty name or
  // model is *not* a CLI rejection there; mirroring that exactly avoids the UI
  // and CLI disagreeing about a body Core actually returned.
  it("accepts an empty name or model, as the CLI's text() bound does", () => {
    expect(
      parseModelProfileReceipt({ ...PROFILE, name: "", model: "" }),
    ).not.toBeNull();
  });
});

describe("model profile update request", () => {
  const base = { expected_revision: 3, name: "Renamed" };

  it("builds the CLI's PATCH path and body", () => {
    const { path, body } = buildModelProfileUpdateRequest("setup-1", base);
    expect(path).toBe("/api/v1/model-profiles/setup-1");
    expect(body).toEqual({ expected_revision: 3, name: "Renamed" });
  });

  it("encodes the profile id rather than pasting it into the path", () => {
    expect(buildModelProfileUpdateRequest("a b/c", base).path).toBe(
      "/api/v1/model-profiles/a%20b%2Fc",
    );
  });

  // The CLI's `validatePatch` refuses a body with any key outside its
  // allow-list; the Web used to post the object straight through.
  it("rejects a stray patch key rather than silently dropping it", () => {
    expect(() =>
      buildModelProfileUpdateRequest("setup-1", {
        ...base,
        unexpected: true,
      }),
    ).toThrow(ModelProfileInputError);
  });

  it("requires a positive integer expected_revision (the CAS guard)", () => {
    for (const expected_revision of [undefined, 0, -1, 1.5, "3"]) {
      expect(() =>
        buildModelProfileUpdateRequest("setup-1", { expected_revision }),
      ).toThrow("expected_revision_invalid");
    }
  });

  it("enforces the same bounds as the CLI", () => {
    const cases: Array<[Record<string, unknown>, string]> = [
      [{ ...base, name: "" }, "name_invalid"],
      [{ ...base, model: "" }, "model_invalid"],
      [{ ...base, timeout_seconds: 601 }, "timeout_seconds_invalid"],
      [{ ...base, max_concurrency: 33 }, "max_concurrency_invalid"],
      [{ ...base, context_window: 512 }, "context_window_invalid"],
      [{ ...base, embedding_dimensions: 0 }, "embedding_dimensions_invalid"],
      [{ ...base, disable_thinking: "yes" }, "disable_thinking_invalid"],
      [
        { ...base, output_token_parameter: "max_output_tokens" },
        "output_token_parameter_invalid",
      ],
    ];
    for (const [patch, code] of cases) {
      expect(() => buildModelProfileUpdateRequest("setup-1", patch)).toThrow(
        code,
      );
    }
  });

  // `api_key` and `clear_api_key` are mutually exclusive: Core cannot both
  // replace and clear the credential in one write.
  it("rejects api_key together with clear_api_key", () => {
    expect(() =>
      buildModelProfileUpdateRequest("setup-1", {
        ...base,
        api_key: "sk-test",
        clear_api_key: true,
      }),
    ).toThrow("api_key_and_clear_conflict");
  });

  it("accepts a null to clear a nullable field", () => {
    const { body } = buildModelProfileUpdateRequest("setup-1", {
      ...base,
      base_url: null,
      embedding_model: null,
    });
    expect(body).toMatchObject({ base_url: null, embedding_model: null });
  });
});
