// @vitest-environment jsdom
import type {
  ModelCreationOperation,
  ModelProfile,
  ModelProfileInput,
} from "@one-memory/contracts";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "./api";
import {
  readPendingModelCreation,
  savePendingModelCreation,
} from "./pending-model-creation";
import {
  clearPendingModelProbe,
  savePendingModelProbe,
} from "./pending-model-probe";
import { useModelProfiles } from "./use-model-profiles";

vi.mock("./api", () => ({
  abandonModelCreationOperation: vi.fn(),
  getModelProfiles: vi.fn(),
  getModelCreationOperation: vi.fn(),
  createModelProfile: vi.fn(),
  updateModelProfile: vi.fn(),
  activateModelProfile: vi.fn(),
  deleteModelProfile: vi.fn(),
  probeModelProfile: vi.fn(),
  apiErrorCode: vi.fn(() => null),
}));
const ID = "b75069a9-cc67-4698-9d51-3f2860ecf51d";
const OTHER_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const KEY = "one-memory.pending-model-creation.v1";
const profile: ModelProfile = {
  profile_id: "saved-1",
  name: "Synthetic model",
  provider_kind: "openai_compatible",
  base_url: "https://fixture.example/v1",
  model: "fixture-model",
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
  created_at: "2026-09-19T00:00:00Z",
  updated_at: "2026-09-19T00:00:00Z",
  capabilities: {
    endpoint_reachable: false,
    model_available: false,
    embedding_available: false,
    structured_output: false,
  },
};
const input: ModelProfileInput = {
  name: profile.name,
  provider_kind: profile.provider_kind,
  model: profile.model,
  base_url: profile.base_url,
  api_key: "DO_NOT_PERSIST_SYNTHETIC_KEY",
};
const committed = (operationId = ID): ModelCreationOperation => ({
  operation_id: operationId,
  state: "committed",
  profile_id: profile.profile_id,
  created_revision: 1,
});
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
  vi.mocked(api.abandonModelCreationOperation).mockImplementation(
    async (id) => ({
      operation_id: id,
      state: "deleted",
      profile_id: OTHER_ID,
      created_revision: 1,
    }),
  );
  vi.mocked(api.getModelCreationOperation).mockImplementation(async (id) =>
    committed(id),
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

it("restores a pending probe and blocks configuration writes until explicit reconciliation", async () => {
  const pending = {
    operationId: ID,
    profileId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    inputRevision: 1,
  };
  const saved = { ...profile, profile_id: pending.profileId };
  savePendingModelProbe(pending);
  vi.mocked(api.getModelProfiles).mockResolvedValue([saved]);
  const hook = renderHook(useModelProfiles);
  await waitFor(() => expect(hook.result.current.profiles).toEqual([saved]));
  expect(hook.result.current.pendingProbe).toEqual(pending);
  await act(async () => {
    await hook.result.current.create(input);
    await hook.result.current.update(saved.profile_id, {
      expected_revision: 1,
      name: "changed",
    });
    await hook.result.current.activate(saved.profile_id);
  });
  expect(api.createModelProfile).not.toHaveBeenCalled();
  expect(api.updateModelProfile).not.toHaveBeenCalled();
  expect(api.activateModelProfile).not.toHaveBeenCalled();
  vi.mocked(api.probeModelProfile).mockImplementation(async () => {
    clearPendingModelProbe(ID);
    return {
      profile_id: saved.profile_id,
      profile_revision: 1,
      input_profile_revision: 1,
      capabilities: saved.capabilities,
      stages: [],
    };
  });
  await act(async () => {
    await hook.result.current.probeProfile(saved.profile_id);
  });
  expect(api.probeModelProfile).toHaveBeenCalledExactlyOnceWith(
    saved.profile_id,
    1,
    expect.any(String),
  );
  expect(hook.result.current.pendingProbe).toBeNull();
  expect(api.activateModelProfile).not.toHaveBeenCalled();
});

it("allows receipt reconciliation for a pending probe whose profile was deleted", async () => {
  const profileId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  savePendingModelProbe({ operationId: ID, profileId, inputRevision: 2 });
  const hook = renderHook(useModelProfiles);
  await waitFor(() =>
    expect(hook.result.current.pendingProbe?.profileId).toBe(profileId),
  );
  vi.mocked(api.probeModelProfile).mockImplementation(async () => {
    clearPendingModelProbe(ID);
    throw new Error("model_probe_operation_stale");
  });
  await act(async () => {
    await hook.result.current.probeProfile(profileId);
  });
  expect(api.probeModelProfile).toHaveBeenCalledExactlyOnceWith(
    profileId,
    2,
    expect.any(String),
  );
  expect(hook.result.current.pendingProbe).toBeNull();
  expect(api.createModelProfile).not.toHaveBeenCalled();
});

async function mount() {
  const hook = renderHook(useModelProfiles);
  await act(async () => {});
  return hook;
}
async function assertWritesBlocked(
  hook: ReturnType<
    typeof renderHook<ReturnType<typeof useModelProfiles>, unknown>
  >,
) {
  await act(async () => {
    expect(await hook.result.current.create(input)).toBeUndefined();
    expect(
      await hook.result.current.update(profile.profile_id, {
        expected_revision: 1,
      }),
    ).toBeUndefined();
    expect(
      await hook.result.current.activate(profile.profile_id),
    ).toBeUndefined();
    expect(
      await hook.result.current.remove(profile.profile_id),
    ).toBeUndefined();
    expect(
      await hook.result.current.probeProfile(profile.profile_id),
    ).toBeUndefined();
  });
  expect(api.updateModelProfile).not.toHaveBeenCalled();
  expect(api.activateModelProfile).not.toHaveBeenCalled();
  expect(api.deleteModelProfile).not.toHaveBeenCalled();
  expect(api.probeModelProfile).not.toHaveBeenCalled();
}

it("journals only a generated UUID before POST and clears only after matching creation receipt", async () => {
  const receipt = deferred<ModelCreationOperation>();
  vi.mocked(api.getModelCreationOperation).mockReturnValue(receipt.promise);
  vi.mocked(api.createModelProfile).mockImplementation(async (value) => {
    expect(value).toEqual({
      ...input,
      creation_operation_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
    });
    expect(JSON.parse(window.localStorage.getItem(KEY) ?? "null")).toEqual({
      schemaVersion: 1,
      operationId: value.creation_operation_id,
    });
    return profile;
  });
  const hook = await mount();
  let saved!: Promise<ModelProfile | null | undefined>;
  act(() => {
    saved = hook.result.current.create(input);
  });
  await waitFor(() =>
    expect(api.getModelCreationOperation).toHaveBeenCalledOnce(),
  );
  expect(hook.result.current.profiles).toEqual([]);
  const id = vi.mocked(api.getModelCreationOperation).mock.calls[0][0];
  expect(readPendingModelCreation()).toBe(id);
  expect(window.localStorage.getItem(KEY)).not.toContain(input.api_key);
  await act(async () => {
    receipt.resolve(committed(id));
    expect(await saved).toEqual(profile);
  });
  expect(readPendingModelCreation()).toBeNull();
  expect(hook.result.current.pendingCreation).toBeNull();
});

it("restores lost-response pending on remount and reconciles through GET without another POST", async () => {
  vi.mocked(api.createModelProfile).mockRejectedValue(new Error(input.api_key));
  const first = await mount();
  await act(async () => {
    expect(await first.result.current.create(input)).toBeUndefined();
  });
  const id = readPendingModelCreation();
  expect(id).toBeTruthy();
  expect(first.result.current.error).not.toContain(input.api_key);
  first.unmount();
  vi.mocked(api.getModelProfiles).mockResolvedValue([profile]);
  const next = await mount();
  expect(next.result.current.pendingCreation).toBe(id);
  await assertWritesBlocked(next);
  expect(api.createModelProfile).toHaveBeenCalledOnce();
  expect(api.getModelCreationOperation).not.toHaveBeenCalled();
  await act(async () => {
    expect(await next.result.current.reconcileCreation()).toEqual(profile);
  });
  expect(api.getModelCreationOperation).toHaveBeenCalledExactlyOnceWith(id);
  expect(api.createModelProfile).toHaveBeenCalledOnce();
  expect(next.result.current.pendingCreation).toBeNull();
  expect(readPendingModelCreation()).toBeNull();
});

it("allows correcting a draft after an explicitly rejected validation POST", async () => {
  vi.mocked(api.createModelProfile).mockRejectedValueOnce({
    isAxiosError: true,
    response: { status: 422, data: { detail: "request_validation_failed" } },
  });
  const hook = await mount();
  await act(async () => {
    expect(await hook.result.current.create(input)).toBeNull();
  });
  expect(readPendingModelCreation()).toBeNull();
  expect(hook.result.current.pendingCreation).toBeNull();
  expect(hook.result.current.error).toContain("配置未保存");
  expect(api.getModelCreationOperation).not.toHaveBeenCalled();
  await act(async () => {
    expect(
      await hook.result.current.create({
        ...input,
        base_url: "https://corrected.example/v1",
      }),
    ).toEqual(profile);
  });
  expect(api.createModelProfile).toHaveBeenCalledTimes(2);
});

it.each([400, 409, 503])(
  "keeps unclassified %s failures pending",
  async (status) => {
    vi.mocked(api.createModelProfile).mockRejectedValueOnce({
      isAxiosError: true,
      response: { status, data: { detail: "request_validation_failed" } },
    });
    const hook = await mount();
    await act(async () => {
      expect(await hook.result.current.create(input)).toBeUndefined();
    });
    expect(readPendingModelCreation()).toBeTruthy();
    await assertWritesBlocked(hook);
  },
);

it("does not confuse a receipt lookup 422 with rejection of the POST", async () => {
  vi.mocked(api.getModelCreationOperation).mockRejectedValueOnce({
    isAxiosError: true,
    response: { status: 422, data: { detail: "request_validation_failed" } },
  });
  const hook = await mount();
  await act(async () => {
    expect(await hook.result.current.create(input)).toBeUndefined();
  });
  expect(readPendingModelCreation()).toBeTruthy();
});

it("does not unlock a rejected create if its pending record cannot be cleared", async () => {
  vi.mocked(api.createModelProfile).mockRejectedValueOnce({
    isAxiosError: true,
    response: { status: 422, data: { detail: "request_validation_failed" } },
  });
  const hook = await mount();
  vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
    throw Error("storage failed");
  });
  await act(async () => {
    expect(await hook.result.current.create(input)).toBeUndefined();
  });
  expect(readPendingModelCreation()).toBeTruthy();
  await assertWritesBlocked(hook);
});

