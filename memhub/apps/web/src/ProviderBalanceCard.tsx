import type {
  ModelProfile,
  ProviderAccountBalance,
} from "@one-memory/contracts";
import { useEffect, useState } from "react";
import { getProviderBalance } from "./api";
import { useGuiLanguage } from "./gui-language";
import { subscribePoll } from "./poll-scheduler";

export function ProviderBalanceCard({
  profiles,
  selectedProfileId,
  active,
}: {
  profiles: ModelProfile[];
  selectedProfileId: string;
  active: boolean;
}) {
  const { t, locale } = useGuiLanguage();
  const profile =
    profiles.find((p) => p.profile_id === selectedProfileId) ??
    profiles.find((p) => p.is_active) ??
    profiles[0];
  const id = profile?.profile_id;
  const revision = profile?.revision;
  const [balance, setBalance] = useState<ProviderAccountBalance | null>(null);
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);

  // biome-ignore lint/correctness/useExhaustiveDependencies: revision changes the credential behind this read.
  useEffect(() => {
    if (!active || !id) return;
    let cancelled = false;
    let inFlight = false;
    setBalance(null);
    const load = async (force = false) => {
      if (inFlight) return;
      inFlight = true;
      setLoading(true);
      try {
        const next = await getProviderBalance(id, force);
        if (!cancelled) setBalance(next);
      } catch {
        if (!cancelled)
          setBalance({
            schema_version: 1,
            profile_id: id,
            state: "unavailable",
            balances: [],
            is_available: null,
            checked_at: null,
            source_url: null,
          });
      } finally {
        inFlight = false;
        if (!cancelled) setLoading(false);
      }
    };
    void load(refresh > 0);
    const stop = subscribePoll({
      key: "provider-balance",
      intervalMs: 60_000,
      run: () => load(),
    });
    return () => {
      cancelled = true;
      stop();
    };
    // A revision rotates credentials; a manual refresh bypasses the ordinary cache.
  }, [active, id, revision, refresh]);

  if (!profile) return null;
  const money = (amount: string, currency: string) =>
    new Intl.NumberFormat(locale, {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 8,
    }).format(Number(amount));
  return (
    <section
      className="provider-balance"
      aria-label={t("dailyUsage.balanceTitle")}
    >
      <div className="provider-balance-title">
        <strong>
          {t("dailyUsage.balanceTitle")} · {profile.name}
        </strong>
        <button
          type="button"
          disabled={loading}
          onClick={() => setRefresh((value) => value + 1)}
        >
          {t("dailyUsage.refreshBalance")}
        </button>
      </div>
      {loading && !balance && <p>{t("dailyUsage.balanceLoading")}</p>}
      {balance?.state === "ready" && (
        <>
          {balance.balances.map((item) => (
            <div className="provider-balance-values" key={item.currency}>
              {[
                [t("dailyUsage.totalBalance"), item.total_balance],
                [t("dailyUsage.grantedBalance"), item.granted_balance],
                [t("dailyUsage.toppedUpBalance"), item.topped_up_balance],
              ].map(([label, amount]) => (
                <div key={label}>
                  <span>{label}</span>
                  <strong>{money(amount, item.currency)}</strong>
                </div>
              ))}
            </div>
          ))}
          {!balance.balances.length && <p>{t("dailyUsage.noBalanceInfo")}</p>}
          {balance.checked_at && (
            <p className="memory-usage-note">
              {t("dailyUsage.balanceCheckedAt", {
                time: new Date(balance.checked_at).toLocaleString(locale),
              })}
            </p>
          )}
        </>
      )}
      {balance?.state === "unsupported" && (
        <p>{t("dailyUsage.balanceUnsupported")}</p>
      )}
      {balance?.state === "credentials_unavailable" && (
        <p role="alert">{t("dailyUsage.balanceCredentials")}</p>
      )}
      {balance?.state === "unavailable" && (
        <p role="alert">{t("dailyUsage.balanceUnavailable")}</p>
      )}
      <p className="memory-usage-note">{t("dailyUsage.balanceNote")}</p>
    </section>
  );
}
