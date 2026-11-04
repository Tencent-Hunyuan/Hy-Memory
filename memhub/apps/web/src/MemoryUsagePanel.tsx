import type {
  DailyMemoryUsage,
  MemoryUsageTotals,
  ModelProfile,
  ModelUsagePricing,
  ModelUsageSettings,
} from "@one-memory/contracts";
import { useEffect, useState } from "react";
import {
  getDailyModelUsage,
  getModelProfiles,
  getModelUsagePricing,
  setModelUsagePricing,
} from "./api";
import { useGuiLanguage } from "./gui-language";
import { subscribePoll } from "./poll-scheduler";
import "./memory-usage.css";

type Period = 1 | 7 | 30;
export function MemoryUsagePanel({ active }: { active: boolean }) {
  const { t, locale } = useGuiLanguage();
  const [expanded, setExpanded] = useState(false);
  const [days, setDays] = useState<Period>(7);
  const [profileId, setProfileId] = useState("");
  const [report, setReport] = useState<DailyMemoryUsage | null>(null);
  const [failed, setFailed] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [pricingOpen, setPricingOpen] = useState(false);
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [settings, setSettings] = useState<ModelUsageSettings | null>(null);
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const number = new Intl.NumberFormat(locale);
  // A fresh closure discards replies from a previous range, model or page.
  // biome-ignore lint/correctness/useExhaustiveDependencies: refresh is an explicit read trigger.
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setReport(null);
    setFailed(false);
    const load = async () => {
      try {
        const next = await getDailyModelUsage(
          expanded ? days : 1,
          timezone,
          profileId || undefined,
        );
        if (!cancelled) {
          setReport(next);
          setFailed(false);
        }
      } catch {
        if (!cancelled) setFailed(true);
      }
    };
    void load();
    const stop = subscribePoll({
      key: "model-usage",
      intervalMs: 15_000,
      run: load,
    });
    return () => {
      cancelled = true;
      stop();
    };
  }, [active, expanded, days, timezone, profileId, refresh]);
  useEffect(() => {
    if (!active || !expanded) return;
    let cancelled = false;
    void Promise.all([getModelProfiles(), getModelUsagePricing()])
      .then(([models, prices]) => {
        if (!cancelled) {
          setProfiles(models);
          setSettings(prices);
        }
      })
      .catch(() => {
        if (!cancelled) setSettings(null);
      });
    return () => {
      cancelled = true;
    };
  }, [active, expanded]);
  const today = report?.days[0];
  const options = new Map(
    profiles.map((p) => [p.profile_id, `${p.name} · ${p.model}`]),
  );
  for (const model of report?.models ?? [])
    if (!options.has(model.profile_id))
      options.set(model.profile_id, model.model);
  const amount = (totals: MemoryUsageTotals) =>
    costLabel(totals, locale, t("dailyUsage.noPrice"));
  const columns = [
    t("dailyUsage.date"),
    t("usageRequests"),
    t("dailyUsage.input"),
    t("dailyUsage.output"),
    t("dailyUsage.cached"),
    t("usageTotalTokens"),
    t("dailyUsage.cost"),
  ];
  return (
    <details
      className="memory-usage"
      onToggle={(e) => setExpanded(e.currentTarget.open)}
    >
      <summary>
        <strong>{t("dailyUsage.title")}</strong>
        <span>
          {t("dailyUsage.today")} ·{" "}
          {today && !failed
            ? `${number.format(today.request_count)} ${t("usageRequests")} · ${number.format(today.total_tokens)} token · ${amount(today)}`
            : failed
              ? t("usageReadError")
              : "—"}
        </span>
      </summary>
      {expanded && (
        <div className="memory-usage-body">
          <div className="memory-usage-toolbar">
            <label>
              {t("usageRange")}
              <select
                value={days}
                onChange={(e) => setDays(Number(e.target.value) as Period)}
              >
                <option value={1}>{t("dailyUsage.today")}</option>
                <option value={7}>{t("dailyUsage.sevenDays")}</option>
                <option value={30}>{t("dailyUsage.thirtyDays")}</option>
              </select>
            </label>
            <label>
              {t("dailyUsage.allModels")}
              <select
                value={profileId}
                onChange={(e) => setProfileId(e.target.value)}
              >
                <option value="">{t("dailyUsage.allModels")}</option>
                {[...options].map(([id, name]) => (
                  <option key={id} value={id}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <span>{timezone}</span>
            <button
              type="button"
              onClick={() => setRefresh((value) => value + 1)}
            >
              {t("debugRefresh")}
            </button>
            <button
              type="button"
              onClick={() => setPricingOpen((value) => !value)}
              aria-expanded={pricingOpen}
            >
              {t("dailyUsage.pricing")}
            </button>
          </div>
          {failed && <p role="alert">{t("usageReadError")}</p>}
          {!report && !failed && <p role="status">{t("usageLoading")}</p>}
          {report && (
            <>
              <div className="memory-usage-metrics">
                {[
                  [
                    t("usageRequests"),
                    number.format(report.summary.request_count),
                  ],
                  [
                    t("usageTotalTokens"),
                    number.format(report.summary.total_tokens),
                  ],
                  [
                    t("dailyUsage.cached"),
                    number.format(report.summary.cached_prompt_tokens),
                  ],
                  [t("dailyUsage.cost"), amount(report.summary)],
                ].map(([label, value]) => (
                  <div key={label}>
                    <span>{label}</span>
                    <strong>{value}</strong>
                  </div>
                ))}
              </div>
              <p>
                {t("dailyUsage.status")}: {report.summary.succeeded} /{" "}
                {report.summary.failed} / {report.summary.in_flight}
                {report.summary.unknown_usage_requests > 0 && (
                  <>
                    {" "}
                    ·{" "}
                    {t("dailyUsage.missing", {
                      count: report.summary.unknown_usage_requests,
                    })}
                  </>
                )}
              </p>
              <div className="memory-usage-table">
                <table aria-label={t("dailyUsage.title")}>
                  <thead>
                    <tr>
                      {columns.map((label) => (
                        <th key={label} scope="col">
                          {label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {report.days.map((day) => (
                      <tr key={day.date}>
                        <th scope="row">
                          {day.date}
                          {day.coverage === "partial" ? " *" : ""}
                        </th>
                        {[
                          day.request_count,
                          day.prompt_tokens,
                          day.completion_tokens,
                          day.cached_prompt_tokens,
                          day.total_tokens,
                        ].map((value, index) => (
                          <td key={columns[index + 1]}>
                            {day.coverage === "unrecorded"
                              ? t("dailyUsage.unknown")
                              : number.format(value)}
                          </td>
                        ))}
                        <td>
                          {day.coverage === "unrecorded"
                            ? t("dailyUsage.unknown")
                            : amount(day)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <ul className="memory-usage-models">
                {report.models.map((model) => (
                  <li key={`${model.profile_id}:${model.model}`}>
                    <strong>{model.model}</strong> ·{" "}
                    {number.format(model.request_count)} {t("usageRequests")} ·{" "}
                    {number.format(model.total_tokens)} token · {amount(model)}
                  </li>
                ))}
              </ul>
              <p className="memory-usage-note">
                {t("dailyUsage.history", {
                  date: new Date(report.recorded_from).toLocaleString(locale),
                })}
              </p>
              <p className="memory-usage-note">
                {t("dailyUsage.note")}{" "}
                <a
                  href={report.pricing_source}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  DeepSeek
                </a>
              </p>
            </>
          )}
          {pricingOpen &&
            (settings ? (
              <PricingEditor
                profiles={profiles}
                settings={settings}
                onSaved={(id, pricing) => {
                  setSettings((previous) =>
                    previous
                      ? {
                          ...previous,
                          pricing: { ...previous.pricing, [id]: pricing },
                        }
                      : previous,
                  );
                  setRefresh((value) => value + 1);
                }}
              />
            ) : (
              <p role="alert">{t("usageReadError")}</p>
            ))}
        </div>
      )}
    </details>
  );
}

export function costLabel(
  totals: MemoryUsageTotals,
  locale: string,
  unknown: string,
): string {
  const costs = totals.costs.map((cost) => {
    const format = new Intl.NumberFormat(locale, {
      style: "currency",
      currency: cost.currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 8,
    });
    const min = format.format(Number(cost.estimated_min)),
      max = format.format(Number(cost.estimated_max));
    return min === max ? min : `${min} – ${max}`;
  });
  if (totals.unpriced_requests > 0) costs.push(unknown);
  return costs.join(" + ") || (totals.request_count === 0 ? "0" : unknown);
}

function PricingEditor({
  profiles,
  settings,
  onSaved,
}: {
  profiles: ModelProfile[];
  settings: ModelUsageSettings;
  onSaved(id: string, value: ModelUsagePricing): void;
}) {
  const { t } = useGuiLanguage();
  const [selected, setSelected] = useState(
    profiles.find((p) => p.is_active)?.profile_id ??
      profiles[0]?.profile_id ??
      "",
  );
  const profile = profiles.find((p) => p.profile_id === selected);
  if (!profile) return <p>{t("usageEmpty")}</p>;
  const initial = settings.pricing[selected];
  return (
    <div className="memory-usage-pricing">
      <label>
        {t("dailyUsage.pricing")}
        <select value={selected} onChange={(e) => setSelected(e.target.value)}>
          {profiles.map((p) => (
            <option key={p.profile_id} value={p.profile_id}>
              {p.name} · {p.model}
            </option>
          ))}
        </select>
      </label>
      <PricingForm
        key={`${selected}:${profile.model}`}
        profile={profile}
        initial={initial?.model === profile.model ? initial : undefined}
        onSaved={(value) => onSaved(selected, value)}
      />
    </div>
  );
}

function PricingForm({
  profile,
  initial,
  onSaved,
}: {
  profile: ModelProfile;
  initial?: ModelUsagePricing;
  onSaved(value: ModelUsagePricing): void;
}) {
  const { t } = useGuiLanguage();
  const [currency, setCurrency] = useState(initial?.currency ?? "CNY");
  const [input, setInput] = useState(initial?.input_per_million ?? "");
  const [cached, setCached] = useState(initial?.cached_input_per_million ?? "");
  const [output, setOutput] = useState(initial?.output_per_million ?? "");
  const [billing, setBilling] = useState(
    initial?.billing_url ??
      (officialDeepSeek(profile.base_url)
        ? "https://platform.deepseek.com/usage"
        : ""),
  );
  const [state, setState] = useState<"idle" | "saving" | "saved" | "error">(
    "idle",
  );
  return (
    <form
      className="memory-usage-pricing-form"
      onChange={() => setState("idle")}
      onSubmit={async (event) => {
        event.preventDefault();
        setState("saving");
        try {
          const value = await setModelUsagePricing(profile.profile_id, {
            model: profile.model,
            currency,
            input_per_million: input || null,
            cached_input_per_million: cached || null,
            output_per_million: output || null,
            billing_url: billing || null,
          });
          onSaved(value);
          setState("saved");
        } catch {
          setState("error");
        }
      }}
    >
      <p>{t("dailyUsage.perMillion")}</p>
      <div className="memory-usage-price-fields">
        <label>
          {t("dailyUsage.currency")}
          <input
            value={currency}
            required
            minLength={3}
            maxLength={3}
            pattern="[A-Z]{3}"
            onChange={(e) => setCurrency(e.target.value.toUpperCase())}
          />
        </label>
        {(
          [
            [t("dailyUsage.input"), input, setInput],
            [t("dailyUsage.cached"), cached, setCached],
            [t("dailyUsage.output"), output, setOutput],
          ] as const
        ).map(([label, value, setter]) => (
          <label key={label}>
            {label}
            <input
              type="number"
              min="0"
              max="1000000000"
              step="any"
              value={value}
              onChange={(e) => setter(e.target.value)}
            />
          </label>
        ))}
      </div>
      <label>
        {t("dailyUsage.billing")}
        <input
          type="url"
          value={billing}
          placeholder="https://"
          maxLength={2048}
          onChange={(e) => setBilling(e.target.value)}
        />
      </label>
      <p className="memory-usage-note">{t("dailyUsage.freeRates")}</p>
      <button type="submit" disabled={state === "saving"}>
        {t("dailyUsage.save")}
      </button>
      {initial?.billing_url || officialDeepSeek(profile.base_url) ? (
        <a
          href={initial?.billing_url ?? "https://platform.deepseek.com/usage"}
          target="_blank"
          rel="noopener noreferrer"
        >
          {t("dailyUsage.openBilling")}
        </a>
      ) : null}
      {state === "saved" && <span role="status">{t("dailyUsage.saved")}</span>}
      {state === "error" && (
        <span role="alert">{t("dailyUsage.saveError")}</span>
      )}
    </form>
  );
}
function officialDeepSeek(url: string | null): boolean {
  try {
    return new URL(url ?? "").hostname === "api.deepseek.com";
  } catch {
    return false;
  }
}