it.each(["not_found", "pending"] as const)(
  "keeps %s unresolved and never adopts a same-name different-ID profile",
  async (state) => {
    savePendingModelCreation(ID);
    vi.mocked(api.getModelProfiles).mockResolvedValue([
      { ...profile, profile_id: "same-name-other-id" },
    ]);
    vi.mocked(api.getModelCreationOperation).mockResolvedValue({
      operation_id: ID,
      state,
      profile_id: null,
      created_revision: null,
    });
    const hook = await mount();
    await act(async () => {
      expect(await hook.result.current.reconcileCreation()).toBeUndefined();
    });
    expect(readPendingModelCreation()).toBe(ID);
    await assertWritesBlocked(hook);
    expect(api.createModelProfile).not.toHaveBeenCalled();
  },
);

it("unlocks a missing creation only after explicit fresh reconciliation", async () => {
  savePendingModelCreation(ID);
  vi.mocked(api.getModelCreationOperation).mockResolvedValue({
    operation_id: ID,
    state: "not_found",
    profile_id: null,
    created_revision: null,
  });
  const hook = await mount();

  await act(async () => {
    expect(await hook.result.current.reconcileCreation()).toBeUndefined();
  });
  expect(hook.result.current.creationMissing).toBe(ID);
  expect(hook.result.current.pendingCreation).toBe(ID);
  await assertWritesBlocked(hook);

  await act(async () => {
    expect(await hook.result.current.discardMissingCreation()).toBe(true);
  });
  expect(api.getModelCreationOperation).toHaveBeenCalledTimes(2);
  expect(api.getModelProfiles).toHaveBeenCalledTimes(2);
  expect(readPendingModelCreation()).toBeNull();
  expect(hook.result.current.pendingCreation).toBeNull();
  expect(hook.result.current.creationMissing).toBeNull();
  expect(api.createModelProfile).not.toHaveBeenCalled();
  expect(api.updateModelProfile).not.toHaveBeenCalled();
  expect(api.probeModelProfile).not.toHaveBeenCalled();

  vi.mocked(api.getModelCreationOperation).mockImplementation(async (id) =>
    committed(id),
  );
  await act(async () => {
    expect(await hook.result.current.create(input)).toEqual(profile);
  });
  expect(api.createModelProfile).toHaveBeenCalledOnce();
  expect(window.localStorage.getItem(KEY) ?? "").not.toContain(input.api_key);
  expect(hook.result.current.error ?? "").not.toContain(input.api_key);
});

