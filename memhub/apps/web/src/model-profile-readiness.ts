import type { ModelProbeResult, ModelProfile } from "@one-memory/contracts";

export function profileFromProbe(
  profile: ModelProfile,
  probe: ModelProbeResult | null | undefined,
): ModelProfile | null {
  if (!probe || probe.profile_id !== profile.profile_id) return null;
  const inputRevision = probe.input_profile_revision;
  if (!Number.isSafeInteger(probe.profile_revision)) return null;
  if (inputRevision === undefined || inputRevision === null) {
    if (probe.profile_revision !== profile.revision) return null;
  } else {
    if (
      !Number.isSafeInteger(inputRevision) ||
      (probe.profile_revision !== inputRevision &&
        probe.profile_revision !== inputRevision + 1) ||
      (profile.revision !== inputRevision &&
        profile.revision !== probe.profile_revision)
    )
      return null;
  }
  return {
    ...profile,
    revision: probe.profile_revision,
    capabilities: probe.capabilities,
  };
}

export function modelProfileReady(
  profile: ModelProfile,
  probe: ModelProbeResult | null = null,
  error: string | null = null,
): boolean {
  return (
    !error &&
    (!probe ||
      (probe.profile_id === profile.profile_id &&
        probe.profile_revision === profile.revision &&
        probe.capabilities.endpoint_reachable &&
        probe.capabilities.model_available &&
        probe.capabilities.embedding_available)) &&
    profile.credential_state !== "unavailable" &&
    !(profile.has_configured_api_key && !profile.has_api_key) &&
    profile.capabilities.endpoint_reachable &&
    profile.capabilities.model_available &&
    profile.capabilities.embedding_available
  );
}
