import type {
  EmbeddingSwitchCurrent,
  EmbeddingSwitchJob,
  EmbeddingSwitchPlan,
} from "@one-memory/contracts";
import {
  createContext,
  createElement,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  cancelEmbeddingSwitch,
  embeddingSwitchRefusal,
  getCurrentEmbeddingSwitch,
  planEmbeddingSwitch,
  startEmbeddingSwitch,
} from "./api";
import { subscribePoll } from "./poll-scheduler";

export type EmbeddingSwitchBusy = "plan" | "start" | "cancel" | null;

interface SequencedCurrent {
  sequence: number;
  current: EmbeddingSwitchCurrent;
}

let currentReadSequence = 0;

function unsupportedRoute(reason: unknown): boolean {
  if (!reason || typeof reason !== "object" || !("response" in reason))
    return false;
  const response = (reason as { response?: unknown }).response;
  if (!response || typeof response !== "object") return false;
  const candidate = response as {
    status?: unknown;
    data?: { detail?: unknown };
  };
  return (
    candidate.status === 404 &&
    (candidate.data?.detail === undefined ||
      candidate.data.detail === "Not Found")
  );
}

async function readCurrentSequenced(): Promise<SequencedCurrent> {
  const sequence = ++currentReadSequence;
  return { sequence, current: await getCurrentEmbeddingSwitch() };
}

/**
 * Reusable switch controller. It owns only transport-backed switch state;
 * profile activation and product UI remain outside this module.
 */
