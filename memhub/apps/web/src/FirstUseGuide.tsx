import type {
  GovernedMemory,
  MemoryDetail,
  SourceResponse,
} from "@one-memory/contracts";
import type { MessageKey } from "@one-memory/contracts/gui-i18n";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Copy,
  ExternalLink,
  FileText,
  Link2,
  RefreshCw,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { getMemories, getMemory } from "./api";
import { copyText } from "./copy-text";
import {
  draftForScenario,
  GUIDE_SCENARIOS,
  type GuideRoute,
  type GuideScenario,
  guideDetailAvailable,
  guideHosts,
  sameGuideMemory,
} from "./first-use-model";
import type { RecallHostStatus } from "./global";
import { useGuiLanguage } from "./gui-language";
import { refreshRecallHosts, useRecallHosts } from "./host-state";
import { refreshSources } from "./source-state";

const NO_HOSTS: RecallHostStatus[] = [];

export function FirstUseGuide({
  sources,
  onSources,
  onClose,
}: {
  sources: SourceResponse[];
  onSources(): void;
  onClose(): void;
}) {
  const { t, tn, locale } = useGuiLanguage();
  const [scene, setScene] = useState<GuideScenario>("same");
  const [route, setRoute] = useState<GuideRoute>("context");
  const [memories, setMemories] = useState<GovernedMemory[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<MemoryDetail | null>(null);
  const hostRead = useRecallHosts();
  const refetchHosts = hostRead.refetch;
  const hostsError = hostRead.isError;
  const hosts = hostsError ? NO_HOSTS : (hostRead.data ?? NO_HOSTS);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<MessageKey | "">("");
  const [reload, setReload] = useState(0);
  const [targetId, setTargetId] = useState("");
  const [topic, setTopic] = useState("");
  const [notice, setNotice] = useState<MessageKey | "">("");
  const [noticeHost, setNoticeHost] = useState("");
  const [copiedKey, setCopiedKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [reported, setReported] = useState<"ok" | "missing" | null>(null);
  const mounted = useRef(true);
  const actionEpoch = useRef(0);
  const contextKey = JSON.stringify([
    scene,
    route,
    selectedId,
    detail?.memory.revision,
    targetId,
    topic,
    reload,
    sources.map((item) => [item.source_id, item.authorized]),
  ]);
  const contextRef = useRef(contextKey);
  contextRef.current = contextKey;
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      actionEpoch.current++;
    };
  }, []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: reload is the explicit user refresh token.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    void refetchHosts();
    getMemories()
      .then((items) => {
        if (cancelled) return;
        const usable = items.filter(
          (item) =>
            item.status === "confirmed" &&
            !item.deleted_at &&
            item.evidence_ids.length > 0,
        );
        setMemories(usable);
        setSelectedId((id) =>
          usable.some((item) => item.memory_id === id)
            ? id
            : (usable[0]?.memory_id ?? ""),
        );
      })
      .catch(() => {
        if (!cancelled) {
          setMemories([]);
          setSelectedId("");
          setError("guideReadError");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [reload]);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError("");
    setNotice("");
    setCopiedKey("");
    setReported(null);
    if (!selectedId) {
      setDetailLoading(false);
      return;
    }
    setDetailLoading(true);
    getMemory(selectedId)
      .then((result) => {
        if (cancelled) return;
        if (result.memory.memory_id !== selectedId)
          throw new Error("wrong identity");
        setDetail(result);
        setTopic(
          result.memory.tags.slice(0, 2).join("、").slice(0, 160) ||
            "之前讨论的工作",
        );
      })
      .catch(() => {
        if (!cancelled) setError("guideDetailError");
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const available =
    detail !== null &&
    detail.memory.memory_id === selectedId &&
    !detailLoading &&
    guideDetailAvailable(detail, sources);
  const destinations = guideHosts(scene, detail, hosts);
  const host = destinations.find((item) => item.hostId === targetId);
  const canQuery = !!host?.installed && host.selfCheck?.state === "wired";
  const effectiveRoute = route === "query" && canQuery ? "query" : "context";
  const sourceIds = [
    ...new Set(
      detail?.evidence
        .map((item) => item.source_id)
        .filter((id): id is string => !!id),
    ),
  ];
  const nameOf = (id: string) =>
    sources.find((item) => item.source_id === id)?.display_name ??
    (id === "one-memory-manual" ? "端内添加" : id);
  // The handoff draft is task content, not UI copy: retain its existing wording.
  const sourceLabel = sourceIds.map(nameOf).join("、") || "原应用";
  const displaySourceLabel =
    sourceIds
      .map((id) => (id === "one-memory-manual" ? t("guideManual") : nameOf(id)))
      .join(t("guideListSeparator")) || t("guideOriginalApp");
  const draft =
    available && detail
      ? draftForScenario(scene, effectiveRoute, topic, detail, sourceLabel)
      : "";
  const scenario =
    GUIDE_SCENARIOS.find((item) => item.id === scene) ?? GUIDE_SCENARIOS[0];
  const importedMessages = sources
    .filter((item) => item.authorized)
    .reduce((total, item) => total + (item.imported_messages ?? 0), 0);

  function changed() {
    actionEpoch.current++;
    setNotice("");
    setCopiedKey("");
    setReported(null);
    setBusy(false);
  }
  async function carry(open: boolean) {
    if (!detail || !draft || busy) return;
    const original = detail;
    const key = contextKey;
    const epoch = ++actionEpoch.current;
    const current = () =>
      mounted.current &&
      contextRef.current === key &&
      actionEpoch.current === epoch;
    setBusy(true);
    setNotice("");
    setReported(null);
    let validated = false;
    try {
      const [fresh, freshSources, freshHosts] = await Promise.all([
        getMemory(original.memory.memory_id),
        refreshSources(),
        effectiveRoute === "query" && window.oneMemory
          ? refreshRecallHosts()
          : Promise.resolve(hosts),
      ]);
      if (!current()) return;
      if (
        !sameGuideMemory(original, fresh) ||
        !guideDetailAvailable(fresh, freshSources)
      ) {
        setDetail(null);
        setCopiedKey("");
        setError("guideChanged");
        return;
      }
      if (
        effectiveRoute === "query" &&
        !freshHosts.some(
          (item) =>
            item.hostId === host?.hostId &&
            item.present &&
            item.installed &&
            item.selfCheck?.state === "wired",
        )
      ) {
        setCopiedKey("");
        setNotice("guideConnectionChanged");
        return;
      }
      validated = true;
      await copyText(draft);
      if (!current()) return;
      setCopiedKey(key);
      if (open && host?.handoff === "open" && window.oneMemory) {
        try {
          const result = await window.oneMemory.openHandoff(host.hostId, draft);
          if (current()) {
            setNoticeHost(host.label);
            setNotice(
              result.opened ? "guideCopiedOpened" : "guideCopiedNotFilled",
            );
          }
        } catch {
          if (current()) setNotice("guideCopiedNotOpened");
        }
      } else setNotice("guideCopied");
    } catch {
      if (current()) {
        if (!validated) {
          setDetail(null);
          setCopiedKey("");
          setError("guideValidationError");
        }
        setNotice("guideCopyError");
      }
    } finally {
      if (current()) setBusy(false);
    }
  }

  return (
    <section className="view first-use-view" aria-labelledby="first-use-title">
      <header className="first-use-heading">
        <div>
          <p className="first-use-eyebrow">{t("guideEyebrow")}</p>
          <h1 id="first-use-title">{t("guideTitle")}</h1>
        </div>
        <button type="button" className="quiet" onClick={onClose}>
          <ArrowLeft size={16} aria-hidden="true" />
          {t("guideBack")}
        </button>
      </header>
      <fieldset className="first-use-scenarios">
        <legend className="sr-only">{t("guideChooseScene")}</legend>
        {GUIDE_SCENARIOS.map((item, index) => (
          <label key={item.id} data-selected={scene === item.id}>
            <input
              type="radio"
              name="guide-scene"
              value={item.id}
              checked={scene === item.id}
              onChange={() => {
                changed();
                setScene(item.id);
                setTargetId("");
                setRoute("context");
              }}
            />
            <span className="first-use-number">0{index + 1}</span>
            <span>
              {t(
                item.id === "same"
                  ? "guideSame"
                  : item.id === "switch"
                    ? "guideSwitch"
                    : "guideNew",
              )}
            </span>
          </label>
        ))}
      </fieldset>
      <p className="first-use-description">
        {t(
          scenario.id === "same"
            ? "guideSameDescription"
            : scenario.id === "switch"
              ? "guideSwitchDescription"
              : "guideNewDescription",
        )}
      </p>
      {importedMessages > 0 && (
        <p className="first-use-receipt">
          {tn("guideSaved", importedMessages)}
          {loading
            ? t("guideReadingMemories")
            : tn("guideLoaded", memories.length)}
          {t("guideSeparate")}
        </p>
      )}
      <div className="first-use-path">
        <span>{t("guideChooseBackground")}</span>
        <ArrowRight size={14} aria-hidden="true" />
        <span>{t("guideCheckSources")}</span>
        <ArrowRight size={14} aria-hidden="true" />
        <span>{t("guideTry")}</span>
      </div>
      {error && (
        <p className="error-banner" role="alert">
          {t(error)}
        </p>
      )}
      {loading ? (
        <p role="status">{t("guideLoading")}</p>
      ) : !memories.length ? (
        <section className="first-use-empty">
          <FileText size={28} aria-hidden="true" />
          <h2>{t("guideEmptyTitle")}</h2>
          <p>{t("guideEmptyDescription")}</p>
          <div>
            <button type="button" onClick={onClose}>
              {t("guideAdd")}
            </button>
            <button type="button" className="secondary" onClick={onSources}>
              {t("guideConnections")}
            </button>
            <button
              type="button"
              className="quiet"
              onClick={() => setReload((n) => n + 1)}
            >
              <RefreshCw size={15} />
              {t("guideRefresh")}
            </button>
          </div>
        </section>
      ) : (
        <div className="first-use-columns">
          <section
            className="first-use-background"
            aria-labelledby="guide-background-title"
          >
            <header>
              <h2 id="guide-background-title">{t("guideBackgroundTitle")}</h2>
              <button
                type="button"
                className="quiet"
                title={t("guideRefreshExample")}
                aria-label={t("guideRefreshExample")}
                onClick={() => {
                  changed();
                  setDetail(null);
                  setSelectedId("");
                  setReload((n) => n + 1);
                }}
              >
                <RefreshCw size={16} />
              </button>
            </header>
            <label className="first-use-field">
              {t("guideChooseMemory")}
              <select
                value={selectedId}
                onChange={(event) => {
                  changed();
                  setSelectedId(event.target.value);
                }}
              >
                {memories.map((item) => (
                  <option key={item.memory_id} value={item.memory_id}>
                    {item.content.length > 90
                      ? `${item.content.slice(0, 90)}…`
                      : item.content}
                  </option>
                ))}
              </select>
            </label>
            <p className="first-use-meta">
              {tn("guideLoadedScope", memories.length)}
            </p>
            {detailLoading ? (
              <p role="status">{t("guideCheckingEvidence")}</p>
            ) : available && detail ? (
              <>
                <blockquote className="first-use-memory">
                  {detail.memory.content}
                </blockquote>
                <p className="first-use-meta">
                  {t("guideRevision", {
                    source: displaySourceLabel,
                    revision: detail.memory.revision.toLocaleString(locale),
                  })}
                </p>
                <p className="first-use-scope">
                  {t("guideScope")}
                  {detail.memory.scopes.includes("all-apps")
                    ? t("guideAllApps")
                    : detail.memory.scopes.includes("private")
                      ? t("guidePrivate")
                      : t("guideLimited")}
                </p>
                <details className="first-use-evidence" open>
                  <summary>
                    {tn("guideEvidence", detail.evidence.length)}
                  </summary>
                  {detail.evidence.map((item) => (
                    <article key={item.evidence_id}>
                      <header>
                        <span>
                          {item.source_id
                            ? nameOf(item.source_id)
                            : t("guideUnknownSource")}
                        </span>
                        <span>
                          {item.written_by === "person"
                            ? t("guideYou")
                            : item.written_by === "agent"
                              ? "Agent"
                              : t("guideUnknownAuthor")}{" "}
                          ·{" "}
                          {item.created_at
                            ? new Date(item.created_at).toLocaleString(locale)
                            : t("guideUnknownTime")}
                        </span>
                      </header>
                      <p>{item.verbatim}</p>
                    </article>
                  ))}
                </details>
              </>
            ) : (
              <p className="first-use-unavailable">{t("guideUnavailable")}</p>
            )}
          </section>
          <section
            className="first-use-destination"
            aria-labelledby="guide-draft-title"
          >
            <h2 id="guide-draft-title">{t("guideDraftTitle")}</h2>
            <label className="first-use-field">
              {t("guideChooseApp")}
              <select
                value={host?.hostId ?? ""}
                onChange={(event) => {
                  changed();
                  setTargetId(event.target.value);
                  setRoute("context");
                }}
              >
                <option value="">{t("guideCopyOnly")}</option>
                {destinations.map((item) => (
                  <option key={item.hostId} value={item.hostId}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            <p className="first-use-connection">
              {hostsError
                ? t("guideHostsError")
                : !host
                  ? t("guideNoApp")
                  : canQuery
                    ? t("guideWired")
                    : host.installed
                      ? t("guideInstalled")
                      : t("guideNotInstalled")}
            </p>
            <button
              type="button"
              className="quiet first-use-connection-link"
              onClick={onSources}
            >
              <Link2 size={15} aria-hidden="true" />
              {t("guideManage")}
            </button>
            <fieldset className="first-use-routes">
              <legend className="sr-only">{t("guideChooseRoute")}</legend>
              <label>
                <input
                  type="radio"
                  name="guide-route"
                  checked={effectiveRoute === "context"}
                  onChange={() => {
                    changed();
                    setRoute("context");
                  }}
                />
                {t("guideContext")}
              </label>
              <label>
                <input
                  type="radio"
                  name="guide-route"
                  checked={effectiveRoute === "query"}
                  disabled={!canQuery}
                  onChange={() => {
                    changed();
                    setRoute("query");
                  }}
                />
                {t("guideQuery")}
              </label>
            </fieldset>
            {effectiveRoute === "query" && (
              <label className="first-use-field">
                {t("guideTopic")}
                <input
                  value={topic}
                  maxLength={160}
                  onChange={(event) => {
                    changed();
                    setTopic(event.target.value);
                  }}
                />
              </label>
            )}
            <p className="first-use-meta">
              {effectiveRoute === "query"
                ? t("guideQueryHint")
                : scene === "new"
                  ? t("guideNewHint")
                  : t("guideContextHint")}
            </p>
            <textarea
              className="first-use-draft"
              aria-label={t("guidePreview")}
              readOnly
              value={draft}
              placeholder={t("guidePreviewPlaceholder")}
              spellCheck={false}
            />
            <div className="first-use-actions">
              <button
                type="button"
                disabled={!draft || busy}
                onClick={() => void carry(false)}
              >
                <Copy size={16} aria-hidden="true" />
                {busy ? t("guideChecking") : t("guideCopy")}
              </button>
              {host?.handoff === "open" && (
                <button
                  type="button"
                  className="secondary"
                  disabled={!draft || busy}
                  onClick={() => void carry(true)}
                >
                  <ExternalLink size={16} aria-hidden="true" />
                  {t("guideCopyOpen", { host: host.label })}
                </button>
              )}
            </div>
            {notice && (
              <p className="first-use-notice" role="status">
                {t(notice, { host: noticeHost })}
              </p>
            )}
            {copiedKey === contextKey && (
              <section className="first-use-result">
                <p>{t("guideNoReceipt")}</p>
                <div>
                  <button
                    type="button"
                    className="quiet"
                    onClick={() => setReported("ok")}
                  >
                    <Check size={15} />
                    {t("guideVerified")}
                  </button>
                  <button
                    type="button"
                    className="quiet"
                    onClick={() => setReported("missing")}
                  >
                    {t("guideMissing")}
                  </button>
                </div>
                {reported && (
                  <p role="status">
                    {reported === "ok"
                      ? t("guideFeedbackOk")
                      : t("guideFeedbackMissing")}
                  </p>
                )}
              </section>
            )}
          </section>
        </div>
      )}
    </section>
  );
}

export function FirstUseInvitation({
  onOpen,
  onDismiss,
}: {
  onOpen(): void;
  onDismiss(): void;
}) {
  const { t } = useGuiLanguage();
  return (
    <aside className="first-use-invitation" aria-label={t("guideInvitation")}>
      <span>{t("guideInvitationHint")}</span>
      <button type="button" className="quiet" onClick={onOpen}>
        {t("guideTryOnce")}
        <ArrowRight size={15} aria-hidden="true" />
      </button>
      <button
        type="button"
        className="quiet"
        title={t("guideLater")}
        aria-label={t("guideDismiss")}
        onClick={onDismiss}
      >
        <X size={15} />
      </button>
    </aside>
  );
}