it("explicitly abandons a pending creation before unlocking a retry", async () => {
  savePendingModelCreation(ID);
  vi.mocked(api.getModelCreationOperation).mockResolvedValue({
    operation_id: ID,
    state: "pending",
    profile_id: OTHER_ID,
    created_revision: 1,
  });
  const hook = await mount();

  await act(async () => {
    expect(await hook.result.current.discardMissingCreation()).toBe(true);
  });

  expect(api.abandonModelCreationOperation).toHaveBeenCalledExactlyOnceWith(ID);
  expect(readPendingModelCreation()).toBeNull();
  expect(hook.result.current.pendingCreation).toBeNull();
  expect(api.createModelProfile).not.toHaveBeenCalled();
});

it("keeps a missing creation locked when a profile appears before discard", async () => {
  savePendingModelCreation(ID);
  vi.mocked(api.getModelCreationOperation).mockResolvedValue({
    operation_id: ID,
    state: "not_found",
    profile_id: null,
    created_revision: null,
  });
  vi.mocked(api.getModelProfiles)
    .mockResolvedValueOnce([])
    .mockResolvedValueOnce([profile]);
  const hook = await mount();
  await act(async () => {
    await hook.result.current.reconcileCreation();
    expect(await hook.result.current.discardMissingCreation()).toBeUndefined();
  });
  expect(hook.result.current.profiles).toEqual([profile]);
  expect(readPendingModelCreation()).toBe(ID);
  expect(hook.result.current.pendingCreation).toBe(ID);
  expect(hook.result.current.error).toContain("已发现模型配置");
  expect(api.createModelProfile).not.toHaveBeenCalled();
});

