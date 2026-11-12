import type { EvolveOverview, EvolveRunReceipt } from "@one-memory/contracts";
import { useQuery } from "@tanstack/react-query";
import * as api from "./api";
import { onClientReset, queryClient } from "./query-client";

const EVOLVE_KEY = ["evolve", "local"] as const;

queryClient.setQueryDefaults(EVOLVE_KEY, {
  gcTime: Number.POSITIVE_INFINITY,
  staleTime: 1_000,
});

let mutationGeneration = 0;
let readsStarted = 0;
let readsLanded = 0;
onClientReset(() => {
  mutationGeneration = 0;
  readsStarted = 0;
  readsLanded = 0;
});

export async function refreshEvolveOverview() {
  const generation = mutationGeneration;
  const order = ++readsStarted;
  const next = await api.getEvolveOverview();
  const current = queryClient.getQueryData<EvolveOverview>(EVOLVE_KEY);
  if (generation !== mutationGeneration || order < readsLanded)
    return current ?? next;
  readsLanded = order;
  queryClient.setQueryData(EVOLVE_KEY, next);
  return next;
}

/** Publish an accepted run before polling confirms it so every page agrees. */
export function commitEvolveRun(
  receipt: EvolveRunReceipt,
  fallback?: EvolveOverview,
) {
  mutationGeneration++;
  queryClient.setQueryData<EvolveOverview>(EVOLVE_KEY, (current) => {
    const base = current ?? fallback;
    return base
      ? {
          ...base,
          running: [
            ...base.running.filter(
              (running) => running.user_id !== receipt.user_id,
            ),
            receipt,
          ],
        }
      : current;
  });
  void queryClient.invalidateQueries({ queryKey: EVOLVE_KEY });
}

export function useEvolveState(active = true) {
  return useQuery(
    {
      queryKey: EVOLVE_KEY,
      queryFn: refreshEvolveOverview,
      enabled: active,
      refetchInterval: (query) =>
        query.state.data?.running.length ? 1_500 : 10_000,
    },
    queryClient,
  );
}

export function useEvolveOverview(active = true) {
  return useEvolveState(active).data;
}

export function localEvolveRunning(overview?: EvolveOverview | null) {
  return overview?.running.some((run) => run.user_id === "local") ?? false;
}
