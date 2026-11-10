import type { StorageBackendsSummary } from "@one-memory/contracts";
import { useOnboardingTranslation } from "./onboarding-language";

/** One read-only line naming the storage backends a person set through the
 * CLI (docs/contracts/storage-backends-v1.md §7). The App offers no control:
 * nothing here can change them. Hidden on the default Kuzu + Chroma pair and
 * when an older Core does not report them. */
export function StorageBackendsNote({
  backends,
}: {
  backends: StorageBackendsSummary | null | undefined;
}) {
  const { t } = useOnboardingTranslation();
  if (!backends || backends.is_default) return null;
  return (
    <>
      <p className="r6-settings-note" data-testid="storage-backends-note">
        {t("mode.storageBackends.configured", { label: backends.label })}
      </p>
      {backends.health === "failed" ? (
        <p className="r6-settings-error" role="alert">
          {t("mode.storageBackends.unreachable", {
            label: backends.label,
            code: backends.health_code ?? "storage_backend_probe_failed",
          })}
        </p>
      ) : null}
    </>
  );
}
