// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { BillingAuthorizationForm } from "./BillingAuthorizationForm";

const api = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), remove: vi.fn() }));
vi.mock("./api", () => ({
  getBillingAuthorization: api.get,
  setBillingAuthorization: api.put,
  deleteBillingAuthorization: api.remove,
}));
const config = {
  schema_version: 1,
  provider: "openai",
  configured: false,
  docs_url: "https://platform.openai.com/docs",
  fields: [
    { name: "api_key", required: true, secret: true },
    { name: "project_id", required: false, secret: false },
  ],
};
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
it("uses a separate password field, saves the billing key and clears it after success", async () => {
  api.get.mockResolvedValue(config);
  api.put.mockResolvedValue({ ...config, configured: true });
  const saved = vi.fn();
  render(<BillingAuthorizationForm profileId="profile" onSaved={saved} />);
  const field = await screen.findByLabelText("账务 API Key");
  expect(field.getAttribute("type")).toBe("password");
  expect(screen.getByText(/普通模型 Key 不能读取组织账单/)).toBeTruthy();
  fireEvent.change(field, { target: { value: "PRIVATE_ADMIN_KEY" } });
  fireEvent.change(screen.getByLabelText("项目 ID（可选）"), {
    target: { value: "proj_123" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存账务授权" }));
  await screen.findByText(/已保存，请以刷新后的查询结果/);
  expect(api.put).toHaveBeenCalledWith("profile", {
    api_key: "PRIVATE_ADMIN_KEY",
    project_id: "proj_123",
  });
  expect((field as HTMLInputElement).value).toBe("");
  expect(screen.queryByText("PRIVATE_ADMIN_KEY")).toBeNull();
  expect(saved).toHaveBeenCalled();
});
it("removes an existing billing authorization without touching model settings", async () => {
  api.get.mockResolvedValue({ ...config, configured: true });
  api.remove.mockResolvedValue(config);
  const saved = vi.fn();
  render(<BillingAuthorizationForm profileId="profile" onSaved={saved} />);
  fireEvent.click(await screen.findByRole("button", { name: "移除账务授权" }));
  await waitFor(() => expect(api.remove).toHaveBeenCalledWith("profile"));
  await waitFor(() => expect(saved).toHaveBeenCalled());
  expect(api.put).not.toHaveBeenCalled();
});
it("reports a failed save without echoing credentials", async () => {
  api.get.mockResolvedValue(config);
  api.put.mockRejectedValue(new Error("provider error"));
  render(<BillingAuthorizationForm profileId="profile" onSaved={() => {}} />);
  fireEvent.change(await screen.findByLabelText("账务 API Key"), {
    target: { value: "SECRET" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存账务授权" }));
  await screen.findByRole("alert");
  expect(screen.queryByText("SECRET")).toBeNull();
});
