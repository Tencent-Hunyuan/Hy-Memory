// @vitest-environment jsdom
import type { DailyMemoryUsage, ModelProfile } from "@one-memory/contracts";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { costLabel, MemoryUsagePanel } from "./MemoryUsagePanel";

const api = vi.hoisted(() => ({
  report: vi.fn(),
  profiles: vi.fn(),
  settings: vi.fn(),
  save: vi.fn(),
  balance: vi.fn(async (id: string) => ({
    schema_version: 1,
    profile_id: id,
    state: "unsupported",
    balances: [],
    is_available: null,
    checked_at: null,
    source_url: null,
  })),
}));
vi.mock("./api", () => ({
  getDailyModelUsage: api.report,
  getModelProfiles: api.profiles,
  getModelUsagePricing: api.settings,
  setModelUsagePricing: api.save,
  getProviderBalance: api.balance,
}));
const totals = {
  request_count: 3,
  succeeded: 2,
  failed: 1,
  in_flight: 0,
  prompt_tokens: 1000,
  completion_tokens: 200,
  total_tokens: 1200,
  cached_prompt_tokens: 800,
  reasoning_tokens: 20,
  unknown_usage_requests: 1,
  unpriced_requests: 1,
  costs: [
    {
      currency: "CNY",
      estimated_min: "0.0012",
      estimated_max: "0.0024",
      priced_requests: 2,
    },
  ],
};
const report: DailyMemoryUsage = {
  schema_version: 1,
  timezone: "Asia/Shanghai",
  recorded_from: "2026-11-04T00:00:00Z",
  generated_at: "2026-11-04T06:00:00Z",
  pricing_version: "test",
  pricing_source: "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
  summary: totals,
  days: [
    { date: "2026-11-04", coverage: "partial", ...totals },
    { date: "2026-11-03", coverage: "unrecorded", ...totals },
  ],
  models: [{ profile_id: "vendor", model: "vendor-chat", ...totals }],
};
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
function setup(active = true) {
  api.report.mockResolvedValue(report);
  api.profiles.mockResolvedValue([
    {
      profile_id: "vendor",
      name: "Other vendor",
      model: "vendor-chat",
      is_active: true,
      base_url: "https://vendor.test/v1",
    } satisfies Partial<ModelProfile>,
  ]);
  api.settings.mockResolvedValue({
    schema_version: 1,
    recording_started_at: report.recorded_from,
    pricing: {},
  });
  return render(<MemoryUsagePanel active={active} />);
}
async function expand() {
  fireEvent.click(screen.getByText("记忆模型用量"));
  await screen.findByRole("table");
}
it("shows actual daily usage, missing usage and unrecorded history; changes range and model", async () => {
  setup();
  await expand();
  expect(screen.getAllByText("未记录").length).toBeGreaterThan(0);
  expect(screen.getByText(/有 1 次请求未返回完整用量/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText("时间范围"), {
    target: { value: "30" },
  });
  await waitFor(() =>
    expect(api.report).toHaveBeenLastCalledWith(
      30,
      expect.any(String),
      undefined,
    ),
  );
  fireEvent.change(screen.getByLabelText("全部模型"), {
    target: { value: "vendor" },
  });
  await waitFor(() =>
    expect(api.report).toHaveBeenLastCalledWith(
      30,
      expect.any(String),
      "vendor",
    ),
  );
});
it("saves another vendor's currency, per-million prices and billing URL", async () => {
  setup();
  await expand();
  fireEvent.click(screen.getByRole("button", { name: "单价与额度入口" }));
  fireEvent.change(screen.getByLabelText("币种"), { target: { value: "USD" } });
  fireEvent.change(screen.getByLabelText("输入 token"), {
    target: { value: "2" },
  });
  fireEvent.change(screen.getByLabelText("输出 token"), {
    target: { value: "8" },
  });
  fireEvent.change(screen.getByLabelText("厂商用量 / 账单地址"), {
    target: { value: "https://vendor.test/billing" },
  });
  api.save.mockImplementation(async (id, value) => {
    api.settings.mockResolvedValue({
      schema_version: 1,
      recording_started_at: report.recorded_from,
      pricing: { [id]: value },
    });
    return value;
  });
  fireEvent.click(screen.getByRole("button", { name: "保存单价" }));
  await screen.findByText("已保存");
  expect(api.save).toHaveBeenCalledWith("vendor", {
    model: "vendor-chat",
    currency: "USD",
    input_per_million: "2",
    cached_input_per_million: null,
    output_per_million: "8",
    billing_url: "https://vendor.test/billing",
  });
  expect(
    screen
      .getByRole("link", { name: "查询厂商额度与账单" })
      .getAttribute("href"),
  ).toBe("https://vendor.test/billing");
});
it("does not read when inactive and shows a retryable failure without claiming zero usage", async () => {
  const view = setup(false);
  await act(async () => {});
  expect(api.report).not.toHaveBeenCalled();
  api.report.mockRejectedValueOnce(new Error("offline"));
  view.rerender(<MemoryUsagePanel active />);
  await waitFor(() => expect(api.report).toHaveBeenCalledTimes(1));
  expect(screen.queryByText(/0 token/)).toBeNull();
});
it("keeps mixed currencies separate and labels incomplete pricing", () => {
  const result = costLabel(
    {
      ...totals,
      costs: [
        ...totals.costs,
        {
          currency: "USD",
          estimated_min: "1",
          estimated_max: "1",
          priced_requests: 1,
        },
      ],
    },
    "en",
    "Unknown",
  );
  expect(result).toContain("CN");
  expect(result).toContain("$1.00");
  expect(result).toContain("Unknown");
});
