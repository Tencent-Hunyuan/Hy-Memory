// @vitest-environment jsdom
import type {
  ModelProfile,
  ProviderAccountBalance,
} from "@one-memory/contracts";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProviderBalanceCard } from "./ProviderBalanceCard";

const read = vi.hoisted(() => vi.fn());
vi.mock("./api", () => ({ getProviderBalance: read }));
const profile = {
  profile_id: "deepseek",
  revision: 1,
  name: "DeepSeek",
  is_active: true,
} as ModelProfile;
const ready: ProviderAccountBalance = {
  schema_version: 1,
  profile_id: "deepseek",
  state: "ready",
  is_available: true,
  balances: [
    {
      currency: "CNY",
      total_balance: "98.12",
      granted_balance: "8.12",
      topped_up_balance: "90",
    },
  ],
  checked_at: "2026-11-04T08:00:00Z",
  source_url: "https://api.deepseek.com/user/balance",
};
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
it("renders provider amounts separately from project cost and supports manual refresh", async () => {
  read.mockResolvedValue(ready);
  render(
    <ProviderBalanceCard profiles={[profile]} selectedProfileId="" active />,
  );
  await screen.findByText("¥98.12");
  expect(screen.getByText("¥8.12")).toBeTruthy();
  expect(screen.getByText("¥90.00")).toBeTruthy();
  expect(screen.getByText(/不等同本项目消耗/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "刷新余额" }));
  await waitFor(() => expect(read).toHaveBeenLastCalledWith("deepseek", true));
});
it.each(["unavailable", "credentials_unavailable", "unsupported"] as const)(
  "does not render a fake zero balance for %s",
  async (state) => {
    read.mockResolvedValue({ ...ready, state, balances: [], checked_at: null });
    render(
      <ProviderBalanceCard profiles={[profile]} selectedProfileId="" active />,
    );
    await waitFor(() => expect(read).toHaveBeenCalled());
    expect(screen.queryByText("¥0.00")).toBeNull();
  },
);
it("does not query a hidden page and discards a late reply for the old account", async () => {
  let finish: ((value: ProviderAccountBalance) => void) | undefined;
  read.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const other = {
    ...profile,
    profile_id: "other",
    revision: 2,
    name: "Another account",
  };
  const view = render(
    <ProviderBalanceCard
      profiles={[profile, other]}
      selectedProfileId="deepseek"
      active={false}
    />,
  );
  await act(async () => {});
  expect(read).not.toHaveBeenCalled();
  view.rerender(
    <ProviderBalanceCard
      profiles={[profile, other]}
      selectedProfileId="deepseek"
      active
    />,
  );
  await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
  read.mockResolvedValue({
    ...ready,
    profile_id: "other",
    state: "unsupported",
    balances: [],
  });
  view.rerender(
    <ProviderBalanceCard
      profiles={[profile, other]}
      selectedProfileId="other"
      active
    />,
  );
  await screen.findByText(/此厂商暂未接入/);
  await act(async () => finish?.(ready));
  expect(screen.queryByText("¥98.12")).toBeNull();
});

it("shows official organization spend without inventing an account balance", async () => {
  read.mockResolvedValue({
    ...ready,
    scope: "organization",
    balances: [],
    spending: [{ period: "month_to_date", amount: "12.34", currency: "USD" }],
  });
  render(
    <ProviderBalanceCard profiles={[profile]} selectedProfileId="" active />,
  );
  await screen.findByText("官方本月至今费用");
  expect(screen.getByText(/组织账单/)).toBeTruthy();
  expect(screen.queryByText("可用余额")).toBeNull();
});
it("distinguishes an unlimited key from zero credits", async () => {
  read.mockResolvedValue({
    ...ready,
    scope: "api_key",
    quota_unlimited: true,
    balances: [
      {
        currency: "USD",
        total_balance: null,
        granted_balance: null,
        topped_up_balance: null,
      },
    ],
  });
  render(
    <ProviderBalanceCard profiles={[profile]} selectedProfileId="" active />,
  );
  await screen.findByText("未设置 Key 限额");
  expect(screen.queryByText("$0.00")).toBeNull();
});