export function useEmbeddingSwitchController() {
  const [current, setCurrent] = useState<EmbeddingSwitchCurrent | null>(null);
  const [plan, setPlan] = useState<EmbeddingSwitchPlan | null>(null);
  const [busy, setBusy] = useState<EmbeddingSwitchBusy>(null);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const [supported, setSupported] = useState<boolean | null>(null);
  const supportedRef = useRef<boolean | null>(null);
  const alive = useRef(false);
  const generation = useRef(0);
  const acceptedRead = useRef(0);
  const action = useRef<Promise<unknown> | null>(null);

  const acceptCurrent = useCallback((next: SequencedCurrent): boolean => {
    if (!alive.current || next.sequence < acceptedRead.current) return false;
    acceptedRead.current = next.sequence;
    setCurrent(next.current);
    setRefreshFailed(false);
    return true;
  }, []);

  const refresh =
    useCallback(async (): Promise<EmbeddingSwitchCurrent | null> => {
      const owner = generation.current;
      try {
        const next = await readCurrentSequenced();
        if (!alive.current || owner !== generation.current) return null;
        supportedRef.current = true;
        setSupported(true);
        return acceptCurrent(next) ? next.current : null;
      } catch (reason) {
        if (alive.current && owner === generation.current) {
          if (unsupportedRoute(reason)) {
            supportedRef.current = false;
            setSupported(false);
            setRefreshFailed(false);
          } else {
            setRefreshFailed(true);
          }
        }
        return null;
      }
    }, [acceptCurrent]);

  useEffect(() => {
    alive.current = true;
    void refresh();
    return () => {
      alive.current = false;
      generation.current++;
      action.current = null;
    };
  }, [refresh]);

  useEffect(() => {
    if (!alive.current || supported === false) return;
    const unsubscribe = subscribePoll({
      key: "embedding-switch",
      intervalMs: current?.phase === "idle" ? 12_000 : 1_000,
      fetch: async () => {
        try {
          return await readCurrentSequenced();
        } catch (reason) {
          if (alive.current) {
            if (unsupportedRoute(reason)) {
              supportedRef.current = false;
              setSupported(false);
              setRefreshFailed(false);
            } else {
              setRefreshFailed(true);
            }
          }
          throw reason;
        }
      },
      onValue: (value) => {
        const next = value as Partial<SequencedCurrent>;
        if (typeof next.sequence === "number" && next.current !== undefined)
          acceptCurrent(next as SequencedCurrent);
      },
    });
    return unsubscribe;
  }, [acceptCurrent, current?.phase, supported]);

  const run = useCallback(
    <T>(
      kind: Exclude<EmbeddingSwitchBusy, null>,
      operation: () => Promise<T>,
      commit: (value: T) => void,
    ): Promise<T | undefined> => {
      if (!alive.current || action.current) return Promise.resolve(undefined);
      const owner = generation.current;
      // A current-state read that started before a lifecycle mutation must not
      // overwrite its authoritative receipt when it resolves afterwards.
      if (kind === "start" || kind === "cancel")
        acceptedRead.current = ++currentReadSequence;
      setBusy(kind);
      setErrorCode(null);
      const pending = (async () => {
        try {
          const result = await operation();
          if (!alive.current || owner !== generation.current) return undefined;
          if (kind === "start" || kind === "cancel")
            acceptedRead.current = ++currentReadSequence;
          commit(result);
          return result;
        } catch (reason) {
          if (!alive.current || owner !== generation.current) return undefined;
          if (unsupportedRoute(reason)) {
            supportedRef.current = false;
            setSupported(false);
            setErrorCode("embedding_switch_unsupported");
            return undefined;
          }
          const refusal = embeddingSwitchRefusal(reason);
          if (refusal?.plan) setPlan(refusal.plan);
          const message = reason instanceof Error ? reason.message : "";
          setErrorCode(
            refusal?.code ??
              (/^embedding_(switch|space)_[a-z0-9_]+$/.test(message)
                ? message
                : "embedding_switch_failed"),
          );
          return undefined;
        } finally {
          if (alive.current && owner === generation.current) {
            action.current = null;
            setBusy(null);
          }
        }
      })();
      action.current = pending;
      return pending;
    },
    [],
  );

  const planActivation = useCallback(
    (profileId: string, expectedRevision?: number) =>
      run(
        "plan",
        () => planEmbeddingSwitch(profileId, expectedRevision),
        setPlan,
      ),
    [run],
  );

  const startPlannedSwitch = useCallback(
    (selected: EmbeddingSwitchPlan | null = plan) => {
      if (!selected) {
        setErrorCode("embedding_switch_plan_required");
        return Promise.resolve(undefined);
      }
      if (!selected.needed) {
        setErrorCode("embedding_switch_not_needed");
        return Promise.resolve(undefined);
      }
      return run(
        "start",
        () =>
          startEmbeddingSwitch({
            profile_id: selected.target_profile_id,
            expected_revision: selected.target_revision,
            plan_token: selected.plan_token,
            requested_by: "app",
          }),
        (job) => {
          setPlan(null);
          setCurrent((previous) =>
            previous
              ? {
                  ...previous,
                  phase: "running",
                  writes_paused: true,
                  job,
                }
              : previous,
          );
        },
      );
    },
    [plan, run],
  );

  const cancelCurrentSwitch = useCallback(() => {
    const job = current?.job;
    if (!job) {
      setErrorCode("embedding_switch_not_found");
      return Promise.resolve(undefined);
    }
    return run(
      "cancel",
      () => cancelEmbeddingSwitch(job.job_id),
      (updated: EmbeddingSwitchJob) =>
        setCurrent((previous) =>
          previous?.job?.job_id === updated.job_id
            ? { ...previous, job: updated }
            : previous,
        ),
    );
  }, [current?.job, run]);

  return {
    current,
    plan,
    busy,
    errorCode,
    refreshFailed,
    supported,
    refresh,
    planActivation,
    startPlannedSwitch,
    cancelCurrentSwitch,
    shouldUseLegacyActivation: () => supportedRef.current === false,
    clearPlan: () => setPlan(null),
    clearError: () => setErrorCode(null),
  };
}

export type EmbeddingSwitchController = ReturnType<
  typeof useEmbeddingSwitchController
>;

const EmbeddingSwitchContext = createContext<EmbeddingSwitchController | null>(
  null,
);

export function EmbeddingSwitchProvider({ children }: { children: ReactNode }) {
  const controller = useEmbeddingSwitchController();
  return createElement(
    EmbeddingSwitchContext.Provider,
    { value: controller },
    children,
  );
}

export function useEmbeddingSwitch(): EmbeddingSwitchController {
  const controller = useContext(EmbeddingSwitchContext);
  if (!controller) throw new Error("embedding_switch_provider_missing");
  return controller;
}