it("keeps the replacement journal when the local operation changes before discard", async () => {
  savePendingModelCreation(ID);
  vi.mocked(api.getModelCreationOperation).mockResolvedValue({
    operation_id: ID,
    state: "not_found",
    profile_id: null,
    created_revision: null,
  });
  const hook = await mount();
  await act(async () => {
    await hook.result.current.reconcileCreation();
  });
  window.localStorage.removeItem(KEY);
  savePendingModelCreation(OTHER_ID);
  await act(async () => {
    expect(await hook.result.current.discardMissingCreation()).toBeUndefined();
  });
  expect(readPendingModelCreation()).toBe(OTHER_ID);
  expect(hook.result.current.pendingCreation).toBe(ID);
  expect(api.getModelCreationOperation).toHaveBeenCalledOnce();
  expect(api.createModelProfile).not.toHaveBeenCalled();
});

it("keeps a missing creation locked when verified local clearing fails", async () => {
  savePendingModelCreation(ID);
  vi.mocked(api.getModelCreationOperation).mockResolvedValue({
    operation_id: ID,
    state: "not_found",
    profile_id: null,
    created_revision: null,
  });
  const hook = await mount();
  await act(async () => {
    await hook.result.current.reconcileCreation();
  });
  vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
    throw new Error(input.api_key);
  });
  await act(async () => {
    expect(await hook.result.current.discardMissingCreation()).toBeUndefined();
  });
  expect(readPendingModelCreation()).toBe(ID);
  expect(hook.result.current.pendingCreation).toBe(ID);
  expect(hook.result.current.error).not.toContain(input.api_key);
  expect(api.createModelProfile).not.toHaveBeenCalled();
});

