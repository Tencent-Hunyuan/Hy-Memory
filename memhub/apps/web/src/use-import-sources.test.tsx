// @vitest-environment jsdom

import { webcrypto } from "node:crypto";
import type {
  ConnectSourcesResponse,
  HealthResponse,
  ImportJob,
  SourceResponse,
} from "@one-memory/contracts";
import {
  act,
  cleanup,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { connectionFingerprint, freezeConnection } from "./connection-intent";
import { useImportSources } from "./use-import-sources";

const connectSources = vi.fn();
const getHealth = vi.fn();
const getSources = vi.fn();
const getImportJobs = vi.fn();
const getImportJob = vi.fn();
const changeImportJob = vi.fn();
const createImportJob = vi.fn();
const detectSource = vi.fn();
const setSourceSync = vi.fn();
const getSourceConnection = vi.fn();

vi.mock("./api", () => ({
  apiErrorCode: (reason: { response?: { data?: { detail?: string } } }) =>
    reason.response?.data?.detail ?? null,
  changeImportJob: (...args: unknown[]) => changeImportJob(...args),
  connectSources: (...args: unknown[]) => connectSources(...args),
  createImportJob: (...args: unknown[]) => createImportJob(...args),
  detectSource: (...args: unknown[]) => detectSource(...args),
  getHealth: (...args: unknown[]) => getHealth(...args),
  getImportJobs: (...args: unknown[]) => getImportJobs(...args),
  getImportJob: (...args: unknown[]) => getImportJob(...args),
  getSources: (...args: unknown[]) => getSources(...args),
  setSourceSync: (...args: unknown[]) => setSourceSync(...args),
  getSourceConnection: (...args: unknown[]) => getSourceConnection(...args),
}));

function Harness() {
  const imports = useImportSources({ automaticSync: false });
  return (
    <>
      <button
        type="button"
        disabled={imports.loading}
        onClick={() =>
          void imports
            .connectSelected(["ok", "failed"], imports.sources)
            .catch(() => {})
        }
      >
        connect
      </button>
      <output aria-label="error">{imports.error ?? ""}</output>
      <output aria-label="accepted">
        {imports.lastConnectResult?.accepted_source_ids.join(",") ?? ""}
      </output>
      <output aria-label="failed">
        {Object.keys(imports.lastConnectResult?.failed_sources ?? {}).join(",")}
      </output>
      <output aria-label="backfill">
        {imports.health?.memory_backfill ?? ""}
      </output>
      <button
        type="button"
        disabled={imports.loading}
        onClick={() => void imports.syncAll()}
      >
        sync
      </button>
    </>
  );
}

function ColdIntentHarness() {
  const imports = useImportSources({ automaticSync: false });
  return (
    <button
      type="button"
      disabled={imports.loading}
      onClick={() =>
        void imports
          .connectForOnboarding(
            ["cursor"],
            [
              {
                source_id: "cursor",
                candidate_root_id: "root:v2:fixture",
                display_name: "Cursor",
                storage_kinds: ["jsonl"],
                available: true,
                authorized: false,
                detected: null,
                root_label: null,
                candidate_path: "~/.cursor",
                candidate_kind: "default",
                imported_messages: 0,
                imported_conversations: 0,
                sync_enabled: false,
                sync_completeness: "additions_only",
                capabilities: {
                  historical_import: "implemented",
                  incremental_import: "implemented",
                  live_sync: "implemented",
                  recall: "implemented",
                  installer: "implemented",
                  verification: "fixture_only",
                },
              },
            ],
          )
          .catch(() => {})
      }
    >
      start cold import
    </button>
  );
}

describe("useImportSources", () => {
  it("recovers an uncertain cold-start write from its durable receipt immediately", async () => {
    connectSources.mockRejectedValueOnce(new Error("response lost"));
    getSourceConnection.mockImplementation(async (requestId: string) => ({
      request_id: requestId,
      accepted_source_ids: ["cursor"],
      failed_sources: {},
      jobs: [],
    }));
    getSources.mockResolvedValue([source("cursor", "root:v2:fixture")]);
    render(<ColdIntentHarness />);
    const button = screen.getByRole("button", { name: "start cold import" });
    await waitFor(() => expect(button).toHaveProperty("disabled", false));
    await userEvent.click(button);
    await waitFor(() => expect(getSourceConnection).toHaveBeenCalledOnce());
    const sent = connectSources.mock.calls[0][2];
    expect(sent.sourceRoots).toEqual({ cursor: "root:v2:fixture" });
    expect(getSourceConnection).toHaveBeenCalledWith(sent.requestId);
    expect(connectSources).toHaveBeenCalledTimes(1);
  });
  it("translates a source error code without exposing a generic HTTP failure", async () => {
    const reason = Object.assign(
      new Error("Request failed with status code 503"),
      {
        response: { data: { detail: "source_unavailable" } },
      },
    );
    connectSources.mockRejectedValueOnce(reason);
    render(<Harness />);
    const button = screen.getByRole("button", { name: "connect" });
    await waitFor(() =>
      expect((button as HTMLButtonElement).disabled).toBe(false),
    );
    await userEvent.click(button);
    expect(screen.getByLabelText("error").textContent).toBe(
      "来源目录暂时不可读取，请核对位置和权限后重试。",
    );
  });
  it("does not enqueue a live sync job for uploaded snapshots", async () => {
    getSources.mockResolvedValue([
      {
        source_id: "chatgpt-export",
        authorized: true,
        sync_completeness: "one_time_snapshot",
      },
      {
        source_id: "cursor",
        authorized: true,
        sync_enabled: true,
        sync_completeness: "additions_only",
      },
    ]);
    const user = userEvent.setup();
    render(<Harness />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "sync" })).toHaveProperty(
        "disabled",
        false,
      ),
    );
    await user.click(screen.getByRole("button", { name: "sync" }));
    expect(createImportJob).toHaveBeenCalledTimes(1);
    // The third argument is the interaction's trace id, which is what makes
    // the kernel legs of the four-line chain joinable.
    expect(createImportJob).toHaveBeenCalledWith(
      "cursor",
      "sync",
      expect.any(String),
    );
  });
  it("does not treat history authorization as continuous-sync permission", async () => {
    getSources.mockResolvedValue([
      {
        source_id: "cursor",
        authorized: true,
        sync_enabled: false,
        sync_completeness: "additions_only",
      },
      {
        source_id: "codex",
        authorized: true,
        sync_completeness: "additions_only",
      },
    ]);
    render(<Harness />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "sync" })).toHaveProperty(
        "disabled",
        false,
      ),
    );
    await userEvent.click(screen.getByRole("button", { name: "sync" }));
    expect(createImportJob).not.toHaveBeenCalled();
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal("crypto", webcrypto);
    window.localStorage.clear();
    getHealth.mockResolvedValue(health());
    getSources.mockResolvedValue([source("ok"), source("failed")]);
    getImportJobs.mockResolvedValue([]);
    connectSources.mockImplementation(async (ids: string[], _, options) =>
      receipt(options?.requestId, ids),
    );
    getSourceConnection.mockRejectedValue(
      apiFailure("source_intent_not_found"),
    );
  });

  it("preserves partial batch success as a result instead of a global failure", async () => {
    const user = userEvent.setup();
    connectSources.mockImplementation(async (_ids, _, options) =>
      receipt(options.requestId, ["ok"], { failed: "grant_not_available" }),
    );

    render(<Harness />);
    const button = screen.getByRole("button", { name: "connect" });
    await waitFor(() =>
      expect((button as HTMLButtonElement).disabled).toBe(false),
    );
    await user.click(button);

    await waitFor(() =>
      expect(screen.getByLabelText("accepted").textContent).toBe("ok"),
    );
    expect(screen.getByLabelText("failed").textContent).toBe("failed");
    expect(screen.getByLabelText("error").textContent).toBe("");
  });

  it("keeps asking while Core is still reading old conversations", async () => {
    // Nothing else here refreshes on its own: with no import running, the
    // screen would keep saying Core is catching up long after it stopped.
    getHealth
      .mockResolvedValueOnce({
        status: "ok",
        service: "one-memory-core",
        version: "test",
        instance_id: "instance",
        memory_backfill: "running",
      })
      .mockResolvedValue({
        status: "ok",
        service: "one-memory-core",
        version: "test",
        instance_id: "instance",
        memory_backfill: "done",
      });

    render(<Harness />);

    await waitFor(() =>
      expect(screen.getByLabelText("backfill").textContent).toBe("running"),
    );
    await waitFor(
      () => expect(screen.getByLabelText("backfill").textContent).toBe("done"),
      { timeout: 6_000 },
    );
    const asked = getHealth.mock.calls.length;

    // And stops once it is done, rather than polling for the session's life.
    await new Promise((resolve) => setTimeout(resolve, 4_000));

    expect(getHealth.mock.calls.length).toBe(asked);
  }, 15_000);

  it("refreshes final source counts after a short import finishes", async () => {
    vi.useFakeTimers();
    getImportJobs
      .mockResolvedValueOnce([
        {
          job_id: "j",
          source_id: "s",
          status: "running",
          created_at: "2026-09-12",
        },
      ])
      .mockResolvedValue([
        {
          job_id: "j",
          source_id: "s",
          status: "completed",
          created_at: "2026-09-12",
        },
      ]);
    render(<Harness />);
    await act(async () => {});
    expect(getSources).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(999);
    });
    expect(getSources).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(getSources).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2199);
    });
    expect(getSources).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(getSources).toHaveBeenCalledTimes(3);
  }, 6000);

  it.each(["connectSelected", "connectForOnboarding"] as const)(
    "%s refuses a missing checked snapshot even when current sources have identities",
    async (method) => {
      const { result } = await controller();
      await act(async () => {
        await expect(result.current[method](["ok"])).rejects.toThrow();
      });
      expect(connectSources).not.toHaveBeenCalled();
    },
  );

  it.each([
    { ids: [], snapshot: [] },
    { ids: ["ok", "ok"], snapshot: [source("ok"), source("ok")] },
    { ids: ["ok", "failed"], snapshot: [source("ok"), source("ok")] },
    { ids: ["ok"], snapshot: [{ ...source("ok"), candidate_root_id: null }] },
  ])(
    "rejects incomplete or ambiguous checked identities: $ids",
    async ({ ids, snapshot }) => {
      const { result } = await controller();
      await act(async () => {
        await expect(
          result.current.connectSelected(ids, snapshot),
        ).rejects.toThrow();
      });
      expect(connectSources).not.toHaveBeenCalled();
    },
  );

  it("copies only checked identities before any asynchronous scope check", async () => {
    const { result } = await controller();
    const pendingHealth = deferred<HealthResponse>();
    getHealth.mockReturnValueOnce(pendingHealth.promise);
    const ids = ["ok"];
    const snapshot = [source("ok"), source("failed")];
    let operation!: Promise<ConnectSourcesResponse>;
    act(() => {
      operation = result.current.connectSelected(ids, snapshot);
    });
    ids.push("failed");
    snapshot[0].candidate_root_id = "root:v2:mutated";
    await act(async () => {
      pendingHealth.resolve(health());
      await operation;
    });
    expect(connectSources).toHaveBeenCalledTimes(1);
    expect(connectSources).toHaveBeenCalledWith(["ok"], true, {
      requestId: expect.any(String),
      sourceRoots: { ok: "root:v2:ok" },
      fullRescan: false,
      onboarding: false,
      enableSync: true,
    });
  });

  it("coalesces synchronous duplicate confirmations until the refresh settles", async () => {
    const { result } = await controller();
    const pending = deferred<ConnectSourcesResponse>();
    connectSources.mockReturnValueOnce(pending.promise);
    let first!: Promise<ConnectSourcesResponse>;
    let second!: Promise<ConnectSourcesResponse>;
    act(() => {
      first = result.current.connectSelected(["ok"], [source("ok")]);
      second = result.current.connectSelected(["ok"], [source("ok")]);
    });
    expect(first).toBe(second);
    await waitFor(() => expect(connectSources).toHaveBeenCalledTimes(1));
    expect(result.current.busyKey).toBe("connect:selected");
    await act(async () => {
      pending.resolve(
        receipt(connectSources.mock.calls[0][2].requestId, ["ok"]),
      );
      await Promise.all([first, second]);
    });
    expect(result.current.busyKey).toBeNull();
  });

  it.each(["instance", "restarted-instance"])(
    "recovers a lost response after remount with Core %s using only receipt reads",
    async (instance) => {
      const first = await controller();
      connectSources.mockRejectedValueOnce(new Error("response lost"));
      await act(async () => {
        await expect(
          first.result.current.connectSelected(["ok"], [source("ok")]),
        ).rejects.toThrow();
      });
      const requestId = connectSources.mock.calls[0][2].requestId;
      const stored = JSON.stringify(window.localStorage);
      expect(stored).toContain(requestId);
      expect(stored).not.toContain("candidate_path");
      expect(stored).not.toContain("authorized");
      expect(stored).not.toContain("~/.fixture");
      first.unmount();
      getSourceConnection.mockClear();
      getHealth.mockResolvedValue(health(instance));
      getSourceConnection.mockResolvedValue(receipt(requestId, ["ok"]));
      const next = await controller();
      expect(getSourceConnection).not.toHaveBeenCalled();
      await act(async () => {
        await expect(
          next.result.current.connectSelected(["ok"], [source("ok")]),
        ).resolves.toMatchObject({
          accepted_source_ids: ["ok"],
          request_id: requestId,
        });
      });
      expect(getSourceConnection).toHaveBeenCalledWith(requestId);
      expect(connectSources).toHaveBeenCalledTimes(1);
    },
  );

  it("replaces a missing receipt when this core never stored it", async () => {
    const first = await controller();
    connectSources.mockRejectedValueOnce(new Error("response lost"));
    await act(async () => {
      await expect(
        first.result.current.connectSelected(["ok"], [source("ok")]),
      ).rejects.toThrow();
    });
    const requestId = connectSources.mock.calls[0][2].requestId;
    first.unmount();
    getHealth.mockResolvedValue(health("other-core"));
    const next = await controller();
    await act(async () => {
      await next.result.current.connectSelected(["ok"], [source("ok")]);
    });
    expect(getSourceConnection).toHaveBeenCalledWith(requestId);
    expect(connectSources).toHaveBeenCalledTimes(2);
    const replacement = connectSources.mock.calls[1][2].requestId;
    expect(replacement).not.toBe(requestId);
    expect(JSON.stringify(window.localStorage)).not.toContain(requestId);
    expect(JSON.stringify(window.localStorage)).toContain(replacement);
  });

  it("replaces a leftover request id when this core has no such receipt", async () => {
    const checked = freezeConnection(["ok"], [source("ok")], true);
    const fingerprint = await connectionFingerprint(checked.identity);
    const stale = "123ec388-5195-407d-898f-0c889981b3f4";
    window.localStorage.setItem(
      "one-memory.source-connection-intents.v1",
      JSON.stringify({
        version: 1,
        intents: [{ fingerprint, requestIds: [stale] }],
      }),
    );
    const first = await controller();
    await act(async () => {
      await first.result.current.connectForOnboarding(["ok"], [source("ok")]);
    });
    expect(connectSources).toHaveBeenCalledTimes(1);
    const freshId = connectSources.mock.calls[0][2].requestId;
    expect(freshId).not.toBe(stale);
    expect(JSON.stringify(window.localStorage)).not.toContain(stale);
    expect(JSON.stringify(window.localStorage)).toContain(freshId);
    first.unmount();
    getHealth.mockResolvedValue(health("other-core"));
    const next = await controller();
    await act(async () => {
      await next.result.current.connectForOnboarding(["ok"], [source("ok")]);
    });
    expect(connectSources).toHaveBeenCalledTimes(2);
    const again = connectSources.mock.calls[1][2].requestId;
    expect(again).not.toBe(freshId);
    expect(JSON.stringify(window.localStorage)).not.toContain(freshId);
    expect(JSON.stringify(window.localStorage)).toContain(again);
  });

  it("reuses an uncertain live request ID only after a not-found receipt in the same Core instance", async () => {
    const { result } = await controller();
    connectSources.mockRejectedValueOnce(new Error("response lost"));
    await act(async () => {
      await expect(
        result.current.connectForOnboarding(["ok"], [source("ok")]),
      ).rejects.toThrow();
      await result.current.connectForOnboarding(["ok"], [source("ok")]);
    });
    expect(connectSources).toHaveBeenCalledTimes(2);
    expect(connectSources.mock.calls[1][2]).toEqual(
      connectSources.mock.calls[0][2],
    );
    expect(connectSources.mock.calls[1][2]).toMatchObject({
      fullRescan: true,
      onboarding: true,
    });
  });

  it("checks the live Core instance again before reposting an uncertain intent", async () => {
    const { result } = await controller();
    connectSources.mockRejectedValueOnce(new Error("response lost"));
    await act(async () => {
      await expect(
        result.current.connectSelected(["ok"], [source("ok")]),
      ).rejects.toThrow();
    });
    getHealth.mockResolvedValue(health("different-instance"));
    await act(async () => {
      await expect(
        result.current.connectSelected(["ok"], [source("ok")]),
      ).rejects.toThrow();
    });
    expect(connectSources).toHaveBeenCalledTimes(1);
  });

  it("replays a partial receipt by default and retries only failed sources explicitly", async () => {
    const saved = new Map<string, ConnectSourcesResponse>();
    connectSources.mockImplementation(async (ids: string[], _, options) => {
      const response =
        ids.length === 2
          ? receipt(options.requestId, ["ok"], { failed: "source_unavailable" })
          : receipt(options.requestId, ids);
      saved.set(options.requestId, response);
      return response;
    });
    getSourceConnection.mockImplementation(async (id: string) => saved.get(id));
    const { result } = await controller();
    const snapshot = [source("ok"), source("failed")];
    await act(async () => {
      await result.current.connectSelected(["ok", "failed"], snapshot);
      await result.current.connectSelected(["failed", "ok"], snapshot);
    });
    expect(connectSources).toHaveBeenCalledTimes(1);
    await act(async () => {
      const retried = await result.current.connectSelected(
        ["ok", "failed"],
        snapshot,
        { retryFailed: true },
      );
      expect(retried.accepted_source_ids).toEqual(["failed", "ok"]);
      expect(retried.failed_sources).toEqual({});
      expect(retried.request_id).toBeNull();
    });
    expect(connectSources).toHaveBeenCalledTimes(2);
    expect(connectSources.mock.calls[1][0]).toEqual(["failed"]);
    expect(connectSources.mock.calls[1][2].sourceRoots).toEqual({
      failed: "root:v2:failed",
    });
    expect(connectSources.mock.calls[1][2].requestId).not.toBe(
      connectSources.mock.calls[0][2].requestId,
    );
    await act(async () => {
      await result.current.connectSelected(["ok", "failed"], snapshot, {
        retryFailed: true,
      });
    });
    expect(connectSources).toHaveBeenCalledTimes(2);
  });

  it("does not replay an old accepted receipt when the user reconnects the same revoked source", async () => {
    let currentSource = { ...source("ok"), grant_revision: 0 };
    const saved = new Map<string, ConnectSourcesResponse>();
    getSources.mockImplementation(async () => [currentSource]);
    getSourceConnection.mockImplementation(async (id: string) => saved.get(id));
    connectSources.mockImplementation(async (ids: string[], _, options) => {
      const response = receipt(options.requestId, ids);
      saved.set(options.requestId, response);
      currentSource = {
        ...currentSource,
        authorized: true,
        grant_revision: currentSource.grant_revision + 1,
      };
      return response;
    });
    const first = await controller();
    await act(async () => {
      await first.result.current.connectSelected(
        ["ok"],
        first.result.current.sources,
      );
    });
    const originalId = connectSources.mock.calls[0][2].requestId;
    currentSource = { ...currentSource, authorized: false };
    await act(async () => {
      await first.result.current.refresh();
    });
    first.unmount();
    const next = await controller();
    await act(async () => {
      await next.result.current.connectSelected(
        ["ok"],
        next.result.current.sources,
      );
    });
    expect(connectSources).toHaveBeenCalledTimes(2);
    expect(connectSources.mock.calls[1][2].requestId).not.toBe(originalId);
    expect(next.result.current.sources[0].authorized).toBe(true);
    expect(next.result.current.sources[0].grant_revision).toBe(2);
    await act(async () => {
      await next.result.current.connectSelected(
        ["ok"],
        next.result.current.sources,
      );
    });
    expect(connectSources).toHaveBeenCalledTimes(2);
  });

  it("recovers a committed regrant after response loss without submitting it twice", async () => {
    let currentSource = { ...source("ok"), grant_revision: 1 };
    const saved = new Map<string, ConnectSourcesResponse>();
    getSources.mockImplementation(async () => [currentSource]);
    getSourceConnection
      .mockRejectedValueOnce(new Error("receipt temporarily offline"))
      .mockImplementation(async (id: string) => saved.get(id));
    connectSources.mockImplementation(async (ids: string[], _, options) => {
      saved.set(options.requestId, receipt(options.requestId, ids));
      currentSource = { ...currentSource, authorized: true, grant_revision: 2 };
      throw new Error("Response lost after regrant commit");
    });
    const first = await controller();
    await act(async () => {
      await expect(
        first.result.current.connectSelected(
          ["ok"],
          first.result.current.sources,
        ),
      ).rejects.toThrow();
    });
    first.unmount();
    const next = await controller();
    await act(async () => {
      const result = await next.result.current.connectSelected(
        ["ok"],
        next.result.current.sources,
      );
      expect(result.accepted_source_ids).toEqual(["ok"]);
    });
    expect(connectSources).toHaveBeenCalledTimes(1);
  });

  it("invalidates a changed root and requires a fresh checked snapshot", async () => {
    const { result } = await controller();
    await act(async () => {
      await result.current.connectSelected(["ok"], [source("ok")]);
    });
    getSources.mockResolvedValue([source("ok", "root:v2:replacement")]);
    await act(async () => {
      await result.current.refresh();
    });
    await act(async () => {
      await expect(
        result.current.connectSelected(["ok"], [source("ok")]),
      ).rejects.toThrow();
    });
    expect(connectSources).toHaveBeenCalledTimes(1);
    await act(async () => {
      await result.current.connectSelected(
        ["ok"],
        [source("ok", "root:v2:replacement")],
      );
    });
    expect(connectSources).toHaveBeenCalledTimes(2);
    expect(connectSources.mock.calls[1][2].requestId).not.toBe(
      connectSources.mock.calls[0][2].requestId,
    );
    expect(connectSources.mock.calls[1][2].sourceRoots).toEqual({
      ok: "root:v2:replacement",
    });
  });

  it("does not let an older refresh overwrite newer grants or jobs", async () => {
    const { result } = await controller();
    const oldSources = deferred<SourceResponse[]>();
    const oldJobs = deferred<ImportJob[]>();
    getSources
      .mockReturnValueOnce(oldSources.promise)
      .mockResolvedValue([
        { ...source("ok"), authorized: true, grant_revision: 2 },
      ]);
    getImportJobs
      .mockReturnValueOnce(oldJobs.promise)
      .mockResolvedValue([job("new", "ok")]);
    let older!: Promise<unknown>;
    act(() => {
      older = result.current.refresh().catch(() => {});
    });
    await act(async () => {
      await result.current.refresh();
    });
    await act(async () => {
      oldSources.resolve([
        { ...source("ok"), authorized: false, grant_revision: 1 },
      ]);
      oldJobs.resolve([job("old", "ok")]);
      await older;
    });
    expect(result.current.sources[0]).toMatchObject({
      authorized: true,
      grant_revision: 2,
    });
    expect(result.current.jobs.map((item) => item.job_id)).toEqual(["new"]);
  });

  it("preserves a committed receipt when its follow-up refresh fails", async () => {
    const { result } = await controller();
    connectSources.mockImplementation(async (_ids, _, options) => ({
      ...receipt(options.requestId, ["ok"]),
      jobs: [job("accepted-job", "ok")],
    }));
    getSources.mockRejectedValue(new Error("refresh offline"));
    await act(async () => {
      await expect(
        result.current.connectSelected(["ok"], [source("ok")]),
      ).resolves.toMatchObject({
        accepted_source_ids: ["ok"],
      });
    });
    expect(result.current.lastConnectResult?.accepted_source_ids).toEqual([
      "ok",
    ]);
    expect(result.current.jobs.map((item) => item.job_id)).toEqual([
      "accepted-job",
    ]);
    expect(result.current.error).toContain("刷新");
  });

  it("does not reject a saved sync decision because refresh failed", async () => {
    const { result } = await controller();
    getSources.mockRejectedValue(new Error("refresh offline"));
    await act(async () => {
      await expect(
        result.current.setSync(source("ok"), true),
      ).resolves.toBeUndefined();
    });
    expect(setSourceSync).toHaveBeenCalledTimes(1);
    expect(result.current.error).toContain("刷新");
  });

  it("refuses a new write when recovery storage cannot retain its request ID", async () => {
    const { result } = await controller();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("storage denied");
    });
    await act(async () => {
      await expect(
        result.current.connectSelected(["ok"], [source("ok")]),
      ).rejects.toThrow();
    });
    expect(connectSources).not.toHaveBeenCalled();
  });

  it("does not accept a mismatched receipt as confirmation or issue another write", async () => {
    const { result } = await controller();
    connectSources.mockRejectedValueOnce(new Error("response lost"));
    await act(async () => {
      await expect(
        result.current.connectSelected(["ok"], [source("ok")]),
      ).rejects.toThrow();
    });
    getSourceConnection.mockResolvedValue(receipt("wrong-id", ["ok"]));
    await act(async () => {
      await expect(
        result.current.connectSelected(["ok"], [source("ok")]),
      ).rejects.toThrow();
    });
    expect(connectSources).toHaveBeenCalledTimes(1);
    expect(result.current.lastConnectResult).toBeNull();
  });

  it("invalidates a replay if the root changes while its receipt is being read", async () => {
    const { result } = await controller();
    await act(async () => {
      await result.current.connectSelected(["ok"], [source("ok")]);
    });
    const requestId = connectSources.mock.calls[0][2].requestId;
    const lookup = deferred<ConnectSourcesResponse>();
    getSourceConnection.mockReturnValueOnce(lookup.promise);
    let replay!: Promise<unknown>;
    act(() => {
      replay = result.current
        .connectSelected(["ok"], [source("ok")])
        .catch((reason) => reason);
    });
    await waitFor(() => expect(getSourceConnection).toHaveBeenCalled());
    getSources.mockResolvedValue([source("ok", "root:v2:replacement")]);
    await act(async () => {
      await result.current.refresh();
    });
    await act(async () => {
      lookup.resolve(receipt(requestId, ["ok"]));
      expect(await replay).toBeInstanceOf(Error);
    });
    expect(connectSources).toHaveBeenCalledTimes(1);
    expect(result.current.lastConnectResult).toBeNull();
  });

  it("keeps a committed job when an older refresh finishes after a newer refresh failure", async () => {
    const { result } = await controller();
    const oldSources = deferred<SourceResponse[]>();
    const oldJobs = deferred<ImportJob[]>();
    getSources
      .mockReturnValueOnce(oldSources.promise)
      .mockRejectedValue(new Error("refresh offline"));
    getImportJobs.mockReturnValueOnce(oldJobs.promise);
    let older!: Promise<unknown>;
    act(() => {
      older = result.current.refresh().catch(() => {});
    });
    connectSources.mockImplementation(async (_ids, _, options) => ({
      ...receipt(options.requestId, ["ok"]),
      jobs: [job("committed", "ok")],
    }));
    await act(async () => {
      await result.current.connectSelected(["ok"], [source("ok")]);
    });
    await act(async () => {
      oldSources.resolve([source("ok")]);
      oldJobs.resolve([]);
      await older;
    });
    expect(result.current.jobs.map((item) => item.job_id)).toEqual([
      "committed",
    ]);
    expect(result.current.error).toContain("刷新");
  });

  it("recovers an uncertain failed-only retry after remount without replaying accepted sources", async () => {
    const saved = new Map<string, ConnectSourcesResponse>();
    let loseRetryReceiptOnce = true;
    connectSources.mockImplementation(async (ids: string[], _, options) => {
      const response =
        ids.length === 2
          ? receipt(options.requestId, ["ok"], { failed: "source_unavailable" })
          : receipt(options.requestId, ids);
      saved.set(options.requestId, response);
      if (ids.length === 1) throw new Error("retry response lost");
      return response;
    });
    getSourceConnection.mockImplementation(async (id: string) => {
      const known = saved.get(id);
      if (
        loseRetryReceiptOnce &&
        known?.accepted_source_ids.includes("failed")
      ) {
        loseRetryReceiptOnce = false;
        throw new Error("retry receipt temporarily offline");
      }
      return known;
    });
    const first = await controller();
    const snapshot = [source("ok"), source("failed")];
    await act(async () => {
      await first.result.current.connectForOnboarding(
        ["ok", "failed"],
        snapshot,
      );
      await expect(
        first.result.current.connectForOnboarding(["ok", "failed"], snapshot, {
          retryFailed: true,
        }),
      ).rejects.toThrow();
    });
    expect(connectSources.mock.calls[1][0]).toEqual(["failed"]);
    const retryId = connectSources.mock.calls[1][2].requestId;
    first.unmount();
    const next = await controller();
    await act(async () => {
      await expect(
        next.result.current.connectForOnboarding(["ok", "failed"], snapshot),
      ).resolves.toMatchObject({
        accepted_source_ids: ["failed", "ok"],
        failed_sources: {},
      });
    });
    expect(getSourceConnection).toHaveBeenCalledWith(retryId);
    expect(connectSources).toHaveBeenCalledTimes(2);
  });

  it("does not clear a new refresh error when a superseded refresh succeeds", async () => {
    const { result } = await controller();
    const pending = deferred<SourceResponse[]>();
    getSources
      .mockReturnValueOnce(pending.promise)
      .mockRejectedValueOnce(new Error("offline"));
    let earlier!: Promise<unknown>;
    act(() => {
      earlier = result.current.refresh().catch(() => {});
    });
    await act(async () => {
      await result.current.refresh().catch(() => {});
    });
    await act(async () => {
      pending.resolve([source("ok")]);
      await earlier;
    });
    expect(result.current.error).toContain("刷新");
  });

  it("coalesces duplicate submissions while the committed request's refresh is pending", async () => {
    const { result } = await controller();
    const pending = deferred<SourceResponse[]>();
    getSources.mockReturnValueOnce(pending.promise);
    let first!: Promise<ConnectSourcesResponse>;
    act(() => {
      first = result.current.connectSelected(["ok"], [source("ok")]);
    });
    await waitFor(() =>
      expect(result.current.lastConnectResult?.accepted_source_ids).toEqual([
        "ok",
      ]),
    );
    expect(result.current.connectSelected(["ok"], [source("ok")])).toBe(first);
    expect(connectSources).toHaveBeenCalledTimes(1);
    expect(result.current.busyKey).toBe("connect:selected");
    await act(async () => {
      pending.resolve([source("ok")]);
      await first;
    });
  });

  it("allows a completed refresh to advance while a later poll is still pending", async () => {
    const { result } = await controller();
    const first = deferred<SourceResponse[]>();
    const later = deferred<SourceResponse[]>();
    getSources
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(later.promise);
    let a!: Promise<unknown>;
    let b!: Promise<unknown>;
    act(() => {
      a = result.current.refresh().catch(() => {});
      b = result.current.refresh().catch(() => {});
    });
    await act(async () => {
      first.resolve([{ ...source("ok"), imported_messages: 5 }]);
      await a;
    });
    expect(result.current.sources[0].imported_messages).toBe(5);
    await act(async () => {
      later.resolve([{ ...source("ok"), imported_messages: 10 }]);
      await b;
    });
    expect(result.current.sources[0].imported_messages).toBe(10);
  });

  it("replays the original completed job after remount even when source counts and grants changed", async () => {
    const first = await controller();
    const completed = job("original-job", "ok");
    connectSources.mockImplementation(async (_ids, _, options) => ({
      ...receipt(options.requestId, ["ok"]),
      jobs: [completed],
    }));
    await act(async () => {
      await first.result.current.connectSelected(["ok"], [source("ok")]);
    });
    const requestId = connectSources.mock.calls[0][2].requestId;
    first.unmount();
    const updated = {
      ...source("ok"),
      authorized: true,
      grant_revision: 1,
      imported_messages: 10,
    };
    getSources.mockResolvedValue([updated]);
    getImportJobs.mockResolvedValue([completed]);
    getSourceConnection.mockResolvedValue({
      ...receipt(requestId, ["ok"]),
      jobs: [completed],
    });
    const next = await controller();
    await act(async () => {
      const replayed = await next.result.current.connectSelected(
        ["ok"],
        [updated],
      );
      expect(replayed.jobs).toEqual([completed]);
      expect(replayed.request_id).toBe(requestId);
    });
    expect(connectSources).toHaveBeenCalledTimes(1);
    expect(next.result.current.sources[0].grant_revision).toBe(1);
  });

  it("keeps a fully rejected intent until an explicit failed-only retry", async () => {
    const { result } = await controller();
    connectSources.mockImplementationOnce(async (_ids, _, options) =>
      receipt(options.requestId, [], { ok: "source_unavailable" }),
    );
    await act(async () => {
      await result.current.connectSelected(["ok"], [source("ok")]);
    });
    const requestId = connectSources.mock.calls[0][2].requestId;
    getSourceConnection.mockResolvedValue(
      receipt(requestId, [], { ok: "source_unavailable" }),
    );
    await act(async () => {
      await result.current.connectSelected(["ok"], [source("ok")]);
    });
    expect(connectSources).toHaveBeenCalledTimes(1);
    await act(async () => {
      await result.current.connectSelected(["ok"], [source("ok")], {
        retryFailed: true,
      });
    });
    expect(connectSources).toHaveBeenCalledTimes(2);
    expect(connectSources.mock.calls[1][2].requestId).not.toBe(requestId);
  });

  it.each(["connectSelected", "connectForOnboarding"] as const)(
    "%s coalesces the same upload, replays it after remount, and starts a new checked upload in the same directory",
    async (method) => {
      const approved = {
        ...source("generic-export"),
        candidate_path: "~/data/import-files/ready/generic-export",
        sync_completeness: "one_time_snapshot" as const,
      };
      getSources.mockResolvedValue([approved]);
      const saved = new Map<string, ConnectSourcesResponse>();
      connectSources.mockImplementation(async (ids: string[], _, options) => {
        const response = {
          ...receipt(options.requestId, ids),
          jobs: [job(`job-${saved.size}`, approved.source_id)],
        };
        saved.set(options.requestId, response);
        return response;
      });
      getSourceConnection.mockImplementation(async (id: string) =>
        saved.get(id),
      );
      const options = {
        importUploadId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      };
      const first = await controller();
      let submitted!: Promise<ConnectSourcesResponse>;
      act(() => {
        submitted = first.result.current[method](
          [approved.source_id],
          [approved],
          options,
        );
        expect(
          first.result.current[method](
            [approved.source_id],
            [approved],
            options,
          ),
        ).toBe(submitted);
      });
      let accepted!: ConnectSourcesResponse;
      await act(async () => {
        accepted = await submitted;
      });
      expect(connectSources).toHaveBeenCalledTimes(1);
      expect(connectSources.mock.calls[0][2]).toEqual({
        importUploadId: options.importUploadId,
        sourceRoots: { "generic-export": approved.candidate_root_id },
        requestId: accepted.request_id,
        fullRescan: method === "connectForOnboarding",
        onboarding: method === "connectForOnboarding",
        enableSync: true,
      });
      first.unmount();
      const next = await controller();
      await act(async () => {
        expect(
          await next.result.current[method](
            [approved.source_id],
            [approved],
            options,
          ),
        ).toEqual(accepted);
      });
      expect(connectSources).toHaveBeenCalledTimes(1);
      expect(getSourceConnection).toHaveBeenCalledWith(accepted.request_id);
      const secondOptions = {
        importUploadId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      };
      await act(async () => {
        const newer = await next.result.current[method](
          [approved.source_id],
          [approved],
          secondOptions,
        );
        expect(newer.request_id).not.toBe(accepted.request_id);
        expect(newer.jobs[0].job_id).not.toBe(accepted.jobs[0].job_id);
        expect(
          await next.result.current[method](
            [approved.source_id],
            [approved],
            secondOptions,
          ),
        ).toEqual(newer);
      });
      expect(connectSources).toHaveBeenCalledTimes(2);
      expect(connectSources.mock.calls[1][2].importUploadId).toBe(
        secondOptions.importUploadId,
      );
      const stored = JSON.stringify(localStorage);
      expect(stored).not.toContain(options.importUploadId);
      expect(stored).not.toContain(secondOptions.importUploadId);
      expect(stored).not.toContain(approved.candidate_path);
      expect(createImportJob).not.toHaveBeenCalled();
      expect(setSourceSync).not.toHaveBeenCalled();
    },
  );

  it("freezes the upload option before asynchronous checks and rejects a different concurrent upload", async () => {
    const { result } = await controller();
    const pending = deferred<HealthResponse>();
    getHealth.mockReturnValueOnce(pending.promise);
    const firstId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const options = { importUploadId: firstId };
    let operation!: Promise<ConnectSourcesResponse>;
    act(() => {
      operation = result.current.connectSelected(
        ["ok"],
        [source("ok")],
        options,
      );
    });
    options.importUploadId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    await act(async () => {
      const refused = expect(
        result.current.connectSelected(["ok"], [source("ok")], options),
      ).rejects.toThrow("另一连接请求");
      pending.resolve(health());
      await Promise.all([operation, refused]);
    });
    expect(connectSources).toHaveBeenCalledTimes(1);
    expect(connectSources.mock.calls[0][2].importUploadId).toBe(firstId);
  });

  it("reposts an uncertain upload with the same request ID and the same frozen upload binding", async () => {
    const { result } = await controller();
    const options = { importUploadId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" };
    connectSources.mockRejectedValueOnce(new Error("response lost"));
    await act(async () => {
      await expect(
        result.current.connectSelected(["ok"], [source("ok")], options),
      ).rejects.toThrow("response lost");
      await result.current.connectSelected(["ok"], [source("ok")], options);
    });
    expect(connectSources).toHaveBeenCalledTimes(2);
    expect(connectSources.mock.calls[1][2]).toEqual(
      connectSources.mock.calls[0][2],
    );
    expect(connectSources.mock.calls[1][2].importUploadId).toBe(
      options.importUploadId,
    );
  });

  it("retains upload identity for an explicit failed-source retry", async () => {
    const { result } = await controller();
    const options = { importUploadId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" };
    connectSources.mockImplementationOnce(async (_ids, _, sent) =>
      receipt(sent.requestId, [], { ok: "source_import_busy" }),
    );
    await act(async () => {
      await result.current.connectSelected(["ok"], [source("ok")], options);
    });
    const original = connectSources.mock.calls[0][2];
    getSourceConnection.mockResolvedValue(
      receipt(original.requestId, [], { ok: "source_import_busy" }),
    );
    await act(async () => {
      await result.current.connectSelected(["ok"], [source("ok")], options);
      expect(connectSources).toHaveBeenCalledTimes(1);
      await result.current.connectSelected(["ok"], [source("ok")], {
        ...options,
        retryFailed: true,
      });
    });
    expect(connectSources).toHaveBeenCalledTimes(2);
    expect(connectSources.mock.calls[1][2]).toEqual({
      ...original,
      requestId: expect.any(String),
      importUploadId: options.importUploadId,
    });
    expect(connectSources.mock.calls[1][2].requestId).not.toBe(
      original.requestId,
    );
  });

  it("does not let a new upload bypass a changed checked root", async () => {
    const { result } = await controller();
    getSources.mockResolvedValue([source("ok", "root:v2:changed")]);
    await act(async () => {
      await result.current.refresh();
    });
    await act(async () => {
      await expect(
        result.current.connectSelected(["ok"], [source("ok")], {
          importUploadId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        }),
      ).rejects.toThrow("身份已变化");
    });
    expect(connectSources).not.toHaveBeenCalled();
    expect(localStorage.length).toBe(0);
  });

  it.each([
    "",
    "not-a-uuid",
    "aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa",
    "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
    " aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa\n",
    null,
    7,
  ])(
    "rejects noncanonical upload identity %j before saving or submitting an intent",
    async (id) => {
      const { result } = await controller();
      await act(async () => {
        await expect(
          result.current.connectSelected(["ok"], [source("ok")], {
            importUploadId: id as unknown as string,
          }),
        ).rejects.toThrow();
      });
      expect(connectSources).not.toHaveBeenCalled();
      expect(getSourceConnection).not.toHaveBeenCalled();
      expect(localStorage.length).toBe(0);
    },
  );

  describe("source snapshot freshness", () => {
    const firstObserved = "2026-09-14T05:20:00.000Z";
    const nextObserved = "2026-09-14T05:21:00.000Z";
    const lastObserved = "2026-09-14T05:22:00.000Z";
    const reads = [
      ["health", getHealth],
      ["sources", getSources],
      ["jobs", getImportJobs],
    ] as const;

    beforeEach(() => {
      vi.useFakeTimers({ toFake: ["Date"] });
      vi.setSystemTime(new Date(firstObserved));
    });

    it("confirms only after all three initial reads with the local completion time", async () => {
      const pendingHealth = deferred<HealthResponse>();
      const pendingSources = deferred<SourceResponse[]>();
      const pendingJobs = deferred<ImportJob[]>();
      getHealth.mockReturnValueOnce(pendingHealth.promise);
      getSources.mockReturnValueOnce(pendingSources.promise);
      getImportJobs.mockReturnValueOnce(pendingJobs.promise);
      const { result } = renderHook(() =>
        useImportSources({ automaticSync: false }),
      );
      expect(result.current.sourceSnapshot).toEqual({
        confirmedAt: null,
        stale: true,
      });
      await act(async () => {
        pendingHealth.resolve(health());
        pendingSources.resolve([{ ...source("ok"), imported_messages: 17 }]);
      });
      expect(result.current.sourceSnapshot).toEqual({
        confirmedAt: null,
        stale: true,
      });
      expect(result.current.sources).toEqual([]);
      const oldServerJob = { ...job("old", "ok"), errors: 2, stored: 17 };
      vi.setSystemTime(new Date(nextObserved));
      await act(async () => {
        pendingJobs.resolve([oldServerJob]);
      });
      expect(result.current.sourceSnapshot).toEqual({
        confirmedAt: nextObserved,
        stale: false,
      });
      expect(result.current.sourceSnapshot?.confirmedAt).not.toBe(
        oldServerJob.updated_at,
      );
      expect(result.current.jobs).toEqual([oldServerJob]);
      expect(result.current.sources[0].imported_messages).toBe(17);
    });

    it.each(reads)(
      "initial %s failure leaves an unconfirmed stale snapshot",
      async (_name, read) => {
        read.mockRejectedValueOnce(new Error("offline"));
        const { result } = await controller();
        expect(result.current.sourceSnapshot).toEqual({
          confirmedAt: null,
          stale: true,
        });
        expect(result.current.health).toBeNull();
        expect(result.current.sources).toEqual([]);
        expect(result.current.jobs).toEqual([]);
        expect(result.current.error).toContain("刷新");
      },
    );

    it.each(reads)(
      "%s refresh failure retains the last confirmed timestamp and data",
      async (_name, read) => {
        getSources.mockResolvedValue([
          { ...source("ok"), imported_messages: 17 },
        ]);
        getImportJobs.mockResolvedValue([
          { ...job("old", "ok"), stored: 17, errors: 2 },
        ]);
        const { result } = await controller();
        const before = result.current;
        expect(before.sourceSnapshot).toEqual({
          confirmedAt: firstObserved,
          stale: false,
        });
        getHealth.mockResolvedValue(health("uncommitted-instance"));
        getSources.mockResolvedValue([
          { ...source("ok"), imported_messages: 29 },
        ]);
        getImportJobs.mockResolvedValue([job("uncommitted", "ok")]);
        read.mockRejectedValueOnce(new Error("timeout"));
        vi.setSystemTime(new Date(nextObserved));
        await act(async () => {
          await expect(result.current.refresh()).rejects.toThrow("timeout");
        });
        expect(result.current.sourceSnapshot).toEqual({
          confirmedAt: firstObserved,
          stale: true,
        });
        expect(result.current.health).toBe(before.health);
        expect(result.current.sources).toBe(before.sources);
        expect(result.current.jobs).toBe(before.jobs);
        expect(result.current.error).toContain("刷新超时");
      },
    );

    it("recovers initial failure only when all recovery reads complete", async () => {
      getSources.mockRejectedValueOnce(new Error("offline"));
      const { result } = await controller();
      const pending = deferred<ImportJob[]>();
      getImportJobs.mockReturnValueOnce(pending.promise);
      let refreshing!: Promise<unknown>;
      vi.setSystemTime(new Date(nextObserved));
      act(() => {
        refreshing = result.current.refresh();
      });
      await act(async () => {});
      expect(result.current.sourceSnapshot).toEqual({
        confirmedAt: null,
        stale: true,
      });
      expect(result.current.error).toContain("刷新");
      vi.setSystemTime(new Date(lastObserved));
      await act(async () => {
        pending.resolve([job("recovered", "ok")]);
        await refreshing;
      });
      expect(result.current.sourceSnapshot).toEqual({
        confirmedAt: lastObserved,
        stale: false,
      });
      expect(result.current.error).toBeNull();
      expect(result.current.jobs[0].job_id).toBe("recovered");
    });

    it("does not let an old successful poll clear a newer failure or advance confirmation", async () => {
      const { result } = await controller();
      const old = deferred<SourceResponse[]>();
      getSources
        .mockReturnValueOnce(old.promise)
        .mockRejectedValueOnce(new Error("timeout"));
      let older!: Promise<unknown>;
      act(() => {
        older = result.current.refresh().catch((error) => error);
      });
      vi.setSystemTime(new Date(nextObserved));
      await act(async () => {
        await result.current.refresh().catch(() => {});
      });
      const failure = result.current.error;
      expect(failure).toContain("刷新超时");
      vi.setSystemTime(new Date(lastObserved));
      await act(async () => {
        old.resolve([{ ...source("old"), imported_messages: 99 }]);
        await older;
      });
      expect(result.current.sourceSnapshot).toEqual({
        confirmedAt: firstObserved,
        stale: true,
      });
      expect(result.current.error).toBe(failure);
      expect(result.current.sources.map((item) => item.source_id)).toEqual([
        "ok",
        "failed",
      ]);
    });

    it("does not let an old failure mark a recovered snapshot stale again", async () => {
      const { result } = await controller();
      const old = deferred<SourceResponse[]>();
      getSources.mockReturnValueOnce(old.promise);
      let older!: Promise<unknown>;
      act(() => {
        older = result.current.refresh().catch((error) => error);
      });
      vi.setSystemTime(new Date(nextObserved));
      await act(async () => {
        await result.current.refresh();
      });
      vi.setSystemTime(new Date(lastObserved));
      await act(async () => {
        old.reject(new Error("late timeout"));
        await older;
      });
      expect(result.current.sourceSnapshot).toEqual({
        confirmedAt: nextObserved,
        stale: false,
      });
      expect(result.current.error).toBeNull();
    });

    it("does not advance confirmation when a superseded successful poll finishes later", async () => {
      const { result } = await controller();
      const old = deferred<SourceResponse[]>();
      getSources.mockReturnValueOnce(old.promise);
      let older!: Promise<unknown>;
      act(() => {
        older = result.current.refresh().catch((error) => error);
      });
      vi.setSystemTime(new Date(nextObserved));
      await act(async () => {
        await result.current.refresh();
      });
      vi.setSystemTime(new Date(lastObserved));
      await act(async () => {
        old.resolve([source("old")]);
        await older;
      });
      expect(result.current.sourceSnapshot).toEqual({
        confirmedAt: nextObserved,
        stale: false,
      });
      expect(result.current.sources.map((item) => item.source_id)).toEqual([
        "ok",
        "failed",
      ]);
    });

    it("a job action emits one trace and forwards its id to the write", async () => {
      const emitted: Array<Record<string, unknown>> = [];
      (
        window as unknown as {
          oneMemory: { logUiTrace: (input: Record<string, unknown>) => void };
        }
      ).oneMemory = { logUiTrace: (input) => emitted.push(input) };
      getImportJobs.mockResolvedValue([actionJob("a", "running")]);
      const { result } = await controller();
      changeImportJob.mockResolvedValue(actionJob("a", "paused", 2));
      await act(async () => {
        await result.current.changeJob("a", "pause");
      });
      // Exactly one action and one result, sharing the id handed to the write.
      const action = emitted.filter((item) => item.phase === "action");
      const outcome = emitted.filter((item) => item.phase === "result");
      expect(action).toHaveLength(1);
      expect(outcome).toHaveLength(1);
      expect(action[0].page).toBe("sources");
      expect(action[0].trace_id).toBe(outcome[0].trace_id);
      expect(outcome[0].status).toBe("succeeded");
      // The kernel legs only exist when the request carries the same id.
      expect(changeImportJob.mock.calls.at(-1)?.[2]).toBe(action[0].trace_id);
      delete (window as unknown as { oneMemory?: unknown }).oneMemory;
    });

    it("can confirm a completed refresh while a later poll remains pending", async () => {
      const { result } = await controller();
      const first = deferred<SourceResponse[]>();
      const later = deferred<SourceResponse[]>();
      getSources
        .mockReturnValueOnce(first.promise)
        .mockReturnValueOnce(later.promise);
      let a!: Promise<unknown>;
      let b!: Promise<unknown>;
      act(() => {
        a = result.current.refresh();
        b = result.current.refresh().catch((error) => error);
      });
      vi.setSystemTime(new Date(nextObserved));
      await act(async () => {
        first.resolve([{ ...source("ok"), imported_messages: 7 }]);
        await a;
      });
      expect(result.current.sourceSnapshot).toEqual({
        confirmedAt: nextObserved,
        stale: false,
      });
      vi.setSystemTime(new Date(lastObserved));
      await act(async () => {
        later.reject(new Error("offline"));
        await b;
      });
      expect(result.current.sourceSnapshot).toEqual({
        confirmedAt: nextObserved,
        stale: true,
      });
      expect(result.current.sources[0].imported_messages).toBe(7);
    });

    it.each([false, true])(
      "a job receipt (reconciled=%s) cannot refresh stale source totals or their time",
      async (reconciled) => {
        getSources.mockResolvedValue([
          { ...source("source-a"), imported_messages: 11 },
        ]);
        getImportJobs.mockResolvedValue([actionJob("a", "running")]);
        const { result } = await controller();
        getSources.mockRejectedValueOnce(new Error("offline"));
        await act(async () => {
          await result.current.refresh().catch(() => {});
        });
        const confirmed = { ...actionJob("a", "paused", 2), stored: 29 };
        if (reconciled) {
          changeImportJob.mockRejectedValueOnce(new Error("response lost"));
          getImportJob.mockResolvedValueOnce(confirmed);
        } else changeImportJob.mockResolvedValueOnce(confirmed);
        const nextSources = deferred<SourceResponse[]>();
        getSources.mockReturnValueOnce(nextSources.promise);
        getImportJobs.mockResolvedValue([confirmed]);
        vi.setSystemTime(new Date(nextObserved));
        await act(async () => {
          await expect(result.current.changeJob("a", "pause")).resolves.toEqual(
            confirmed,
          );
        });
        expect(result.current.jobs).toEqual([confirmed]);
        expect(result.current.sources[0].imported_messages).toBe(11);
        expect(result.current.sourceSnapshot).toEqual({
          confirmedAt: firstObserved,
          stale: true,
        });
        expect(result.current.error).toContain("刷新");
        vi.setSystemTime(new Date(lastObserved));
        await act(async () => {
          nextSources.resolve([
            { ...source("source-a"), imported_messages: 29 },
          ]);
        });
        expect(result.current.sourceSnapshot).toEqual({
          confirmedAt: lastObserved,
          stale: false,
        });
        expect(result.current.sources[0].imported_messages).toBe(29);
      },
    );

    it("a confirmed job supersedes a poll without certifying the whole snapshot", async () => {
      getImportJobs.mockResolvedValue([actionJob("a", "running")]);
      const { result } = await controller();
      const old = deferred<SourceResponse[]>();
      const following = deferred<SourceResponse[]>();
      getSources
        .mockReturnValueOnce(old.promise)
        .mockReturnValueOnce(following.promise);
      let older!: Promise<unknown>;
      act(() => {
        older = result.current.refresh().catch((error) => error);
      });
      const confirmed = actionJob("a", "paused", 2);
      changeImportJob.mockResolvedValueOnce(confirmed);
      vi.setSystemTime(new Date(nextObserved));
      await act(async () => {
        await result.current.changeJob("a", "pause");
      });
      expect(result.current.sourceSnapshot).toEqual({
        confirmedAt: firstObserved,
        stale: false,
      });
      vi.setSystemTime(new Date(lastObserved));
      await act(async () => {
        old.resolve([source("obsolete")]);
        await older;
      });
      expect(result.current.sourceSnapshot).toEqual({
        confirmedAt: firstObserved,
        stale: false,
      });
      expect(result.current.jobs).toEqual([confirmed]);
      await act(async () => {
        following.reject(new Error("offline"));
      });
      expect(result.current.sourceSnapshot).toEqual({
        confirmedAt: firstObserved,
        stale: true,
      });
    });

    it.each(["connect", "sync"])(
      "%s success followed by refresh failure does not certify a full snapshot",
      async (operation) => {
        const { result } = await controller();
        getSources.mockRejectedValue(new Error("offline"));
        vi.setSystemTime(new Date(nextObserved));
        await act(async () => {
          if (operation === "connect")
            await result.current.connectSelected(["ok"], [source("ok")]);
          else if (operation === "sync")
            await result.current.setSync(source("ok"), true);
        });
        expect(result.current.sourceSnapshot).toEqual({
          confirmedAt: firstObserved,
          stale: true,
        });
        expect(result.current.error).toContain("刷新");
      },
    );

    it("refresh and recovery issue only full-snapshot reads, with no implicit writes", async () => {
      const { result } = await controller();
      getHealth.mockRejectedValueOnce(new Error("offline"));
      await act(async () => {
        await result.current.refresh().catch(() => {});
      });
      vi.setSystemTime(new Date(nextObserved));
      await act(async () => {
        await result.current.refresh();
      });
      expect(result.current.sourceSnapshot).toEqual({
        confirmedAt: nextObserved,
        stale: false,
      });
      for (const read of [getHealth, getSources, getImportJobs])
        expect(read).toHaveBeenCalledTimes(3);
      for (const notCalled of [
        getImportJob,
        getSourceConnection,
        changeImportJob,
        createImportJob,
        connectSources,
        setSourceSync,
        detectSource,
      ]) {
        expect(notCalled).not.toHaveBeenCalled();
      }
    });

    it("does not change confirmation after a refresh settles on an unmounted controller", async () => {
      const hook = await controller();
      const pending = deferred<SourceResponse[]>();
      getSources.mockReturnValueOnce(pending.promise);
      let refreshing!: Promise<unknown>;
      act(() => {
        refreshing = hook.result.current.refresh().catch((error) => error);
      });
      const snapshot = hook.result.current.sourceSnapshot;
      hook.unmount();
      vi.setSystemTime(new Date(nextObserved));
      pending.resolve([source("late")]);
      expect(await refreshing).toBeInstanceOf(Error);
      expect(hook.result.current.sourceSnapshot).toBe(snapshot);
    });
  });

  describe("job actions", () => {
    it.each([{ operation: "sync" as const }])(
      "invalidates an in-flight job when current context changes: %j",
      async (change) => {
        const original = {
          ...actionJob("a", "running"),
          operation: "history" as const,
        };
        getImportJobs.mockResolvedValue([original]);
        const { result } = await controller();
        const pending = deferred<ImportJob>();
        changeImportJob.mockReturnValue(pending.promise);
        let operation!: Promise<unknown>;
        act(() => {
          operation = result.current
            .changeJob("a", "pause")
            .catch((error) => error);
        });
        await waitFor(() => expect(changeImportJob).toHaveBeenCalled());
        const replacement = {
          ...original,
          ...change,
          updated_at: "2026-09-13T00:03:00Z",
        };
        getImportJobs.mockResolvedValue([replacement]);
        await act(async () => {
          await result.current.refresh();
        });
        await act(async () => {
          pending.resolve({
            ...original,
            status: "paused",
            updated_at: "2026-09-13T00:02:00Z",
          });
          expect(await operation).toBeInstanceOf(Error);
        });
        expect(result.current.jobs).toEqual([replacement]);
        expect(getImportJob).not.toHaveBeenCalled();
      },
    );

    it.each([{ operation: "sync" }])(
      "drops receipt protection when a refresh proves new context: %j",
      async (change) => {
        const original = {
          ...actionJob("a", "running"),
          operation: "history",
        };
        getImportJobs.mockResolvedValue([original]);
        const { result } = await controller();
        changeImportJob.mockResolvedValue({
          ...original,
          status: "paused",
          updated_at: "2026-09-13T00:03:00Z",
        });
        await act(async () => {
          await result.current.changeJob("a", "pause");
        });
        const replacement = { ...original, ...change };
        getImportJobs.mockResolvedValue([replacement]);
        await act(async () => {
          await result.current.refresh();
        });
        expect(result.current.jobs).toEqual([replacement]);
      },
    );

    it("clears old receipt protection after a refresh confirms a different runtime", async () => {
      const original = actionJob("a", "running");
      getImportJobs.mockResolvedValue([original]);
      const { result } = await controller();
      changeImportJob.mockResolvedValue(actionJob("a", "completed", 3));
      await act(async () => {
        await result.current.changeJob("a", "pause");
      });
      getHealth.mockResolvedValue(health("different-instance"));
      await act(async () => {
        await result.current.refresh();
      });
      expect(result.current.jobs).toEqual([original]);
      getImportJobs.mockResolvedValue([]);
      await act(async () => {
        await result.current.refresh();
      });
      expect(result.current.jobs).toEqual([]);
    });

    it.each([{ operation: "sync" }])(
      "rejects POST/GET receipts whose context differs: %j",
      async (change) => {
        const original = {
          ...actionJob("a", "running"),
          operation: "history",
        };
        getImportJobs.mockResolvedValue([original]);
        const { result } = await controller();
        const response = {
          ...original,
          ...change,
          status: "paused",
          updated_at: "2026-09-13T00:02:00Z",
        };
        changeImportJob.mockResolvedValue(response);
        getImportJob.mockResolvedValue(response);
        await act(async () => {
          await expect(result.current.changeJob("a", "pause")).rejects.toThrow(
            "待核对",
          );
        });
        expect(result.current.jobs).toEqual([original]);
      },
    );

    it("orders sub-millisecond server updates without freezing a resumed job", async () => {
      const before = {
        ...actionJob("a", "running"),
        updated_at: "2026-09-13T00:01:00.000001Z",
      };
      getImportJobs.mockResolvedValue([before]);
      const { result } = await controller();
      const paused = {
        ...before,
        status: "paused" as const,
        updated_at: "2026-09-13T00:01:00.000002Z",
      };
      changeImportJob.mockResolvedValue(paused);
      await act(async () => {
        await result.current.changeJob("a", "pause");
      });
      const resumed = { ...before, updated_at: "2026-09-13T00:01:00.000003Z" };
      getImportJobs.mockResolvedValue([resumed]);
      await act(async () => {
        await result.current.refresh();
      });
      expect(result.current.jobs).toEqual([resumed]);
    });

    it("does not overwrite a newer polled state with a delayed older POST receipt", async () => {
      const before = {
        ...actionJob("a", "running"),
        updated_at: "2026-09-13T00:01:00.000001Z",
      };
      getImportJobs.mockResolvedValue([before]);
      const { result } = await controller();
      const pending = deferred<ImportJob>();
      changeImportJob.mockReturnValue(pending.promise);
      let action!: Promise<unknown>;
      act(() => {
        action = result.current.changeJob("a", "pause");
      });
      const polled = { ...before, updated_at: "2026-09-13T00:01:00.000003Z" };
      getImportJobs.mockResolvedValue([polled]);
      await act(async () => {
        await result.current.refresh();
      });
      await act(async () => {
        pending.resolve({
          ...before,
          status: "paused",
          updated_at: "2026-09-13T00:01:00.000002Z",
        });
        await expect(action).resolves.toEqual(polled);
      });
      expect(result.current.jobs).toEqual([polled]);
    });

    it("keeps per-job uncertainty through unrelated successful refresh and opposite action", async () => {
      getImportJobs.mockResolvedValue([actionJob("a", "paused")]);
      const { result } = await controller();
      changeImportJob.mockRejectedValue(new Error("response lost"));
      getImportJob.mockResolvedValue(actionJob("a", "running", 2));
      await act(async () => {
        await expect(result.current.changeJob("a", "resume")).rejects.toThrow(
          "待核对",
        );
        await result.current.refresh();
        await expect(result.current.changeJob("a", "cancel")).rejects.toThrow(
          "待核对",
        );
      });
      expect(changeImportJob).toHaveBeenCalledTimes(1);
      expect(result.current.jobActions?.get("a")).toMatchObject({
        action: "resume",
        pending: false,
        uncertain: true,
      });
    });

    it.each([408, 500, 502, 504])(
      "reconciles ambiguous HTTP %s through GET without resending",
      async (status) => {
        getImportJobs.mockResolvedValue([actionJob("a", "running")]);
        const { result } = await controller();
        changeImportJob.mockRejectedValue(
          jobFailure(status, "transport_error"),
        );
        getImportJob.mockResolvedValue(actionJob("a", "cancelled", 2));
        await act(async () => {
          await expect(
            result.current.changeJob("a", "cancel"),
          ).resolves.toMatchObject({ status: "cancelled" });
        });
        expect(getImportJob).toHaveBeenCalledExactlyOnceWith("a");
        expect(changeImportJob).toHaveBeenCalledTimes(1);
      },
    );

    it("does not merge a late command into a different Core instance", async () => {
      const initial = actionJob("a", "running");
      getImportJobs.mockResolvedValue([initial]);
      const { result } = await controller();
      const pending = deferred<ImportJob>();
      changeImportJob.mockReturnValue(pending.promise);
      let operation!: Promise<unknown>;
      act(() => {
        operation = result.current
          .changeJob("a", "pause")
          .catch((error) => error);
      });
      await waitFor(() => expect(changeImportJob).toHaveBeenCalled());
      getHealth.mockResolvedValue(health("different-core"));
      await act(async () => {
        await result.current.refresh();
      });
      await act(async () => {
        pending.resolve(actionJob("a", "paused", 2));
        expect(await operation).toBeInstanceOf(Error);
      });
      expect(result.current.jobs).toEqual([initial]);
      expect(getImportJob).not.toHaveBeenCalled();
    });

    it("does not make a recovery GET or another POST after controller unmount", async () => {
      getImportJobs.mockResolvedValue([actionJob("a", "running")]);
      const hook = await controller();
      const pending = deferred<ImportJob>();
      changeImportJob.mockReturnValue(pending.promise);
      let operation!: Promise<unknown>;
      act(() => {
        operation = hook.result.current
          .changeJob("a", "pause")
          .catch((error) => error);
      });
      await waitFor(() => expect(changeImportJob).toHaveBeenCalled());
      hook.unmount();
      pending.reject(new Error("response lost"));
      expect(await operation).toBeInstanceOf(Error);
      expect(getImportJob).not.toHaveBeenCalled();
      expect(changeImportJob).toHaveBeenCalledTimes(1);
    });

    it("returns the acknowledged job before a follow-up refresh finishes", async () => {
      const running = actionJob("a", "running");
      getImportJobs.mockResolvedValue([running]);
      const { result } = await controller();
      const refresh = deferred<SourceResponse[]>();
      getSources.mockReturnValueOnce(refresh.promise);
      const paused = actionJob("a", "paused", 2);
      changeImportJob.mockResolvedValue(paused);
      let answer: unknown;
      let operation!: Promise<unknown>;
      act(() => {
        operation = result.current.changeJob("a", "pause");
        void operation.then((value) => {
          answer = value;
        });
      });
      await act(async () => {});
      expect(answer).toEqual(paused);
      expect(result.current.jobs).toEqual([paused]);
      await act(async () => {
        refresh.resolve([source("ok")]);
        await operation;
      });
      expect(result.current.jobs).toEqual([paused]);
    });

    it("coalesces the same job action and rejects a conflicting in-flight action", async () => {
      getImportJobs.mockResolvedValue([actionJob("a", "running")]);
      const { result } = await controller();
      const pending = deferred<ImportJob>();
      changeImportJob.mockReturnValue(pending.promise);
      let first!: Promise<unknown>;
      let duplicate!: Promise<unknown>;
      let conflict!: Promise<unknown>;
      act(() => {
        first = result.current.changeJob("a", "pause");
        duplicate = result.current.changeJob("a", "pause");
        conflict = result.current.changeJob("a", "cancel").catch((e) => e);
      });
      expect(first).toBe(duplicate);
      expect(await conflict).toBeInstanceOf(Error);
      expect(changeImportJob).toHaveBeenCalledTimes(1);
      expect(result.current.jobActions?.get("a")).toMatchObject({
        action: "pause",
        pending: true,
      });
      await act(async () => {
        pending.resolve(actionJob("a", "paused", 2));
        await first;
      });
      expect(result.current.jobActions?.get("a")?.pending).toBe(false);
    });

    it("keeps different jobs' busy states and errors independent", async () => {
      getImportJobs.mockResolvedValue([
        actionJob("a", "running"),
        actionJob("b", "running"),
      ]);
      const { result } = await controller();
      const a = deferred<ImportJob>();
      const b = deferred<ImportJob>();
      changeImportJob.mockImplementation((id: string) =>
        id === "a" ? a.promise : b.promise,
      );
      let first!: Promise<unknown>;
      let second!: Promise<unknown>;
      act(() => {
        first = result.current.changeJob("a", "pause");
        second = result.current.changeJob("b", "cancel").catch((e) => e);
      });
      await act(async () => {
        b.reject(jobFailure(409, "source_permission_changed"));
        expect(await second).toBeInstanceOf(Error);
      });
      expect(result.current.jobActions?.get("a")?.pending).toBe(true);
      expect(result.current.jobActions?.get("b")).toMatchObject({
        pending: false,
        uncertain: false,
        error: expect.stringContaining("权限"),
      });
      expect(result.current.error).toBeNull();
      await act(async () => {
        a.resolve(actionJob("a", "paused", 2));
        await first;
      });
      expect(result.current.jobActions?.get("b")?.error).toContain("权限");
      expect(
        result.current.jobs.find((item) => item.job_id === "a")?.status,
      ).toBe("paused");
      expect(
        result.current.jobs.find((item) => item.job_id === "b")?.status,
      ).toBe("running");
    });

    it("does not clear another source operation's busy key", async () => {
      getImportJobs.mockResolvedValue([actionJob("a", "running")]);
      const { result } = await controller();
      const sync = deferred<void>();
      setSourceSync.mockReturnValue(sync.promise);
      let setting!: Promise<void>;
      act(() => {
        setting = result.current.setSync(source("ok"), true);
      });
      expect(result.current.busyKey).toBe("sync-permission:ok");
      changeImportJob.mockResolvedValue(actionJob("a", "paused", 2));
      await act(async () => {
        await result.current.changeJob("a", "pause");
      });
      expect(result.current.busyKey).toBe("sync-permission:ok");
      await act(async () => {
        sync.resolve();
        await setting;
      });
    });

    it("keeps acknowledged job data when refresh fails and an old poll arrives", async () => {
      const running = actionJob("a", "running");
      getImportJobs.mockResolvedValue([running]);
      const { result } = await controller();
      const oldSources = deferred<SourceResponse[]>();
      const oldJobs = deferred<ImportJob[]>();
      getSources
        .mockReturnValueOnce(oldSources.promise)
        .mockRejectedValue(new Error("offline"));
      getImportJobs.mockReturnValueOnce(oldJobs.promise);
      let poll!: Promise<unknown>;
      act(() => {
        poll = result.current.refresh().catch((e) => e);
      });
      const completed = actionJob("a", "completed", 2);
      changeImportJob.mockResolvedValue(completed);
      await act(async () => {
        await expect(result.current.changeJob("a", "cancel")).resolves.toEqual(
          completed,
        );
      });
      expect(result.current.jobs).toEqual([completed]);
      await act(async () => {
        oldSources.resolve([source("ok")]);
        oldJobs.resolve([running]);
        await poll;
      });
      expect(result.current.jobs).toEqual([completed]);
      expect(result.current.error).toContain("最后已确认");
      expect(result.current.error).not.toMatch(
        /后台.*(?:继续|不会中断)|正在自动重试/,
      );
    });

    it("preserves a receipt over stale post-action snapshots but accepts a newer job state", async () => {
      getImportJobs.mockResolvedValue([actionJob("a", "running")]);
      const { result } = await controller();
      const paused = actionJob("a", "paused", 2);
      changeImportJob.mockResolvedValue(paused);
      await act(async () => {
        await result.current.changeJob("a", "pause");
      });
      await act(async () => {
        await result.current.refresh();
      });
      expect(result.current.jobs).toEqual([paused]);
      const resumed = actionJob("a", "running", 3);
      getImportJobs.mockResolvedValue([resumed]);
      await act(async () => {
        await result.current.refresh();
      });
      expect(result.current.jobs).toEqual([resumed]);
    });

    it.each(["pause", "resume", "cancel"] as const)(
      "%s returns the real completed race outcome without changing identity",
      async (action) => {
        getImportJobs.mockResolvedValue([
          actionJob("a", action === "resume" ? "paused" : "running"),
        ]);
        const { result } = await controller();
        const completed = actionJob("a", "completed", 2);
        changeImportJob.mockResolvedValue(completed);
        await act(async () => {
          await expect(result.current.changeJob("a", action)).resolves.toEqual(
            completed,
          );
        });
        expect(result.current.jobs).toEqual([completed]);
        expect(createImportJob).not.toHaveBeenCalled();
      },
    );

    it.each([
      ["pause", "paused"],
      ["pause", "completed"],
      ["cancel", "cancelled"],
      ["cancel", "completed"],
      ["resume", "completed"],
    ] as const)(
      "reconciles a lost %s response to observed %s without another POST",
      async (action, status) => {
        getImportJobs.mockResolvedValue([
          actionJob("a", action === "resume" ? "paused" : "running"),
        ]);
        const { result } = await controller();
        changeImportJob.mockRejectedValue(new Error("response lost"));
        const observed = actionJob("a", status, 2);
        getImportJob.mockResolvedValue(observed);
        await act(async () => {
          await expect(result.current.changeJob("a", action)).resolves.toEqual(
            observed,
          );
        });
        expect(getImportJob).toHaveBeenCalledExactlyOnceWith("a");
        expect(changeImportJob).toHaveBeenCalledTimes(1);
        expect(result.current.jobs).toEqual([observed]);
      },
    );

    it.each(["pending", "running", "failed", "paused"] as const)(
      "does not infer resume succeeded from GET %s and retries only the read",
      async (status) => {
        getImportJobs.mockResolvedValue([actionJob("a", "paused")]);
        const { result } = await controller();
        changeImportJob.mockRejectedValue(new Error("response lost"));
        getImportJob.mockResolvedValue(actionJob("a", status, 2));
        await act(async () => {
          await expect(result.current.changeJob("a", "resume")).rejects.toThrow(
            "待核对",
          );
        });
        expect(result.current.jobActions?.get("a")).toMatchObject({
          pending: false,
          uncertain: true,
        });
        await act(async () => {
          await expect(result.current.changeJob("a", "resume")).rejects.toThrow(
            "待核对",
          );
        });
        expect(changeImportJob).toHaveBeenCalledTimes(1);
        expect(getImportJob).toHaveBeenCalledTimes(2);
        const done = actionJob("a", "completed", 3);
        getImportJob.mockResolvedValue(done);
        await act(async () => {
          await expect(
            result.current.changeJob("a", "resume"),
          ).resolves.toEqual(done);
        });
        expect(changeImportJob).toHaveBeenCalledTimes(1);
      },
    );

    it("keeps an unknown result recoverable when both POST and GET fail", async () => {
      const running = actionJob("a", "running");
      getImportJobs.mockResolvedValue([running]);
      const { result } = await controller();
      changeImportJob.mockRejectedValue(new Error("timeout"));
      getImportJob.mockRejectedValue(new Error("offline"));
      await act(async () => {
        await expect(result.current.changeJob("a", "pause")).rejects.toThrow(
          "待核对",
        );
      });
      expect(result.current.jobs).toEqual([running]);
      getImportJob.mockResolvedValue(actionJob("a", "paused", 2));
      await act(async () => {
        await expect(
          result.current.changeJob("a", "pause"),
        ).resolves.toMatchObject({ status: "paused" });
      });
      expect(changeImportJob).toHaveBeenCalledTimes(1);
      expect(result.current.jobActions?.get("a")?.uncertain).toBe(false);
    });

    it("propagates definite rejection without turning it into an uncertain success", async () => {
      getImportJobs.mockResolvedValue([actionJob("a", "paused")]);
      const { result } = await controller();
      changeImportJob.mockRejectedValue(
        jobFailure(409, "source_permission_changed"),
      );
      await act(async () => {
        await expect(result.current.changeJob("a", "resume")).rejects.toThrow(
          "权限",
        );
      });
      expect(getImportJob).not.toHaveBeenCalled();
      expect(result.current.jobActions?.get("a")?.uncertain).toBe(false);
      expect(result.current.jobs[0].status).toBe("paused");
    });

    it.each([
      { job_id: "foreign" },
      { user_id: "other" },
      { source_id: "other" },
      { created_at: "2026-09-14T00:00:00Z" },
    ])(
      "rejects changed job identity %j from both POST and GET",
      async (identity) => {
        const running = actionJob("a", "running");
        getImportJobs.mockResolvedValue([running]);
        const { result } = await controller();
        const invalid = { ...actionJob("a", "paused", 2), ...identity };
        changeImportJob.mockResolvedValue(invalid);
        getImportJob.mockResolvedValue(invalid);
        await act(async () => {
          await expect(
            result.current.changeJob("a", "pause"),
          ).rejects.toThrow();
        });
        expect(result.current.jobs).toEqual([running]);
      },
    );

    it.each(["completed", "cancelled"] as const)(
      "never POSTs resume for known %s",
      async (status) => {
        getImportJobs.mockResolvedValue([actionJob("a", status)]);
        const { result } = await controller();
        await act(async () => {
          await expect(
            result.current.changeJob("a", "resume"),
          ).rejects.toThrow();
        });
        expect(changeImportJob).not.toHaveBeenCalled();
        expect(createImportJob).not.toHaveBeenCalled();
      },
    );

    it("does not retarget an old operation to a newer same-source job", async () => {
      const old = actionJob("a", "running");
      getImportJobs.mockResolvedValue([old]);
      const { result } = await controller();
      const pending = deferred<ImportJob>();
      changeImportJob.mockReturnValue(pending.promise);
      let operation!: Promise<unknown>;
      act(() => {
        operation = result.current.changeJob("a", "pause");
      });
      const next = {
        ...actionJob("b", "running", 3),
        source_id: old.source_id,
        created_at: "2026-09-13T01:00:00Z",
      };
      getImportJobs.mockResolvedValue([old, next]);
      await act(async () => {
        await result.current.refresh();
      });
      await act(async () => {
        pending.resolve(actionJob("a", "completed", 2));
        await operation;
      });
      expect(changeImportJob).toHaveBeenCalledExactlyOnceWith(
        "a",
        "pause",
        expect.any(String),
      );
      expect(result.current.latestJobBySource.get(old.source_id)).toEqual(next);
      expect(
        result.current.jobs.find((item) => item.job_id === "a")?.status,
      ).toBe("completed");
    });

    it("refuses an unknown job rather than operating by source or starting a new job", async () => {
      const { result } = await controller();
      await act(async () => {
        await expect(
          result.current.changeJob("missing", "pause"),
        ).rejects.toThrow();
      });
      expect(changeImportJob).not.toHaveBeenCalled();
      expect(createImportJob).not.toHaveBeenCalled();
    });

    it.each(["offline", "timeout"])(
      "describes %s refresh as last-known state, not continued background work",
      async (message) => {
        const { result } = await controller();
        getSources.mockRejectedValue(new Error(message));
        await act(async () => {
          await result.current.refresh().catch(() => {});
        });
        expect(result.current.error).toContain("最后已确认");
        expect(result.current.error).not.toMatch(
          /仍在后台继续|后台任务不会中断|正在自动重试/,
        );
      },
    );
  });
});

