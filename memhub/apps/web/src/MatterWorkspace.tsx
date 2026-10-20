import type {
  Citation,
  GovernedMemory,
  Matter,
  MatterClaim,
  MatterContext,
  MatterEvidence,
  MatterRun,
  MemoryDetail,
  MemoryGraphNode,
  MemoryGraphResponse,
} from "@one-memory/contracts";
import {
  formatUiDate,
  formatUiNumber,
  type UiMessage,
} from "@one-memory/contracts";
import {
  ArrowUp,
  BookOpen,
  Check,
  ChevronRight,
  Copy,
  FileText,
  History,
  Link2,
  Network,
  PanelRightOpen,
  Pencil,
  RefreshCw,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  addMatterNote,
  discoverMatters,
  getMatterContext,
  getMatterEvidence,
  getMatterHistory,
  getMatterRuns,
  getMatters,
  getMemories,
  getMemory,
  getMemoryGraph,
  patchMatter,
  refreshMatter,
} from "./api";
import { MatterMemoryPanel } from "./MatterMemoryPanel";
import { MemoryGraph3D } from "./MemoryGraph3D";
import { projectMatterMap } from "./matter-map";
import { memoryWriteErrorText } from "./model-error-text";
import { getUiLanguage, useTranslate, useUiLanguage } from "./ui-language";
import { traceUiAction } from "./ui-trace";
import { isDefiniteRefusal } from "./write-outcome";
import "./matters.css";