it.each([
  [
    "changed identity",
    {
      operation_id: OTHER_ID,
      state: "not_found",
      profile_id: null,
      created_revision: null,
    },
  ],
  [
    "noncanonical absence",
    {
      operation_id: ID,
      state: "not_found",
      profile_id: profile.profile_id,
      created_revision: null,
    },
  ],
] as const)(
  "keeps the journal when the fresh receipt becomes %s",
  async (_label, next) => {
    savePendingModelCreation(ID);
    vi.mocked(api.getModelCreationOperation)
      .mockResolvedValueOnce({
        operation_id: ID,
        state: "not_found",
        profile_id: null,
        created_revision: null,
      })
      .mockResolvedValueOnce(next as ModelCreationOperation);
    const hook = await mount();
    await act(async () => {
      await hook.result.current.reconcileCreation();
      expect(
        await hook.result.current.discardMissingCreation(),
      ).toBeUndefined();
    });
    expect(readPendingModelCreation()).toBe(ID);
    expect(hook.result.current.pendingCreation).toBe(ID);
    expect(api.getModelProfiles).toHaveBeenCalledOnce();
    expect(api.createModelProfile).not.toHaveBeenCalled();
  },
);

it("keeps the journal when the fresh receipt lookup fails", async () => {
  savePendingModelCreation(ID);
  vi.mocked(api.getModelCreationOperation)
    .mockResolvedValueOnce({
      operation_id: ID,
      state: "not_found",
      profile_id: null,
      created_revision: null,
    })
    .mockRejectedValueOnce(new Error(input.api_key));
  const hook = await mount();
  await act(async () => {
    await hook.result.current.reconcileCreation();
    expect(await hook.result.current.discardMissingCreation()).toBeUndefined();
  });
  expect(readPendingModelCreation()).toBe(ID);
  expect(hook.result.current.error).not.toContain(input.api_key);
  expect(api.createModelProfile).not.toHaveBeenCalled();
});

it("rejects a journal replacement that happens during the fresh receipt lookup", async () => {
  savePendingModelCreation(ID);
  const second = deferred<ModelCreationOperation>();
  vi.mocked(api.getModelCreationOperation)
    .mockResolvedValueOnce({
      operation_id: ID,
      state: "not_found",
      profile_id: null,
      created_revision: null,
    })
    .mockReturnValueOnce(second.promise);
  const hook = await mount();
  await act(async () => {
    await hook.result.current.reconcileCreation();
  });
  let discarded!: Promise<boolean | undefined>;
  act(() => {
    discarded = hook.result.current.discardMissingCreation();
  });
  await waitFor(() =>
    expect(api.getModelCreationOperation).toHaveBeenCalledTimes(2),
  );
  window.localStorage.removeItem(KEY);
  savePendingModelCreation(OTHER_ID);
  await act(async () => {
    second.resolve({
      operation_id: ID,
      state: "not_found",
      profile_id: null,
      created_revision: null,
    });
    expect(await discarded).toBeUndefined();
  });
  expect(readPendingModelCreation()).toBe(OTHER_ID);
  expect(api.getModelProfiles).toHaveBeenCalledOnce();
  expect(api.createModelProfile).not.toHaveBeenCalled();
});

