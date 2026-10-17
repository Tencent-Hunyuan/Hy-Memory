import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getModelUsage,
  type ModelUsageMinute,
  type ModelUsageReport,
} from "./api";

const RANGES = [
  { minutes: 15, label: "最近 15 分钟" },
  { minutes: 60, label: "最近 1 小时" },
  { minutes: 360, label: "最近 6 小时" },
  { minutes: 1_440, label: "最近 24 小时" },
  { minutes: 10_080, label: "最近 7 天" },
];

const STAGE_LABELS: Record<string, string> = {
  coding_judge: "Judge",
  extract: "Extract",
  reconcile: "Reconcile",
  coding_extract: "Coding Extract",
  coding_reconcile: "Coding Reconcile",
  coding_fact_reconcile: "Fact Reconcile",
  coding_fact_summary: "Fact Summary",
  other: "Other",
};

const COLORS = [
  "#39759b",
  "#b5643b",
  "#538665",
  "#8a65a3",
  "#b08b38",
  "#437f82",
  "#a04f68",
  "#6e7781",
];

interface ModelUsageViewProps {
  live: boolean;
  refreshToken: number;
}

export function ModelUsageView({ live, refreshToken }: ModelUsageViewProps) {
  const [rangeMinutes, setRangeMinutes] = useState(60);
  const [report, setReport] = useState<ModelUsageReport | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");

  const load = useCallback(async () => {
    setState((current) => (current === "ready" ? current : "loading"));
    try {
      const since = new Date(Date.now() - rangeMinutes * 60_000).toISOString();
      setReport(await getModelUsage(since));
      setState("ready");
    } catch {
      setState("error");
    }
  }, [rangeMinutes]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: refreshToken is the explicit refresh trigger.
  useEffect(() => {
    void load();
  }, [load, refreshToken]);

  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => void load(), 5_000);
    return () => window.clearInterval(timer);
  }, [live, load]);

  const stages = useMemo(
    () => summarizeStages(report?.by_minute ?? []),
    [report],
  );

  return (
    <div className="model-usage-view">
      <div className="model-usage-toolbar">
        <label>
          <span>时间范围</span>
          <select
            aria-label="模型用量时间范围"
            value={rangeMinutes}
            onChange={(event) => setRangeMinutes(Number(event.target.value))}
          >
            {RANGES.map((range) => (
              <option key={range.minutes} value={range.minutes}>
                {range.label}
              </option>
            ))}
          </select>
        </label>
        <span className={`model-usage-state state-${state}`}>
          {state === "loading"
            ? "正在读取…"
            : state === "error"
              ? "读取失败"
              : live
                ? "每 5 秒刷新"
                : "已暂停"}
        </span>
      </div>

      <div className="model-usage-metrics">
        <Metric
          label="总 Tokens"
          value={formatTokens(report?.summary.total_tokens)}
        />
        <Metric
          label="Avg Prompt"
          value={formatTokens(report?.summary.avg_prompt_tokens)}
        />
        <Metric
          label="Avg Completion"
          value={formatTokens(report?.summary.avg_completion_tokens)}
        />
        <Metric
          label="请求数"
          value={String(report?.summary.request_count ?? 0)}
        />
      </div>

      <section
        className="model-usage-chart-card"
        aria-label="各 Prompt 每分钟 Token 曲线"
      >
        <div className="model-usage-section-title">
          <strong>各 Prompt 花费曲线</strong>
          <span>每分钟 · Total Tokens</span>
        </div>
        <UsageChart rows={report?.by_minute ?? []} />
      </section>

      <section className="model-usage-stage-card">
        <div className="model-usage-section-title">
          <strong>Prompt 明细</strong>
          <span>当前时间范围</span>
        </div>
        <div className="model-usage-stage-list">
          {stages.length ? (
            stages.map((stage) => (
              <div className="model-usage-stage-row" key={stage.name}>
                <i style={{ background: stageColor(stage.name) }} />
                <strong>{stageLabel(stage.name)}</strong>
                <span>{stage.requests} 次</span>
                <span>Prompt {formatTokens(stage.prompt)}</span>
                <span>Completion {formatTokens(stage.completion)}</span>
                <b>{formatTokens(stage.total)}</b>
              </div>
            ))
          ) : (
            <p className="debug-log-empty">这个时间范围内没有沉淀模型调用</p>
          )}
        </div>
      </section>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function UsageChart({ rows }: { rows: ModelUsageMinute[] }) {
  const minutes = [...new Set(rows.map((row) => row.minute))].sort();
  const stages = [...new Set(rows.map((row) => row.prompt_stage))].sort();
  const width = 640;
  const height = 190;
  const pad = { left: 42, right: 12, top: 14, bottom: 28 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const lookup = new Map(
    rows.map((row) => [
      `${row.minute}\u0000${row.prompt_stage}`,
      row.total_tokens,
    ]),
  );
  const max = Math.max(1, ...rows.map((row) => row.total_tokens));
  const x = (index: number) =>
    pad.left +
    (minutes.length <= 1
      ? plotWidth / 2
      : (index / (minutes.length - 1)) * plotWidth);
  const y = (value: number) =>
    pad.top + plotHeight - (value / max) * plotHeight;

  if (!rows.length)
    return <div className="model-usage-chart-empty">暂无曲线数据</div>;

  return (
    <>
      <svg
        className="model-usage-chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="按分钟显示各 Prompt 的 Token 花费"
      >
        {[0, 0.5, 1].map((ratio) => (
          <g key={ratio}>
            <line
              x1={pad.left}
              x2={width - pad.right}
              y1={y(max * ratio)}
              y2={y(max * ratio)}
            />
            <text x={pad.left - 7} y={y(max * ratio) + 3}>
              {formatTokens(max * ratio)}
            </text>
          </g>
        ))}
        {stages.map((stage) => {
          const points = minutes.map((minute, index) => ({
            minute,
            value: lookup.get(`${minute}\u0000${stage}`) ?? 0,
            x: x(index),
          }));
          return (
            <g key={stage}>
              <polyline
                points={points
                  .map((point) => `${point.x},${y(point.value)}`)
                  .join(" ")}
                stroke={stageColor(stage)}
              />
              {points.map((point) => (
                <circle
                  key={point.minute}
                  cx={point.x}
                  cy={y(point.value)}
                  r="2.4"
                  fill={stageColor(stage)}
                >
                  <title>{`${stageLabel(stage)} · ${timeLabel(point.minute)} · ${formatTokens(point.value)}`}</title>
                </circle>
              ))}
            </g>
          );
        })}
        <text x={pad.left} y={height - 7}>
          {timeLabel(minutes[0])}
        </text>
        <text x={width - pad.right} y={height - 7} textAnchor="end">
          {timeLabel(minutes.at(-1) ?? "")}
        </text>
      </svg>
      <div className="model-usage-legend">
        {stages.map((stage) => (
          <span key={stage}>
            <i style={{ background: stageColor(stage) }} />
            {stageLabel(stage)}
          </span>
        ))}
      </div>
    </>
  );
}

function summarizeStages(rows: ModelUsageMinute[]) {
  const stages = new Map<
    string,
    {
      name: string;
      requests: number;
      prompt: number;
      completion: number;
      total: number;
    }
  >();
  for (const row of rows) {
    const stage = stages.get(row.prompt_stage) ?? {
      name: row.prompt_stage,
      requests: 0,
      prompt: 0,
      completion: 0,
      total: 0,
    };
    stage.requests += row.request_count;
    stage.prompt += row.prompt_tokens;
    stage.completion += row.completion_tokens;
    stage.total += row.total_tokens;
    stages.set(row.prompt_stage, stage);
  }
  return [...stages.values()].sort((left, right) => right.total - left.total);
}

function stageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? stage.replaceAll("_", " ");
}

function stageColor(stage: string): string {
  const known = Object.keys(STAGE_LABELS).indexOf(stage);
  if (known >= 0) return COLORS[known % COLORS.length];
  let hash = 0;
  for (const character of stage)
    hash = (hash * 31 + character.charCodeAt(0)) | 0;
  return COLORS[Math.abs(hash) % COLORS.length];
}

function formatTokens(value: number | undefined): string {
  const amount = value ?? 0;
  if (amount >= 1_000_000) return `${(amount / 1_000_000).toFixed(2)}M`;
  if (amount >= 1_000) return `${(amount / 1_000).toFixed(1)}K`;
  return Math.round(amount).toLocaleString("zh-CN");
}

function timeLabel(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}
