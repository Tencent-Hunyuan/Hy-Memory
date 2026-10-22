import { useQuery } from "@tanstack/react-query";
import { getSedimentation, type SedimentationState } from "./api";
import { onClientReset, queryClient } from "./query-client";

const SEDIMENTATION_KEY = ["sedimentation"] as const;
const FRESH_MS = 5_000;

queryClient.setQueryDefaults(SEDIMENTATION_KEY, {
  gcTime: Number.POSITIVE_INFINITY,
  staleTime: FRESH_MS,
});

let revision = 0;
onClientReset(() => {
  revision = 0;
});

/**
 * Reads the server switch without allowing an older request to overwrite a
 * newer PUT receipt committed by the settings page.
 */
export async function refreshSedimentation(): Promise<SedimentationState> {
  const startedAtRevision = revision;
  const next = await getSedimentation();
  const current =
    queryClient.getQueryData<SedimentationState>(SEDIMENTATION_KEY);
  if (startedAtRevision !== revision && current) return current;
  queryClient.setQueryData(SEDIMENTATION_KEY, next);
  return next;
}

/** The PUT response is the latest confirmed server state. */
export function commitSedimentation(value: SedimentationState) {
  revision += 1;
  queryClient.setQueryData(SEDIMENTATION_KEY, value);
}

export function useSedimentation() {
  return useQuery(
    {
      queryKey: SEDIMENTATION_KEY,
      queryFn: refreshSedimentation,
      enabled: false,
    },
    queryClient,
  ).data;
}