it("prevents an older refresh from overwriting discard's profile snapshot", async () => {
  savePendingModelCreation(ID);
  vi.mocked(api.getModelCreationOperation).mockResolvedValue({
    operation_id: ID,
    state: "not_found",
    profile_id: null,
    created_revision: null,
  });
  const hook = await mount();
  await act(async () => {
    await hook.result.current.reconcileCreation();
  });
  const oldRefresh = deferred<ModelProfile[]>();
  vi.mocked(api.getModelProfiles)
    .mockReturnValueOnce(oldRefresh.promise)
    .mockResolvedValueOnce([profile]);
  let refreshing!: Promise<void>;
  act(() => {
    refreshing = hook.result.current.refreshProfiles();
  });
  await waitFor(() => expect(api.getModelProfiles).toHaveBeenCalledTimes(2));
  expect(hook.result.current.refreshing).toBe(true);
  await act(async () => {
    expect(await hook.result.current.discardMissingCreation()).toBeUndefined();
  });
  expect(hook.result.current.profiles).toEqual([profile]);
  await act(async () => {
    oldRefresh.resolve([]);
    await refreshing;
  });
  expect(hook.result.current.profiles).toEqual([profile]);
  expect(hook.result.current.refreshing).toBe(false);
  expect(readPendingModelCreation()).toBe(ID);
});

it("does not substitute a same-name profile for a committed receipt's missing ID", async () => {
  savePendingModelCreation(ID);
  vi.mocked(api.getModelProfiles).mockResolvedValue([
    { ...profile, profile_id: "same-name-other-id" },
  ]);
  const hook = await mount();
  await act(async () => {
    expect(await hook.result.current.reconcileCreation()).toBeUndefined();
  });
  expect(readPendingModelCreation()).toBe(ID);
  expect(api.createModelProfile).not.toHaveBeenCalled();
});

it("returns null for a verified deleted creation without recreating the model", async () => {
  savePendingModelCreation(ID);
  vi.mocked(api.getModelCreationOperation).mockResolvedValue({
    ...committed(),
    state: "deleted",
  });
  const hook = await mount();
  await act(async () => {
    expect(await hook.result.current.reconcileCreation()).toBeNull();
  });
  expect(hook.result.current.pendingCreation).toBeNull();
  expect(readPendingModelCreation()).toBeNull();
  expect(hook.result.current.profiles).toEqual([]);
  expect(api.createModelProfile).not.toHaveBeenCalled();
});

it.each(["operation", "profile", "revision"])(
  "does not clear a create with mismatched %s receipt",
  async (mismatch) => {
    vi.mocked(api.getModelCreationOperation).mockImplementation(async (id) => ({
      ...committed(id),
      ...(mismatch === "operation" ? { operation_id: ID } : {}),
      ...(mismatch === "profile" ? { profile_id: "different-id" } : {}),
      ...(mismatch === "revision" ? { created_revision: null } : {}),
    }));
    const hook = await mount();
    await act(async () => {
      expect(await hook.result.current.create(input)).toBeUndefined();
    });
    expect(readPendingModelCreation()).toBeTruthy();
    expect(hook.result.current.profiles).toEqual([]);
    await assertWritesBlocked(hook);
    expect(api.createModelProfile).toHaveBeenCalledOnce();
  },
);

it("fails closed on a denied storage accessor rather than sending an unjournaled create", async () => {
  vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
    throw new Error(input.api_key);
  });
  const hook = await mount();
  expect(hook.result.current.creationStorageFailed).toBe(true);
  await assertWritesBlocked(hook);
  expect(api.createModelProfile).not.toHaveBeenCalled();
  expect(JSON.stringify(hook.result.current)).not.toContain(input.api_key);
});

it("does not POST when persisting the new operation ID fails", async () => {
  const hook = await mount();
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error(input.api_key);
  });
  await act(async () => {
    expect(await hook.result.current.create(input)).toBeUndefined();
  });
  expect(hook.result.current.creationStorageFailed).toBe(true);
  expect(api.createModelProfile).not.toHaveBeenCalled();
  expect(api.getModelCreationOperation).not.toHaveBeenCalled();
  expect(hook.result.current.error).not.toContain(input.api_key);
});