function health(instance = "instance"): HealthResponse {
  return {
    status: "ok",
    service: "one-memory-core",
    version: "test",
    instance_id: instance,
  };
}

function source(id: string, root = `root:v2:${id}`): SourceResponse {
  return {
    source_id: id,
    display_name: id,
    storage_kinds: ["jsonl"],
    available: true,
    authorized: false,
    detected: null,
    root_label: null,
    candidate_path: id === "cursor" ? "~/.cursor" : `~/.fixture/${id}`,
    candidate_kind: "default",
    candidate_root_id: root,
    imported_messages: 0,
    imported_conversations: 0,
    sync_enabled: false,
    sync_completeness: "additions_only",
    capabilities: {
      historical_import: "implemented",
      incremental_import: "implemented",
      live_sync: "implemented",
      recall: "implemented",
      installer: "implemented",
      verification: "fixture_only",
    },
  };
}

function receipt(
  id: string,
  accepted: string[],
  failed: Record<string, string> = {},
): ConnectSourcesResponse {
  return {
    request_id: id,
    accepted_source_ids: accepted,
    failed_sources: failed,
    jobs: [],
  };
}

function job(id: string, sourceId: string): ImportJob {
  return {
    job_id: id,
    user_id: "local",
    source_id: sourceId,
    status: "completed",
    phase: "done",
    checkpoint: null,
    discovered: 1,
    discovered_targets: 1,
    emitted: 1,
    stored: 1,
    revised: 0,
    merged: 0,
    skipped: 0,
    errors: 0,
    created_at: "2026-09-13T00:00:00Z",
    updated_at: "2026-09-13T00:01:00Z",
  };
}

function apiFailure(code: string) {
  return Object.assign(new Error(code), {
    response: { data: { detail: code } },
  });
}

function actionJob(
  id: string,
  status: ImportJob["status"],
  minute = 1,
): ImportJob {
  return {
    ...job(id, `source-${id}`),
    status,
    phase: status === "completed" ? "done" : "read",
    checkpoint: "byte:150",
    updated_at: `2026-09-13T00:0${minute}:00Z`,
  };
}

function jobFailure(status: number, code: string) {
  return Object.assign(new Error(code), {
    response: { status, data: { detail: code } },
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

async function controller() {
  const hook = renderHook(() => useImportSources({ automaticSync: false }));
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  return hook;
}
