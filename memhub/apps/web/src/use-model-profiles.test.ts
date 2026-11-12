// @vitest-environment jsdom
import type {
  ModelProbeResult,
  ModelProfile,
  ModelProfileInput,
} from "@one-memory/contracts";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "./api";
import { useModelProfiles } from "./use-model-profiles";

vi.mock("./api", () => ({
  getModelProfiles: vi.fn(),
  getModelCreationOperation: vi.fn(),
  createModelProfile: vi.fn(),
  updateModelProfile: vi.fn(),
  activateModelProfile: vi.fn(),
  deleteModelProfile: vi.fn(),
  probeModelProfile: vi.fn(),
  apiErrorCode: vi.fn(() => null),
}));

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
const input: ModelProfileInput = {
  name: profile.name,
  provider_kind: "openai_compatible",
  model: profile.model,
  base_url: profile.base_url,
  api_key: "synthetic-secret",
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
beforeEach(() => {
  vi.resetAllMocks();
  window.localStorage.clear();
  vi.mocked(api.getModelProfiles).mockResolvedValue([]);
  vi.mocked(api.createModelProfile).mockResolvedValue(profile);
  vi.mocked(api.getModelCreationOperation).mockImplementation(
    async (operationId) => ({
      operation_id: operationId,
      state: "committed",
      profile_id: profile.profile_id,
      created_revision: 1,
    }),
  );
});
afterEach(cleanup);

it("returns and retains the authoritative created profile when list refresh fails", async () => {
  const { result } = renderHook(useModelProfiles);
  await waitFor(() => expect(api.getModelProfiles).toHaveBeenCalledTimes(1));
  vi.mocked(api.getModelProfiles).mockRejectedValueOnce(new Error("offline"));
  let saved: ModelProfile | null | undefined;
  await act(async () => {
    saved = await result.current.create(input);
  });
  expect(saved).toEqual(profile);
  expect(result.current.profiles).toEqual([profile]);
  expect(result.current.error).toBeNull();
  expect(result.current.refreshError).toContain("已完成");
  expect(api.createModelProfile).toHaveBeenCalledTimes(1);
  vi.mocked(api.getModelProfiles).mockResolvedValue([profile]);
  await act(async () => {
    await result.current.refreshProfiles();
  });
  expect(result.current.refreshError).toBeNull();
  expect(api.createModelProfile).toHaveBeenCalledTimes(1);
});

it("publishes the saved profile while its refresh is still pending and merges duplicate create calls", async () => {
  const { result } = renderHook(useModelProfiles);
  await waitFor(() => expect(api.getModelProfiles).toHaveBeenCalledTimes(1));
  const refresh = deferred<ModelProfile[]>();
  vi.mocked(api.getModelProfiles).mockReturnValueOnce(refresh.promise);
  let first!: Promise<ModelProfile | null | undefined>;
  let second!: Promise<ModelProfile | null | undefined>;
  await act(async () => {
    first = result.current.create(input);
    second = result.current.create(input);
  });
  expect(result.current.profiles).toEqual([profile]);
  expect(api.createModelProfile).toHaveBeenCalledTimes(1);
  await act(async () => {
    refresh.resolve([profile]);
    await Promise.all([first, second]);
  });
  expect(await first).toEqual(profile);
  expect(await second).toEqual(profile);
});

it("ignores an initial list arriving after a newer save", async () => {
  const initial = deferred<ModelProfile[]>();
  vi.mocked(api.getModelProfiles)
    .mockReturnValueOnce(initial.promise)
    .mockResolvedValue([profile]);
  const { result } = renderHook(useModelProfiles);
  await act(async () => {
    await result.current.create(input);
  });
  await act(async () => {
    initial.resolve([]);
  });
  expect(result.current.profiles).toEqual([profile]);
});

it("retains failed create for reconciliation without resending or exposing the key", async () => {
  const { result } = renderHook(useModelProfiles);
  await waitFor(() => expect(api.getModelProfiles).toHaveBeenCalledTimes(1));
  vi.mocked(api.createModelProfile).mockRejectedValueOnce(
    new Error("failed synthetic-secret"),
  );
  await act(async () => {
    expect(await result.current.create(input)).toBeUndefined();
  });
  expect(result.current.profiles).toEqual([]);
  expect(result.current.error).not.toContain("synthetic-secret");
  expect(result.current.error).toBeTruthy();
  expect(result.current.pendingCreation).toBeTruthy();
  await act(async () => {
    await result.current.create(input);
  });
  expect(api.createModelProfile).toHaveBeenCalledTimes(1);
  expect(api.getModelCreationOperation).not.toHaveBeenCalled();
});

it("does not refresh or leak a late operation into a new mounted hook", async () => {
  const operation = deferred<ModelProfile>();
  vi.mocked(api.createModelProfile).mockReturnValueOnce(operation.promise);
  const first = renderHook(useModelProfiles);
  await waitFor(() => expect(api.getModelProfiles).toHaveBeenCalledTimes(1));
  let saved!: Promise<ModelProfile | null | undefined>;
  act(() => {
    saved = first.result.current.create(input);
  });
  first.unmount();
  const next = renderHook(useModelProfiles);
  await act(async () => {
    operation.resolve(profile);
    await saved;
  });
  expect(next.result.current.profiles).toEqual([]);
  expect(api.getModelProfiles).toHaveBeenCalledTimes(2);
});

it("does not let an older refresh overwrite a later activation", async () => {
  const { result } = renderHook(useModelProfiles);
  await waitFor(() => expect(api.getModelProfiles).toHaveBeenCalledTimes(1));
  const old = deferred<ModelProfile[]>();
  const active = { ...profile, is_active: true, revision: 2 };
  vi.mocked(api.getModelProfiles)
    .mockReturnValueOnce(old.promise)
    .mockResolvedValueOnce([active]);
  vi.mocked(api.activateModelProfile).mockResolvedValueOnce(active);
  await act(async () => {
    await result.current.create(input);
  });
  await act(async () => {
    await result.current.activate(profile.profile_id);
  });
  await act(async () => {
    old.resolve([profile]);
  });
  expect(result.current.profiles).toEqual([active]);
});

it("keeps update and delete receipts when their follow-up reads fail", async () => {
  vi.mocked(api.getModelProfiles)
    .mockResolvedValueOnce([profile])
    .mockRejectedValue(new Error("offline"));
  const { result } = renderHook(useModelProfiles);
  await waitFor(() => expect(result.current.profiles).toEqual([profile]));
  const updated = { ...profile, revision: 2, name: "Updated" };
  vi.mocked(api.updateModelProfile).mockResolvedValueOnce(updated);
  await act(async () => {
    expect(
      await result.current.update(profile.profile_id, {
        expected_revision: 1,
        name: "Updated",
      }),
    ).toEqual(updated);
  });
  expect(result.current.profiles).toEqual([updated]);
  await act(async () => {
    await result.current.remove(profile.profile_id);
  });
  expect(result.current.profiles).toEqual([]);
  expect(result.current.refreshError).toBeTruthy();
});

it("blocks other writes and retains pending after a synchronous create failure", async () => {
  const { result } = renderHook(useModelProfiles);
  const operation = deferred<ModelProfile>();
  vi.mocked(api.createModelProfile).mockReturnValueOnce(operation.promise);
  let saved!: Promise<ModelProfile | null | undefined>;
  await act(async () => {
    saved = result.current.create(input);
    expect(await result.current.activate(profile.profile_id)).toBeUndefined();
  });
  expect(api.activateModelProfile).not.toHaveBeenCalled();
  await act(async () => {
    operation.resolve(profile);
    await saved;
  });
  vi.mocked(api.createModelProfile).mockImplementationOnce(() => {
    throw new Error("offline");
  });
  await act(async () => {
    await result.current.create(input);
  });
  expect(result.current.busy).toBeNull();
  await act(async () => {
    await result.current.create(input);
  });
  expect(api.createModelProfile).toHaveBeenCalledTimes(2);
  expect(result.current.pendingCreation).toBeTruthy();
});

function probeResult(value = profile): ModelProbeResult {
  return {
    profile_id: value.profile_id,
    profile_revision: value.revision,
    capabilities: {
      ...value.capabilities,
      endpoint_reachable: true,
      model_available: true,
      embedding_available: true,
    },
    stages: [{ stage: "generation", ok: true, code: "ok", latency_ms: 2 }],
  };
}

it("attaches each probe to its own profile and revision and hides it after an edit", async () => {
  const other = {
    ...profile,
    profile_id: "other",
    base_url: "https://other.example/v1",
  };
  vi.mocked(api.getModelProfiles).mockResolvedValue([profile, other]);
  const { result } = renderHook(useModelProfiles);
  await waitFor(() => expect(result.current.profiles).toHaveLength(2));
  vi.mocked(api.probeModelProfile)
    .mockResolvedValueOnce(probeResult())
    .mockResolvedValueOnce(probeResult(other));
  await act(async () => {
    await result.current.probeProfile(profile.profile_id);
  });
  await act(async () => {
    await result.current.probeProfile(other.profile_id);
  });
  expect(result.current.probeFor(profile)).toEqual(probeResult());
  expect(result.current.probeFor(other)).toEqual(probeResult(other));
  const updated = { ...profile, revision: 2, model: "changed" };
  vi.mocked(api.updateModelProfile).mockResolvedValue(updated);
  vi.mocked(api.getModelProfiles).mockResolvedValue([updated, other]);
  await act(async () => {
    await result.current.update(profile.profile_id, {
      expected_revision: 1,
      model: "changed",
    });
  });
  expect(result.current.probeFor(updated)).toBeNull();
  expect(result.current.probeFor(other)).toEqual(probeResult(other));
  expect(api.createModelProfile).not.toHaveBeenCalled();
});

it("records a failed test only for the attempted revision and rejects a mismatched result", async () => {
  vi.mocked(api.getModelProfiles).mockResolvedValue([profile]);
  const { result } = renderHook(useModelProfiles);
  await waitFor(() => expect(result.current.profiles).toHaveLength(1));
  vi.mocked(api.probeModelProfile).mockRejectedValueOnce(
    new Error("private key leak"),
  );
  await act(async () => {
    await result.current.probeProfile(profile.profile_id);
  });
  expect(result.current.probeErrorFor(profile)).toBeTruthy();
  expect(result.current.probeErrorFor(profile)).not.toContain("private key");
  expect(result.current.error).toBeNull();
  expect(result.current.probeErrorFor({ ...profile, revision: 2 })).toBeNull();
  vi.mocked(api.probeModelProfile).mockResolvedValueOnce({
    ...probeResult(),
    profile_id: "wrong-profile",
  });
  await act(async () => {
    await result.current.probeProfile(profile.profile_id);
  });
  expect(result.current.probeFor(profile)).toBeNull();
  expect(result.current.profiles[0].capabilities).toEqual(profile.capabilities);
});

it("commits a probe-induced revision before the follow-up refresh returns", async () => {
  vi.mocked(api.getModelProfiles).mockResolvedValueOnce([profile]);
  const { result } = renderHook(useModelProfiles);
  await waitFor(() => expect(result.current.profiles).toHaveLength(1));
  const updated = {
    ...profile,
    revision: 2,
    embedding_dimensions: 512,
    capabilities: {
      ...profile.capabilities,
      endpoint_reachable: true,
      model_available: true,
      embedding_available: true,
    },
  };
  const receipt = {
    ...probeResult(updated),
    input_profile_revision: profile.revision,
  };
  const delayed = deferred<ModelProfile[]>();
  vi.mocked(api.getModelProfiles).mockReturnValueOnce(delayed.promise);
  vi.mocked(api.probeModelProfile).mockResolvedValueOnce(receipt);
  await act(async () => {
    await result.current.probeProfile(profile.profile_id);
  });
  expect(result.current.profiles[0]).toMatchObject({
    revision: 2,
    capabilities: receipt.capabilities,
  });
  expect(result.current.probeFor(result.current.profiles[0])).toEqual(receipt);
  await act(async () => {
    delayed.resolve([updated]);
  });
  expect(result.current.probeFor(result.current.profiles[0])).toEqual(receipt);
});