it.each(["operation", "profiles"])(
  "retains pending when reconciliation %s GET fails",
  async (phase) => {
    savePendingModelCreation(ID);
    const hook = await mount();
    if (phase === "operation")
      vi.mocked(api.getModelCreationOperation).mockRejectedValue(
        new Error(input.api_key),
      );
    else
      vi.mocked(api.getModelProfiles).mockRejectedValue(
        new Error(input.api_key),
      );
    await act(async () => {
      expect(await hook.result.current.reconcileCreation()).toBeUndefined();
    });
    expect(readPendingModelCreation()).toBe(ID);
    expect(hook.result.current.pendingCreation).toBe(ID);
    expect(hook.result.current.error).not.toContain(input.api_key);
    await assertWritesBlocked(hook);
    expect(api.createModelProfile).not.toHaveBeenCalled();
  },
);

it("keeps pending and does not publish a created profile when journal clear fails", async () => {
  const hook = await mount();
  vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
    throw new Error(input.api_key);
  });
  await act(async () => {
    expect(await hook.result.current.create(input)).toBeUndefined();
  });
  expect(readPendingModelCreation()).toBeTruthy();
  expect(hook.result.current.pendingCreation).toBeTruthy();
  expect(hook.result.current.profiles).toEqual([]);
  await assertWritesBlocked(hook);
  expect(api.createModelProfile).toHaveBeenCalledOnce();
});

it.each(["post", "receipt"])(
  "does not clear or commit a late %s response after unmount",
  async (phase) => {
    const post = deferred<ModelProfile>();
    const receipt = deferred<ModelCreationOperation>();
    if (phase === "post")
      vi.mocked(api.createModelProfile).mockReturnValue(post.promise);
    else
      vi.mocked(api.getModelCreationOperation).mockReturnValue(receipt.promise);
    const first = await mount();
    let saved!: Promise<ModelProfile | null | undefined>;
    act(() => {
      saved = first.result.current.create(input);
    });
    await waitFor(() =>
      expect(
        phase === "post"
          ? api.createModelProfile
          : api.getModelCreationOperation,
      ).toHaveBeenCalledOnce(),
    );
    const id = readPendingModelCreation();
    if (!id) throw new Error("Missing persisted intent");
    first.unmount();
    const next = await mount();
    await act(async () => {
      post.resolve(profile);
      receipt.resolve(committed(id));
      expect(await saved).toBeUndefined();
    });
    expect(readPendingModelCreation()).toBe(id);
    expect(next.result.current.pendingCreation).toBe(id);
    expect(next.result.current.profiles).toEqual([]);
    await assertWritesBlocked(next);
    expect(api.createModelProfile).toHaveBeenCalledOnce();
  },
);

it("does not clear a pending creation when reconciliation GET finishes after unmount", async () => {
  savePendingModelCreation(ID);
  const pending = deferred<ModelCreationOperation>();
  vi.mocked(api.getModelCreationOperation).mockReturnValue(pending.promise);
  vi.mocked(api.getModelProfiles).mockResolvedValue([profile]);
  const first = await mount();
  let recovered!: Promise<ModelProfile | null | undefined>;
  act(() => {
    recovered = first.result.current.reconcileCreation();
  });
  await waitFor(() =>
    expect(api.getModelCreationOperation).toHaveBeenCalledOnce(),
  );
  first.unmount();
  await act(async () => {
    pending.resolve(committed());
    expect(await recovered).toBeUndefined();
  });
  expect(readPendingModelCreation()).toBe(ID);
  expect(api.createModelProfile).not.toHaveBeenCalled();
});

it("does not clear a pending creation when reconciliation profile read finishes after unmount", async () => {
  savePendingModelCreation(ID);
  const first = await mount();
  const pending = deferred<ModelProfile[]>();
  vi.mocked(api.getModelProfiles).mockReturnValueOnce(pending.promise);
  let recovered!: Promise<ModelProfile | null | undefined>;
  act(() => {
    recovered = first.result.current.reconcileCreation();
  });
  await waitFor(() => expect(api.getModelProfiles).toHaveBeenCalledTimes(2));
  first.unmount();
  await act(async () => {
    pending.resolve([profile]);
    expect(await recovered).toBeUndefined();
  });
  expect(readPendingModelCreation()).toBe(ID);
  expect(api.createModelProfile).not.toHaveBeenCalled();
});
