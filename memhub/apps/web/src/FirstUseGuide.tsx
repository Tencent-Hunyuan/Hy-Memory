import type {
  GovernedMemory,
  MemoryDetail,
  SourceResponse,
  UiMessage,
} from "@one-memory/contracts";
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
import { refreshRecallHosts, useRecallHosts } from "./host-state";
import { refreshSources } from "./source-state";
import { useTranslate, useUiLanguage } from "./ui-language";

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
  const t = useTranslate();
  const language = useUiLanguage();
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
  const [error, setError] = useState<UiMessage | "">("");
  const [reload, setReload] = useState(0);
  const [targetId, setTargetId] = useState("");
  const [topic, setTopic] = useState("");
  const [notice, setNotice] = useState<{
    key: UiMessage;
    name?: string;
  } | null>(null);
  const [copiedKey, setCopiedKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [reported, setReported] = useState<"ok" | "missing" | null>(null);
  const mounted = useRef(true);
  const actionEpoch = useRef(0);
  const previousLanguage = useRef(language);
  const contextKey = JSON.stringify([
    language,
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
          setError("暂时无法读取记忆，可稍后重试。");
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
    setNotice(null);
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
        if (!cancelled) setError("这条记忆暂不可读，请换一条或刷新。");
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  useEffect(() => {
    if (previousLanguage.current === language) return;
    previousLanguage.current = language;
    actionEpoch.current++;
    setBusy(false);
    setNotice(null);
    setCopiedKey("");
    setReported(null);
  }, [language]);

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
  const sourceLabel = sourceIds.map(nameOf).join("、") || "原应用";
  const displaySourceLabel =
    sourceIds
      .map(
        (id) =>
          sources.find((item) => item.source_id === id)?.display_name ??
          (id === "one-memory-manual" ? t("端内添加") : id),
      )
      .join(t("、")) || t("原应用");
  const draft =
    available && detail
      ? draftForScenario(
          scene,
          effectiveRoute,
          topic,
          detail,
          sourceLabel,
          language,
        )
      : "";
  const scenario =
    GUIDE_SCENARIOS.find((item) => item.id === scene) ?? GUIDE_SCENARIOS[0];
  const importedMessages = sources
    .filter((item) => item.authorized)
    .reduce((total, item) => total + (item.imported_messages ?? 0), 0);

  function changed() {
    actionEpoch.current++;
    setNotice(null);
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
    setNotice(null);
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
        setError("记忆内容或授权已变化，请刷新后重新核对。");
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
        setNotice({ key: "连接状态已变化，尚未复制。请重新选择方式。" });
        return;
      }
      validated = true;
      await copyText(draft);
      if (!current()) return;
      setCopiedKey(key);
      if (open && host?.handoff === "open" && window.oneMemory) {
        try {
          const result = await window.oneMemory.openHandoff(host.hostId, draft);
          if (current())
            setNotice(
              result.opened
                ? {
                    key: "已复制并请求打开 {name}。尚未发送，请核对输入框。",
                    name: host.label,
                  }
                : { key: "已完整复制，未能预填。可在目标应用粘贴，尚未发送。" },
            );
        } catch {
          if (current())
            setNotice({ key: "已完整复制，但未能打开目标应用。尚未发送。" });
        }
      } else setNotice({ key: "已完整复制。尚未发送，也没有新增读取授权。" });
    } catch {
      if (current()) {
        if (!validated) {
          setDetail(null);
          setCopiedKey("");
          setError("无法核对当前记忆与授权，旧预览已收起。请刷新后重试。");
        }
        setNotice({
          key: "未能核对最新内容或完成复制。请重试；没有打开目标应用。",
        });
      }
    } finally {
      if (current()) setBusy(false);
    }
  }

  return (
    <section className="view first-use-view" aria-labelledby="first-use-title">
      <header className="first-use-heading">
        <div>
          <p className="first-use-eyebrow">{t("上手体验")}</p>
          <h1 id="first-use-title">{t("让下一次对话接得上")}</h1>
        </div>
        <button type="button" className="quiet" onClick={onClose}>
          <ArrowLeft size={16} aria-hidden="true" />
          {t("返回记忆")}
        </button>
      </header>
      <fieldset className="first-use-scenarios">
        <legend className="sr-only">{t("选择使用场景")}</legend>
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
            <span className="first-use-number">
              {String(index + 1).padStart(2, "0")}
            </span>
            <span>{t(item.label)}</span>
          </label>
        ))}
      </fieldset>
      <p className="first-use-description">{t(scenario.description)}</p>
      {importedMessages > 0 && (
        <p className="first-use-receipt">
          {t("本机已保存 {count} 条原始消息 · ", {
            count: importedMessages.toLocaleString(language),
          })}
          {loading
            ? t("正在读取已整理记忆")
            : t("本页已加载 {count} 条可用记忆", {
                count: memories.length.toLocaleString(language),
              })}
          {t("。两个阶段分别计数。")}
        </p>
      )}
      <div className="first-use-path">
        <span>{t("选择背景")}</span>
        <ArrowRight size={14} aria-hidden="true" />
        <span>{t("核对来源")}</span>
        <ArrowRight size={14} aria-hidden="true" />
        <span>{t("到新对话试用")}</span>
      </div>
      {error && (
        <p className="error-banner" role="alert">
          {t(error)}
        </p>
      )}
      {loading ? (
        <p role="status">{t("正在读取本机已有记忆…")}</p>
      ) : !memories.length ? (
        <section className="first-use-empty">
          <FileText size={28} aria-hidden="true" />
          <h2>{t("先留下一条真正有用的背景")}</h2>
          <p>
            {t(
              "目前没有可用于这次体验的记忆。已导入的原文不等于已经整理好；候选内容也不会被自动带出。",
            )}
          </p>
          <div>
            <button type="button" onClick={onClose}>
              {t("去添加记忆")}
            </button>
            <button type="button" className="secondary" onClick={onSources}>
              {t("查看导入与连接")}
            </button>
            <button
              type="button"
              className="quiet"
              onClick={() => setReload((n) => n + 1)}
            >
              <RefreshCw size={15} />
              {t("刷新")}
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
              <h2 id="guide-background-title">{t("这次用到的背景")}</h2>
              <button
                type="button"
                className="quiet"
                title={t("刷新示例")}
                aria-label={t("刷新示例")}
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
              {t("选择一条记忆")}
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
              {t("从当前已加载的 {count} 条可用记忆中选择，不代表全部历史。", {
                count: memories.length.toLocaleString(language),
              })}
            </p>
            {detailLoading ? (
              <p role="status">{t("正在核对原始依据…")}</p>
            ) : available && detail ? (
              <>
                <blockquote className="first-use-memory">
                  {detail.memory.content}
                </blockquote>
                <p className="first-use-meta">
                  {t("{name} · 版本 {revision} · 系统整理，需你核对", {
                    name: displaySourceLabel,
                    revision: detail.memory.revision.toLocaleString(language),
                  })}
                </p>
                <p className="first-use-scope">
                  {t("适用范围：")}
                  {detail.memory.scopes.includes("all-apps")
                    ? t("已设为跨应用可用，不等于个人偏好")
                    : detail.memory.scopes.includes("private")
                      ? t("已停止外部记忆召回；手动带出需另行确认")
                      : t("限定范围，未扩大外部召回范围")}
                </p>
                <details className="first-use-evidence" open>
                  <summary>
                    {t("原始依据 · {count} 段", {
                      count: detail.evidence.length.toLocaleString(language),
                    })}
                  </summary>
                  {detail.evidence.map((item) => (
                    <article key={item.evidence_id}>
                      <header>
                        <span>
                          {item.source_id
                            ? nameOf(item.source_id)
                            : t("未知来源")}
                        </span>
                        <span>
                          {item.written_by === "person"
                            ? t("你")
                            : item.written_by === "agent"
                              ? "Agent"
                              : t("作者未识别")}{" "}
                          ·{" "}
                          {item.created_at
                            ? new Date(item.created_at).toLocaleString(language)
                            : t("时间未知")}
                        </span>
                      </header>
                      <p>{item.verbatim}</p>
                    </article>
                  ))}
                </details>
              </>
            ) : (
              <p className="first-use-unavailable">
                {t(
                  "原文或当前授权不可用。换一条背景或刷新后再试，不会带出这条内容。",
                )}
              </p>
            )}
          </section>
          <section
            className="first-use-destination"
            aria-labelledby="guide-draft-title"
          >
            <h2 id="guide-draft-title">{t("下一段对话的开场白")}</h2>
            <label className="first-use-field">
              {t("在哪个应用试用")}
              <select
                value={host?.hostId ?? ""}
                onChange={(event) => {
                  changed();
                  setTargetId(event.target.value);
                  setRoute("context");
                }}
              >
                <option value="">{t("自行选择应用 · 仅复制")}</option>
                {destinations.map((item) => (
                  <option key={item.hostId} value={item.hostId}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            <p className="first-use-connection">
              {hostsError
                ? t("暂时无法读取连接状态，可重试或仅复制背景。")
                : !host
                  ? t("尚未选择已连接的应用。手动带入不等于安装连接。")
                  : canQuery
                    ? t("上次自检可达，本次是否用对仍需核对。")
                    : host.installed
                      ? t("已安装连接，尚未验证读取。")
                      : t("尚未安装读取连接。")}
            </p>
            <button
              type="button"
              className="quiet first-use-connection-link"
              onClick={onSources}
            >
              <Link2 size={15} aria-hidden="true" />
              {t("管理应用连接")}
            </button>
            <fieldset className="first-use-routes">
              <legend className="sr-only">{t("选择带入方式")}</legend>
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
                {t("带入背景")}
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
                {t("让应用查询")}
              </label>
            </fieldset>
            {effectiveRoute === "query" && (
              <label className="first-use-field">
                {t("要接续的主题")}
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
                ? t("只带查询，不把上方的预期答案塞进新会话。")
                : scene === "new"
                  ? t("仅带你选择的这一条，不自动生成个人画像。")
                  : t("完整带入这一条背景与引用；这不是自动召回测试。")}
            </p>
            <textarea
              className="first-use-draft"
              aria-label={t("开场白完整预览")}
              readOnly
              value={draft}
              placeholder={t("原始依据可用后显示开场白")}
              spellCheck={false}
            />
            <div className="first-use-actions">
              <button
                type="button"
                disabled={!draft || busy}
                onClick={() => void carry(false)}
              >
                <Copy size={16} aria-hidden="true" />
                {busy ? t("正在核对…") : t("复制开场白")}
              </button>
              {host?.handoff === "open" && (
                <button
                  type="button"
                  className="secondary"
                  disabled={!draft || busy}
                  onClick={() => void carry(true)}
                >
                  <ExternalLink size={16} aria-hidden="true" />
                  {t("复制并打开 {name}", { name: host.label })}
                </button>
              )}
            </div>
            {notice && (
              <p className="first-use-notice" role="status">
                {t(notice.key, { name: notice.name ?? "" })}
              </p>
            )}
            {copiedKey === contextKey && (
              <section className="first-use-result">
                <p>{t("尚无本次外部调用回执。新对话是否引用了正确的背景？")}</p>
                <div>
                  <button
                    type="button"
                    className="quiet"
                    onClick={() => setReported("ok")}
                  >
                    <Check size={15} />
                    {t("我已核对结果")}
                  </button>
                  <button
                    type="button"
                    className="quiet"
                    onClick={() => setReported("missing")}
                  >
                    {t("没有找回来")}
                  </button>
                </div>
                {reported && (
                  <p role="status">
                    {reported === "ok"
                      ? t("你的反馈：已核对。这不是系统自动验收记录。")
                      : t(
                          "你的反馈：没有找回来。先检查来源是否导入、记忆范围与应用连接。",
                        )}
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
  const t = useTranslate();
  return (
    <aside className="first-use-invitation" aria-label={t("上手体验邀请")}>
      <span>{t("用一条自己的记忆，试着接续下一次对话。")}</span>
      <button type="button" className="quiet" onClick={onOpen}>
        {t("试一次")}
        <ArrowRight size={15} aria-hidden="true" />
      </button>
      <button
        type="button"
        className="quiet"
        title={t("稍后再看")}
        aria-label={t("关闭上手邀请")}
        onClick={onDismiss}
      >
        <X size={15} />
      </button>
    </aside>
  );
}
