import { describe, expect, it } from "vitest";

import {
  CORE_SERVICE,
  type HomeProjectionV1,
  type ModelProfileInput,
  type OnboardingProgressUpdate,
  type OnboardingState,
  type RecallReceipt,
} from "./index";

describe("Core contract", () => {
  it("has one stable service identity", () => {
    expect(CORE_SERVICE).toBe("one-memory-core");
  });

  it("types a raw-mode recall receipt: excerpts, not memories", () => {
    // Compile-time check as much as a runtime one: this literal must satisfy
    // the receipt type Core sends when it falls back to raw conversations.
    const receipt: RecallReceipt = {
      state: "grounded",
      query: "deploy plan",
      scope: "all-apps",
      mode: "raw",
      fallback_reason: "model_unavailable",
      excluded_sources: ["codex"],
      hits: [
        {
          memory_id: "raw-1",
          content: "we agreed to deploy on Friday",
          content_format: "raw",
          kind: "raw",
          source: "cursor",
          session_id: "session-1",
          scopes: [],
          evidence_ids: [],
          updated_at: "2026-10-27T00:00:00Z",
          score: 0.5,
        },
      ],
      used_characters: 29,
      truncated: false,
    };
    expect(receipt.mode).toBe("raw");
    expect(receipt.hits[0].evidence_ids).toEqual([]);
  });

  it("types the discriminated home projection without raw payload fields", () => {
    const projection: HomeProjectionV1 = {
      projection_version: 1,
      user_id: "local",
      effective_mode: "lite",
      capabilities: {
        content_plane: "raw",
        generation_required: false,
        graph_enabled: false,
        evolve_available: false,
      },
      visible_count: 1,
      display_state: "excerpt",
      latest: {
        type: "raw",
        raw_id: "raw-1",
        session_id: "session-1",
        display_state: "excerpt",
        time: null,
        time_basis: null,
        excerpt: "ship Friday",
        truncated: false,
        source_id: "cursor",
      },
      observed_at: "2026-11-03T00:00:00Z",
    };
    expect(projection.latest?.type).toBe("raw");
  });

  it("types resumable onboarding progress without secret fields", () => {
    const update: OnboardingProgressUpdate = {
      mode_intent: "pro",
      setup_step: "model",
      model_profile_id: "9be6d465-4cf2-4c72-93f7-15f3c012bc80",
    };
    const state: OnboardingState = {
      status: "not_started",
      selected_source_ids: [],
      started_at: null,
      completed_at: null,
      ...update,
      mode_intent: update.mode_intent ?? null,
      setup_step: update.setup_step ?? null,
      model_profile_id: update.model_profile_id ?? null,
    };
    // @ts-expect-error onboarding progress does not carry credentials.
    const secret: OnboardingProgressUpdate = { api_key: "must-not-compile" };

    expect(state.setup_step).toBe("model");
    expect(secret).toEqual({ api_key: "must-not-compile" });
  });

  it("allows Core to own omitted embedding and runtime defaults", () => {
    const input: ModelProfileInput = {
      name: "DeepSeek",
      provider_kind: "openai_compatible",
      base_url: "https://api.deepseek.com",
      model: "deepseek-flash",
    };
    expect(input.embedding_model).toBeUndefined();
    expect(input.timeout_seconds).toBeUndefined();
    expect(input.max_concurrency).toBeUndefined();
    expect(input.context_window).toBeUndefined();
  });
});