const kinds: Record<MatterClaim["kind"], UiMessage> = {
  goal: "目标",
  decision: "已明确的决定",
  constraint: "约束与边界",
  proposal: "提案",
  progress: "进展记录",
  open_question: "尚待决定",
  experience: "经验与避坑",
};
const order: MatterClaim["kind"][] = [
  "goal",
  "decision",
  "constraint",
  "open_question",
  "progress",
  "experience",
  "proposal",
];
const authors: Record<string, UiMessage> = {
  human_statement: "用户陈述",
  agent_report: "Agent 自报",
  mixed: "交叉来源",
  unknown: "作者未确定",
};
const sources: Record<string, string> = {
  cursor: "Cursor",
  codex: "Codex",
  workbuddy: "WorkBuddy",
  "claude-code": "Claude Code",
  "one-memory-manual": "手动添加",
};
const runErrors: Record<string, string> = {
  matter_model_budget_exhausted: "模型项目预算已耗尽，需补充预算或更换模型。",
  matter_model_http_401: "模型鉴权失败，请检查模型配置。",
  matter_model_http_402: "模型服务要求检查余额或付款状态，原话已保留。",
  hy_memory_model_payment_required:
    "记忆模型服务要求检查余额或付款状态，原话已保留。",
  matter_sources_changed_during_generation:
    "整理期间来源发生变化，请重新整理。",
  matter_model_output_truncated: "模型输出超出预算，整理结果未保存。",
  matter_collection_incomplete: "读取范围超出本轮预算，尚未进入 HY 提取。",
  matter_import_paused: "来源导入已暂停，档案未更新。",
  matter_import_failed: "来源导入失败，档案未更新。",
  matter_import_incomplete: "来源扫描有未完成或读取错误，档案未更新。",
  matter_import_source_unavailable: "已授权来源不可用，档案未更新。",
  matter_import_source_revoked: "来源授权已撤销，档案未更新。",
  matter_synthesis_coverage_incomplete: "关键记忆未完整保留，档案未通过核验。",
  matter_organization_coverage_incomplete:
    "事项归属遗漏了关键记忆，档案未通过核验。",
  hy_memory_input_exceeds_window: "有单条消息超出 HY 处理窗口，尚未完整提取。",
  hy_memory_model_empty_response: "记忆模型连续返回空结果，本轮提取未完成。",
  hy_memory_model_output_truncated: "记忆模型输出不完整，本轮提取未完成。",
  hy_memory_model_request_failed: "记忆模型请求失败，本轮提取未完成。",
};
const time = (value: string) =>
  formatUiDate(getUiLanguage(), new Date(value), {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

type MatterRead<T> = { version: string | null; value: T };

export function MatterWorkspace({ onOpenSources }: { onOpenSources(): void }) {
  const t = useTranslate();
  const language = useUiLanguage();
  const [matters, setMatters] = useState<Matter[]>([]);
  const [runs, setRuns] = useState<MatterRun[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [mattersReadFailed, setMattersReadFailed] = useState(false);
  const [error, setError] = useState<UiMessage | "">("");
  const [busy, setBusy] = useState(false);
  const [search, setSearch] = useState("");
  const [tab, setTab] = useState<"map" | "wiki" | "history">("map");
  const [mapData, setMapData] = useState<{
    memories: GovernedMemory[];
    graph: MemoryGraphResponse | null;
  }>({ memories: [], graph: null });
  const [mapError, setMapError] = useState<UiMessage | "">("");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [memoryRead, setMemoryRead] = useState<MatterRead<MemoryDetail> | null>(
    null,
  );
  const [memoryLoading, setMemoryLoading] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const memoryRequest = useRef(0);
  const [historyRead, setHistoryRead] = useState<MatterRead<Matter[]> | null>(
    null,
  );
  const [evidenceRead, setEvidenceRead] =
    useState<MatterRead<MatterEvidence> | null>(null);
  const [citation, setCitation] = useState<Citation | null>(null);
  const [sourceLoading, setSourceLoading] = useState(false);
  const [note, setNote] = useState("");
  const [notice, setNotice] = useState<UiMessage | "">("");
  const [edit, setEdit] = useState(false);
  const [title, setTitle] = useState("");
  const [boundary, setBoundary] = useState("");
  const [context, setContext] = useState<MatterContext | null>(null);
  const [copied, setCopied] = useState(false);
  const evidenceRequest = useRef(0);
  const contextRequest = useRef(0);
  const mapRequest = useRef(0);
  const contextDialog = useRef<HTMLElement>(null);
  const loadSequence = useRef(0);
  const matter = matters.find((m) => m.matter_id === selected);
  const contentVersion = matter
    ? `${matter.matter_id}:${matter.version}:${matter.availability}`
    : null;
  const currentContentVersion = useRef(contentVersion);
  currentContentVersion.current = contentVersion;
  const memoryDetail =
    memoryRead?.version === contentVersion ? memoryRead.value : null;
  const history =
    historyRead?.version === contentVersion ? historyRead.value : [];
  const evidence =
    evidenceRead?.version === contentVersion ? evidenceRead.value : null;

  useEffect(() => {
    if (!context) return;
    const before = document.activeElement;
    const panel = contextDialog.current;
    panel?.querySelector<HTMLTextAreaElement>("textarea")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setContext(null);
      }
      if (event.key !== "Tab" || !panel) return;
      const controls = [
        ...panel.querySelectorAll<HTMLElement>(
          "button:not(:disabled), textarea",
        ),
      ];
      const first = controls[0];
      const last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (before instanceof HTMLElement) before.focus();
    };
  }, [context]);

  const load = useCallback(async () => {
    const request = ++loadSequence.current;
    try {
      const [items, jobs] = await Promise.allSettled([
        getMatters().then((items) => {
          if (request !== loadSequence.current) return;
          setMatters((current) =>
            JSON.stringify(current) === JSON.stringify(items) ? current : items,
          );
          setSelected((current) =>
            items.some((m) => m.matter_id === current)
              ? current
              : (items[0]?.matter_id ?? null),
          );
          setMattersReadFailed(false);
          setLoading(false);
        }),
        getMatterRuns().then((jobs) => {
          if (request === loadSequence.current) setRuns(jobs);
        }),
      ]);
      if (request !== loadSequence.current) return;
      setMattersReadFailed(items.status === "rejected");
      setError(
        items.status === "rejected"
          ? "未能读取事项档案。请检查本地服务后重试。"
          : jobs.status === "rejected"
            ? "整理进度暂时不可用，事项档案已刷新。"
            : "",
      );
    } catch {
      if (request === loadSequence.current)
        setError("未能读取事项档案。请检查本地服务后重试。");
    } finally {
      if (request === loadSequence.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  const loadMap = useCallback(async () => {
    const request = ++mapRequest.current;
    try {
      const [memories, graph] = await Promise.all([
        getMemories(),
        getMemoryGraph(),
      ]);
      if (request !== mapRequest.current) return;
      const next = { memories, graph };
      setMapData((current) =>
        JSON.stringify(current) === JSON.stringify(next) ? current : next,
      );
      setMapError("");
    } catch {
      if (request === mapRequest.current)
        setMapError("基础记忆暂时不可用，可重试读取。");
    }
  }, []);
  useEffect(() => {
    if (matters.length) void loadMap();
    else setMapData({ memories: [], graph: null });
    return () => {
      mapRequest.current += 1;
    };
  }, [loadMap, matters]);
  const running = runs.find((r) => r.status === "running");
  const hasRunning = Boolean(running);
  useEffect(() => {
    let cancelled = false;
    let timer: number;
    const refresh = async () => {
      if (!document.hidden) await load();
      if (!cancelled)
        timer = window.setTimeout(refresh, hasRunning ? 2500 : 5000);
    };
    const visible = () => {
      if (!document.hidden) void load();
    };
    timer = window.setTimeout(refresh, hasRunning ? 2500 : 5000);
    document.addEventListener("visibilitychange", visible);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [hasRunning, load]);
  const projection = useMemo(
    () => projectMatterMap(matter, mapData.memories, mapData.graph),
    [matter, mapData],
  );
  useEffect(() => {
    if (
      selectedNodeId &&
      !projection.graph.nodes.some((node) => node.node_id === selectedNodeId)
    ) {
      memoryRequest.current += 1;
      setSelectedNodeId(null);
      setMemoryRead(null);
      setMemoryLoading(false);
    }
  }, [projection, selectedNodeId]);
  const seenContentVersion = useRef(contentVersion);
  useEffect(() => {
    if (seenContentVersion.current === contentVersion) return;
    seenContentVersion.current = contentVersion;
    // Every derived panel belongs to the same version, not just its source excerpt.
    evidenceRequest.current += 1;
    memoryRequest.current += 1;
    contextRequest.current += 1;
    setCitation(null);
    setEvidenceRead(null);
    setSourceLoading(false);
    setMemoryRead(null);
    setMemoryLoading(false);
    setContext(null);
    setCopied(false);
    setHistoryRead(null);
    setEdit(false);
    setTitle("");
    setBoundary("");
  }, [contentVersion]);
  const previousSelected = useRef(selected);
  useEffect(() => {
    if (previousSelected.current !== selected) {
      previousSelected.current = selected;
      evidenceRequest.current += 1;
      setEvidenceRead(null);
      setCitation(null);
      setEdit(false);
      setContext(null);
      setTab("map");
      memoryRequest.current += 1;
      setSelectedNodeId(null);
      setMemoryRead(null);
      setMemoryLoading(false);
      setNote("");
      setNotice("");
    }
  }, [selected]);
  useEffect(() => {
    if (
      tab !== "history" ||
      !matter ||
      !["ready", "needs_update"].includes(matter.availability)
    )
      return;
    let cancelled = false;
    setHistoryRead(null);
    getMatterHistory(matter.matter_id)
      .then((items) => {
        if (!cancelled && contentVersion === currentContentVersion.current)
          setHistoryRead({ version: contentVersion, value: items });
      })
      .catch(() => {
        if (!cancelled) setError("版本记录读取失败。");
      });
    return () => {
      cancelled = true;
    };
  }, [tab, matter, contentVersion]);

  async function openContext() {
    if (matter?.availability !== "ready") return;
    const request = ++contextRequest.current;
    const result = await getMatterContext(matter.matter_id);
    if (
      request !== contextRequest.current ||
      contentVersion !== currentContentVersion.current
    )
      return;
    if (
      result.matter_id !== matter.matter_id ||
      result.version !== matter.version ||
      result.availability !== "ready"
    ) {
      throw new Error("matter_context_changed");
    }
    setContext(result);
    setCopied(false);
  }

  async function action(
    operation: (traceId?: string) => Promise<unknown>,
    mode: "read" | "write" = "write",
  ) {
    setBusy(true);
    setError("");
    setNotice("");
    const trace = mode === "write" ? traceUiAction("matters", "submit") : null;
    try {
      await operation(trace?.traceId);
      await load();
      trace?.done("succeeded");
    } catch (reason) {
      setError("操作未完成。资料或版本可能已变化，请刷新后重试。");
      trace?.done(isDefiniteRefusal(reason) ? "failed" : "unknown", {
        reason: isDefiniteRefusal(reason)
          ? "matter_write_failed"
          : "matter_write_result_unknown",
      });
    } finally {
      setBusy(false);
    }
  }
  async function openEvidence(cite: Citation, focusMemory = true) {
    if (!matter) return;
    const request = ++evidenceRequest.current;
    setCitation(cite);
    setEvidenceRead(null);
    setSourceLoading(true);
    setInspectorOpen(true);
    memoryRequest.current += 1;
    setMemoryLoading(false);
    setMemoryRead(null);
    const referenced = matter.claims.find((claim) =>
      claim.citations.some((item) => item.evidence_id === cite.evidence_id),
    )?.memory_refs[0];
    const node = projection.graph.nodes.find(
      (item) => item.memory_id === referenced?.memory_id,
    );
    if (node && focusMemory) setSelectedNodeId(node.node_id);
    try {
      const result = await getMatterEvidence(
        matter.matter_id,
        cite.evidence_id,
      );
      if (
        request === evidenceRequest.current &&
        contentVersion === currentContentVersion.current
      )
        setEvidenceRead({ version: contentVersion, value: result });
    } catch {
      if (request === evidenceRequest.current)
        setError("原始证据已变化或不可用。");
    } finally {
      if (request === evidenceRequest.current) setSourceLoading(false);
    }
  }
  async function selectMemory(memoryId: string) {
    const node = projection.graph.nodes.find(
      (item) => item.memory_id === memoryId,
    );
    const expected = projection.memories.find(
      (memory) => memory.memory_id === memoryId,
    );
    if (!node || !expected) return;
    setSelectedNodeId(node.node_id);
    evidenceRequest.current += 1;
    setCitation(null);
    setEvidenceRead(null);
    setSourceLoading(false);
    const request = ++memoryRequest.current;
    setMemoryLoading(true);
    setInspectorOpen(true);
    setMemoryRead(null);
    try {
      const detail = await getMemory(memoryId);
      if (
        request !== memoryRequest.current ||
        contentVersion !== currentContentVersion.current
      )
        return;
      if (
        detail.memory.memory_id !== memoryId ||
        detail.memory.revision !== expected.revision ||
        detail.memory.status !== expected.status
      ) {
        throw new Error("matter_memory_changed");
      }
      setMemoryRead({ version: contentVersion, value: detail });
    } catch {
      if (request === memoryRequest.current)
        setMapError("这条基础记忆已变化或不可读取。");
    } finally {
      if (request === memoryRequest.current) setMemoryLoading(false);
    }
  }
  function selectNode(node: MemoryGraphNode) {
    setSelectedNodeId(node.node_id);
    if (node.memory_id) void selectMemory(node.memory_id);
    else if (node.source_id) {
      const cite = matter?.claims
        .flatMap((claim) => claim.citations)
        .find((item) => item.source_id === node.source_id);
      if (cite) void openEvidence(cite, false);
    }
  }
  const filtered = matters.filter((m) =>
    `${m.title} ${m.tags.join(" ")}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );

  return (
    <section className={`matter-workspace matter-view-${tab}`}>
      <header className="matter-topbar">
        <div>
          <p className="matter-brand">MemHub</p>
          <h1>{t("工作台")}</h1>
        </div>
        <div className="matter-top-actions">
          <span className="local-state">
            <ShieldCheck size={14} /> {t("本地资料库")}
          </span>
          <button type="button" className="secondary" onClick={onOpenSources}>
            <Link2 size={15} /> {t("来源与连接")}
          </button>
          <button
            type="button"
            disabled={busy || !!running}
            onClick={() => void action((traceId) => discoverMatters(traceId))}
          >
            <RefreshCw size={15} /> {t("整理历史")}
          </button>
        </div>
      </header>
      {error && (
        <div className="matter-error" role="alert">
          {t(error)}
          <button type="button" className="quiet" onClick={() => void load()}>
            {t("重试")}
          </button>
        </div>
      )}
      {notice && (
        <p className="matter-notice" role="status">
          {t(notice)}
        </p>
      )}
      {running && (
        <section className="matter-progress" aria-live="polite">
          <div>
            <strong>{running.phase}</strong>
            <span>
              {t("基础记忆 {completed} / {total} 批 · {seconds} 秒", {
                completed: running.completed_batches,
                total: running.total_batches || "…",
                seconds: Math.round(running.elapsed_seconds),
              })}
            </span>
          </div>
          <progress
            max={running.total_batches || 1}
            value={running.completed_batches}
          />
          {running.observations.map((item) => (
            <p key={item}>{item}</p>
          ))}
        </section>
      )}
      {!running && runs[0]?.status === "failed" && (
        <div className="matter-error" role="status">
          {t("上次整理未完成 · ")}
          {memoryWriteErrorText(runs[0].error) ??
            runErrors[runs[0].error ?? ""] ??
            t("模型结果未通过校验，请重试。")}
        </div>
      )}
      {!running && runs[0] && (
        <div className="matter-run-receipt" role="status">
          <span>
            {runs[0].coverage_complete
              ? t("本轮读取完整")
              : t("本轮读取未完整")}
          </span>
          <span>
            {t("已读取 {count} 条", { count: runs[0].selected_records })}
          </span>
          <span>
            {t("HY 输入 {count} 条", { count: runs[0].hy_input_records ?? 0 })}
          </span>
          {runs[0].omitted_records > 0 && (
            <span>
              {t("{count} 条未处理", { count: runs[0].omitted_records })}
            </span>
          )}
          {runs[0].status === "completed" && <span>{t("档案待核对")}</span>}
          {runs[0].failed_phase && (
            <span>{t("未通过：{phase}", { phase: runs[0].failed_phase })}</span>
          )}
        </div>
      )}
      <div className="matter-columns">
        <aside className="matter-sidebar" aria-label={t("事项列表")}>
          <div className="matter-sidebar-head">
            <span>{t("我的事项")}</span>
            <span>
              {loading
                ? t("核对中")
                : mattersReadFailed
                  ? t("待核对")
                  : formatUiNumber(language, matters.length)}
            </span>
          </div>
          <label className="matter-search">
            <Search size={14} />
            <input
              aria-label={t("搜索事项")}
              placeholder={t("搜索事项")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
          {loading && (
            <div
              className="matter-skeleton"
              role="status"
              aria-label={t("正在读取事项")}
            />
          )}
          {filtered.map((item) => (
            <button
              type="button"
              key={item.matter_id}
              className={`matter-item ${selected === item.matter_id ? "selected" : ""}`}
              aria-pressed={selected === item.matter_id}
              onClick={() => setSelected(item.matter_id)}
            >
              <BookOpen size={17} />
              <span>
                <strong>{item.title}</strong>
                <small>
                  {t("{claims} 个记忆点 · {sources} 个来源", {
                    claims: item.claims.length,
                    sources: item.source_count,
                  })}
                </small>
                <em>{item.scope_confirmed ? t("范围已确认") : t("待校准")}</em>
              </span>
            </button>
          ))}
          {!!search && !filtered.length && (
            <p className="matter-muted">
              {mattersReadFailed
                ? t("事项读取失败，保留待核对状态。")
                : t("没有匹配的事项")}
            </p>
          )}
          <footer className="matter-sidebar-footer">
            {runs[0] ? (
              <>
                <span>
                  {t("{count} 条消息参与本轮", {
                    count: runs[0].selected_records,
                  })}
                </span>
                <span>
                  {t("{count} 条不在本轮范围", {
                    count: formatUiNumber(language, runs[0].omitted_records),
                  })}
                </span>
              </>
            ) : (
              <span>
                {loading
                  ? t("正在读取事项")
                  : mattersReadFailed
                    ? t("事项读取失败，当前为待核对状态")
                    : t("仅显示已生成的档案")}
              </span>
            )}
          </footer>
        </aside>
        <article className="matter-document">
          {!matter && !loading && !error && (
            <div className="matter-empty">
              <BookOpen size={32} />
              <h2>{t("尚未整理事项")}</h2>
              <button
                type="button"
                disabled={busy || !!running}
                onClick={() =>
                  void action((traceId) => discoverMatters(traceId))
                }
              >
                {t("从授权历史整理")}
              </button>
            </div>
          )}
          {matter && (
            <>
              <div className="matter-breadcrumb">
                {t("我的事项")} <ChevronRight size={13} />{" "}
                <span>{t("档案 v{version}", { version: matter.version })}</span>
              </div>
              <header className="matter-heading">
                <div>
                  <h2>{matter.title}</h2>
                  <p>{matter.purpose}</p>
                </div>
                <button
                  type="button"
                  className="quiet icon-button"
                  title={t("编辑事项范围")}
                  aria-label={t("编辑事项范围")}
                  disabled={
                    busy ||
                    !["ready", "needs_update"].includes(matter.availability)
                  }
                  onClick={() => {
                    setEdit(true);
                    setTitle(matter.title);
                    setBoundary(matter.boundary);
                  }}
                >
                  <Pencil size={16} />
                </button>
              </header>
              <div className="matter-metadata">
                <span>{t("内容待核对")}</span>
                <span>
                  {matter.source_engine === "hy-memory"
                    ? t("HY Memory 提取")
                    : t("历史探索结果 · 未经 HY")}
                </span>
                <span>
                  <ShieldCheck size={13} />{" "}
                  {t("{count} 条证据", {
                    count: formatUiNumber(language, matter.evidence_count),
                  })}
                </span>
                <span>
                  {t("{time} 更新", { time: time(matter.updated_at) })}
                </span>
                <span>{matter.model}</span>
              </div>
              <label className="matter-agent-access" hidden={tab === "map"}>
                <input
                  type="checkbox"
                  checked={matter.agent_access}
                  disabled={
                    busy ||
                    matter.availability !== "ready" ||
                    matter.source_engine !== "hy-memory"
                  }
                  onChange={(e) =>
                    void action((traceId) =>
                      patchMatter(
                        matter.matter_id,
                        {
                          expected_version: matter.version,
                          agent_access: e.target.checked,
                        },
                        traceId,
                      ),
                    )
                  }
                />
                {t("允许已连接 Agent 读取此事项")}
              </label>
              {matter.availability !== "ready" && (
                <div className="matter-update-warning">
                  {matter.availability === "needs_update"
                    ? t("新资料待整理，旧结论暂不提供给 Agent。")
                    : t("来源或基础记忆已变化，旧档案已隐藏。")}
                  <button
                    type="button"
                    className="quiet"
                    disabled={busy || !!running}
                    onClick={() =>
                      void action((traceId) =>
                        refreshMatter(matter.matter_id, traceId),
                      )
                    }
                  >
                    <RefreshCw size={14} /> {t("更新档案")}
                  </button>
                </div>
              )}
              {!matter.scope_confirmed && matter.availability === "ready" && (
                <div className="matter-calibration">
                  <div>
                    <strong>{t("最近是在推进这件事吗？")}</strong>
                    <p>{matter.boundary}</p>
                  </div>
                  <button
                    type="button"
                    className="secondary"
                    disabled={busy}
                    onClick={() =>
                      void action((traceId) =>
                        patchMatter(
                          matter.matter_id,
                          {
                            expected_version: matter.version,
                            scope_confirmed: true,
                          },
                          traceId,
                        ),
                      )
                    }
                  >
                    <Check size={14} /> {t("确认事项范围")}
                  </button>
                </div>
              )}
              {edit && (
                <form
                  className="matter-edit"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void action(async (traceId) => {
                      await patchMatter(
                        matter.matter_id,
                        {
                          expected_version: matter.version,
                          title,
                          boundary,
                        },
                        traceId,
                      );
                      setEdit(false);
                    });
                  }}
                >
                  <label>
                    {t("事项名称")}
                    <input
                      value={title}
                      maxLength={100}
                      minLength={2}
                      onChange={(e) => setTitle(e.target.value)}
                      required
                    />
                  </label>
                  <label>
                    {t("事项范围")}
                    <textarea
                      value={boundary}
                      maxLength={800}
                      onChange={(e) => setBoundary(e.target.value)}
                      required
                    />
                  </label>
                  <div>
                    <button type="submit" disabled={busy}>
                      {t("保存")}
                    </button>
                    <button
                      type="button"
                      className="quiet"
                      onClick={() => setEdit(false)}
                    >
                      {t("取消")}
                    </button>
                  </div>
                </form>
              )}
              <div className="matter-tabs">
                <div role="tablist" aria-label={t("档案视图")}>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={tab === "map"}
                    onClick={() => setTab("map")}
                  >
                    <Network size={14} /> {t("记忆地图")}
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={tab === "wiki"}
                    onClick={() => setTab("wiki")}
                  >
                    <FileText size={14} /> {t("知识档案")}
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={tab === "history"}
                    onClick={() => setTab("history")}
                  >
                    <History size={14} /> {t("版本记录")}
                  </button>
                </div>
                <button
                  type="button"
                  className="quiet"
                  disabled={busy || matter.availability !== "ready"}
                  onClick={() => void action(openContext, "read")}
                >
                  <Copy size={14} /> {t("任务上下文")}
                </button>
                <button
                  type="button"
                  className="quiet icon-button"
                  title={t("基础记忆与原文")}
                  aria-label={t("打开记忆检查器")}
                  onClick={() => setInspectorOpen(true)}
                >
                  <PanelRightOpen size={16} />
                </button>
              </div>
              <div className="matter-map-stage" hidden={tab !== "map"}>
                {mapError && (
                  <p className="matter-error" role="status">
                    {t(mapError)}{" "}
                    <button
                      type="button"
                      className="quiet"
                      onClick={() => void loadMap()}
                    >
                      {t("重试读取")}
                    </button>
                  </p>
                )}
                {projection.graph.nodes.length ? (
                  <>
                    <MemoryGraph3D
                      graph={projection.graph}
                      selectedNodeId={selectedNodeId}
                      onSelectNode={selectNode}
                    />
                    <fieldset
                      className="memory-map-legend matter-map-legend"
                      aria-label={t("图谱图例")}
                    >
                      <span data-kind="memory">{t("基础记忆")}</span>
                      <span data-kind="source">{t("来源")}</span>
                      <span data-kind="schema">{t("模式")}</span>
                      <span data-kind="topic">{t("主题")}</span>
                      <span>
                        {t("{memories} 条记忆 · {links} 条关联", {
                          memories: formatUiNumber(
                            language,
                            projection.memories.length,
                          ),
                          links: formatUiNumber(
                            language,
                            projection.graph.edges.length,
                          ),
                        })}
                      </span>
                    </fieldset>
                  </>
                ) : (
                  <p className="matter-map-empty">
                    {matter.source_engine === "legacy_direct"
                      ? t("历史探索结果没有 HY 基础记忆，可在知识档案中查看。")
                      : t("暂无可展示的基础记忆关联。")}
                  </p>
                )}
              </div>
              {tab === "wiki" ? (
                <div className="matter-wiki">
                  {order.map((kind) => {
                    const claims = matter.claims.filter((c) => c.kind === kind);
                    return claims.length ? (
                      <section className="matter-claim-section" key={kind}>
                        <h3>
                          <span className={`kind-dot ${kind}`} />
                          {t(kinds[kind])}
                          <small>{claims.length}</small>
                        </h3>
                        {claims.map((claim, index) => (
                          <div
                            className={`matter-claim ${claim.state ?? "current"}`}
                            key={`${kind}-${claim.statement}`}
                          >
                            <p>{claim.statement}</p>
                            {claim.change_note && (
                              <p className="claim-change">
                                {claim.change_note}
                              </p>
                            )}
                            <div className="claim-meta">
                              <span>
                                {t(authors[claim.basis] ?? "作者未确定")}
                              </span>
                              {claim.state && claim.state !== "current" && (
                                <span>
                                  {t(
                                    (
                                      {
                                        historical: "历史记录",
                                        superseded: "已被替代",
                                        unresolved: "待核对",
                                      } as const
                                    )[claim.state],
                                  )}
                                </span>
                              )}
                              {claim.scope && <span>{claim.scope}</span>}
                              {claim.citations.map((cite, i) => (
                                <button
                                  type="button"
                                  className="citation-link"
                                  key={`${cite.evidence_id}-${cite.quote}`}
                                  aria-label={t("查看原话 {name} {index}", {
                                    name:
                                      sources[cite.source_id] ?? cite.source_id,
                                    index: `${index + 1}-${i + 1}`,
                                  })}
                                  onClick={() => void openEvidence(cite)}
                                >
                                  <Link2 size={12} />
                                  {sources[cite.source_id] ?? cite.source_id}
                                </button>
                              ))}
                            </div>
                          </div>
                        ))}
                      </section>
                    ) : null;
                  })}
                  {!!matter.uncertainties.length && (
                    <section className="matter-uncertainties">
                      <h3>{t("信息缺口与待核对")}</h3>
                      <ul>
                        {matter.uncertainties.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </section>
                  )}
                </div>
              ) : tab === "history" ? (
                <ol className="matter-history">
                  {history.map((item) => (
                    <li key={item.version}>
                      <span>v{item.version}</span>
                      <div>
                        <strong>{item.title}</strong>
                        <p>
                          {time(item.updated_at)} ·{" "}
                          {t("{count} 个记忆点", { count: item.claims.length })}{" "}
                          ·{" "}
                          {item.scope_confirmed ? t("范围已确认") : t("未校准")}
                        </p>
                      </div>
                    </li>
                  ))}
                </ol>
              ) : null}
              <form
                className="matter-composer"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!note.trim()) return;
                  void action(async (traceId) => {
                    await addMatterNote(
                      matter.matter_id,
                      note.trim(),
                      matter.version,
                      traceId,
                    );
                    setNote("");
                    setNotice(
                      "补充已保存为用户原话，更新档案后恢复上下文调用。",
                    );
                  });
                }}
              >
                <label htmlFor="matter-note">{t("补充或更正")}</label>
                <div>
                  <textarea
                    id="matter-note"
                    placeholder={t("关于这件事，还有…")}
                    value={note}
                    maxLength={20000}
                    onChange={(e) => setNote(e.target.value)}
                  />
                  <button
                    type="submit"
                    className="icon-button"
                    title={t("保存补充")}
                    aria-label={t("保存补充")}
                    disabled={
                      busy ||
                      !note.trim() ||
                      !["ready", "needs_update"].includes(matter.availability)
                    }
                  >
                    <ArrowUp size={18} />
                  </button>
                </div>
              </form>
            </>
          )}
        </article>
        {inspectorOpen && (
          <button
            type="button"
            className="matter-inspector-backdrop"
            aria-label={t("收起记忆检查器")}
            onClick={() => setInspectorOpen(false)}
          />
        )}
        <aside
          className={`matter-evidence ${inspectorOpen ? "is-open" : ""}`}
          aria-label={t("原始证据")}
          onKeyDown={(event) => {
            if (event.key === "Escape") setInspectorOpen(false);
          }}
        >
          <header className="matter-inspector-heading">
            <h3>{t("记忆与原文")}</h3>
            <button
              type="button"
              className="quiet icon-button"
              title={t("关闭检查器")}
              aria-label={t("关闭记忆检查器")}
              onClick={() => setInspectorOpen(false)}
            >
              <X size={16} />
            </button>
          </header>
          <MatterMemoryPanel
            memories={projection.memories}
            selectedMemoryId={
              projection.graph.nodes.find(
                (node) => node.node_id === selectedNodeId,
              )?.memory_id ?? null
            }
            detail={memoryDetail}
            loading={memoryLoading}
            onSelect={(id) => void selectMemory(id)}
          />
          <header>
            <h3>{t("原始证据")}</h3>
            {citation && (
              <button
                type="button"
                className="quiet icon-button"
                title={t("关闭原话")}
                aria-label={t("关闭原话")}
                onClick={() => {
                  evidenceRequest.current += 1;
                  setCitation(null);
                  setEvidenceRead(null);
                }}
              >
                <X size={15} />
              </button>
            )}
          </header>
          {sourceLoading ? (
            <div
              className="matter-skeleton"
              role="status"
              aria-label={t("正在读取原文")}
            />
          ) : evidence ? (
            <>
              <div className="evidence-source">
                <span className="source-monogram">
                  {(sources[evidence.source_id] ?? evidence.source_id).slice(
                    0,
                    1,
                  )}
                </span>
                <div>
                  <strong>
                    {sources[evidence.source_id] ?? evidence.source_id}
                  </strong>
                  <small>
                    {evidence.written_by === "person"
                      ? t("用户原话")
                      : t("Agent 发言")}
                  </small>
                </div>
              </div>
              <p className="evidence-time">
                {evidence.created_at
                  ? time(evidence.created_at)
                  : t("消息时间未知")}
              </p>
              <p className="evidence-time">
                {t("观测于 {time}", { time: time(evidence.observed_at) })}
              </p>
              <div className="evidence-verbatim">
                {highlight(evidence.text, citation?.quote ?? "")}
              </div>
              <p className="evidence-id">
                {t("证据 {id}", { id: evidence.evidence_id })}
              </p>
            </>
          ) : !memoryDetail && !memoryLoading ? (
            <div className="evidence-empty">
              <Link2 size={25} />
              <p>{t("暂无选中的原话")}</p>
            </div>
          ) : null}
        </aside>
      </div>
      {context &&
        matter?.availability === "ready" &&
        context.matter_id === matter.matter_id &&
        context.version === matter.version && (
          <div className="matter-modal-backdrop">
            <section
              className="matter-context-modal"
              ref={contextDialog}
              role="dialog"
              aria-modal="true"
              aria-labelledby="context-title"
            >
              <header>
                <h2 id="context-title">
                  {t("任务上下文 · v{version}", { version: context.version })}
                </h2>
                <button
                  type="button"
                  className="quiet icon-button"
                  title={t("关闭上下文")}
                  aria-label={t("关闭上下文")}
                  onClick={() => setContext(null)}
                >
                  <X size={18} />
                </button>
              </header>
              <textarea
                aria-label={t("任务上下文内容")}
                readOnly
                value={context.text}
              />
              <footer>
                <span>{t("历史参考 · 保留来源与核验状态")}</span>
                <button
                  type="button"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(context.text);
                      setCopied(true);
                    } catch {
                      setError("复制失败，可在上下文文本框中选择内容。");
                    }
                  }}
                >
                  <Copy size={14} /> {copied ? t("已复制") : t("复制上下文")}
                </button>
              </footer>
            </section>
          </div>
        )}
    </section>
  );
}

function highlight(text: string, quote: string) {
  const index = quote ? text.indexOf(quote) : -1;
  if (index < 0) return text;
  return (
    <>
      {text.slice(0, index)}
      <mark>{quote}</mark>
      {text.slice(index + quote.length)}
    </>
  );
}
