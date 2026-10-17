// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import {
  OnboardingModelStep,
  type OnboardingModelStepProps,
} from "./OnboardingModelStep";

afterEach(cleanup);
function props(): OnboardingModelStepProps {
  return {
    draft: {
      provider_id: "fixture",
      name: "My profile",
      provider_kind: "openai_compatible",
      model: "user-model",
      base_url: "https://example.invalid/v1",
      api_key: "secret",
      disable_thinking: true,
      embedding_mode: "local",
      embedding_provider_kind: "openai_compatible",
      embedding_model: "",
      embedding_base_url: "",
      timeout_seconds: "240",
      max_concurrency: "4",
      context_window: "",
    },
    providers: [
      { id: "fixture", label: "用户服务", keyRequired: true, supported: true },
    ],
    advancedOpen: false,
    onAdvancedChange: vi.fn(),
    onDraftChange: vi.fn(),
    phase: "idle",
    capability: { saveAndEnable: false },
    canTest: true,
    canSaveDraft: true,
    canSaveAndEnable: true,
    sourceNames: ["Source A"],
    importedConversationCount: 37,
    generationRecipient: "用户服务 / 生成",
    embeddingRecipient: "本机 / 向量",
    onTest: vi.fn().mockResolvedValue(undefined),
    onSaveAndEnable: vi.fn().mockResolvedValue(undefined),
    onSaveDraft: vi.fn().mockResolvedValue(undefined),
    onBack: vi.fn(),
    onActionError: vi.fn(),
  };
}
it("renders step3 without engineering-only recipients, with unavailable enable failing closed", () => {
  const p = props();
  render(<OnboardingModelStep {...p} />);
  expect(
    screen.getByRole("heading", { name: "选好模型，让记忆开始沉淀" }),
  ).toBeTruthy();
  expect(
    screen
      .getByRole("navigation", { name: "首次设置进度" })
      .querySelector('[aria-current="step"]')?.textContent,
  ).toContain("沉淀记忆");
  fireEvent.click(screen.getByLabelText("模型服务商"));
  expect(screen.getByRole("option", { name: "用户服务" })).toBeTruthy();
  expect(screen.queryByRole("option", { name: "DeepSeek" })).toBeNull();
  expect(screen.queryByText("用户服务 / 生成")).toBeNull();
  expect(screen.queryByText("本机 / 向量")).toBeNull();
  expect(screen.getByText("Source A")).toBeTruthy();
  expect(screen.getAllByText(/当前不能在此启用模型/).length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("button", { name: "确认启用" }));
  expect(p.onSaveAndEnable).not.toHaveBeenCalled();
  expect(screen.queryByRole("checkbox", { name: /同意|允许发送/ })).toBeNull();
});
it("keeps a real multi-source summary compact", () => {
  const p = props();
  render(
    <OnboardingModelStep
      {...p}
      sourceNames={["WorkBuddy", "Cursor", "Codex", "Claude Code", "Pi"]}
    />,
  );
  expect(
    screen.getByText("WorkBuddy · Cursor · Codex 等 5 个应用"),
  ).toBeTruthy();
  expect(screen.queryByText("Pi")).toBeNull();
});
it("guides first-run keychain authorization when a saved key is unreadable", () => {
  const p = props();
  render(
    <OnboardingModelStep
      {...p}
      profile={{
        profile_id: "saved-profile",
        revision: 1,
        has_configured_api_key: true,
        credential_state: "unavailable",
      }}
    />,
  );
  expect(screen.getByText(/需要允许 MemHub 访问钥匙串/)).toBeTruthy();
  expect(screen.getByText(/始终允许/)).toBeTruthy();
});
it("draft edits and advanced mode are controlled; return never clears or saves values", () => {
  const p = props();
  const view = render(<OnboardingModelStep {...p} />);
  fireEvent.change(screen.getByLabelText("API Key"), {
    target: { value: "new-secret" },
  });
  expect(p.onDraftChange).toHaveBeenCalledWith({ api_key: "new-secret" });
  expect((screen.getByLabelText("API Key") as HTMLInputElement).value).toBe(
    "secret",
  );
  fireEvent.click(screen.getByRole("button", { name: "配置详情与高级设置" }));
  expect(p.onAdvancedChange).toHaveBeenCalledWith(true);
  view.rerender(<OnboardingModelStep {...p} advancedOpen />);
  expect(screen.getByLabelText("API Key")).toHaveProperty("value", "secret");
  fireEvent.click(screen.getByRole("button", { name: "返回服务商选择" }));
  expect(p.onAdvancedChange).toHaveBeenLastCalledWith(false);
  expect(
    screen.getByRole("heading", { name: "配置详情，按需要调整" }),
  ).toBeTruthy();
  expect((screen.getByLabelText("超时（秒）") as HTMLInputElement).max).toBe(
    "600",
  );
  expect((screen.getByLabelText("最大并发") as HTMLInputElement).max).toBe(
    "32",
  );
  expect((screen.getByLabelText("上下文窗口") as HTMLInputElement).min).toBe(
    "1024",
  );
  fireEvent.click(screen.getByRole("button", { name: "跳过，先进入应用" }));
  expect(p.onBack).toHaveBeenCalledTimes(1);
  expect(p.onSaveDraft).not.toHaveBeenCalled();
  expect(p.draft.api_key).toBe("secret");
});
it("keeps skip available while an uncertain write is reconciled", () => {
  const p = props();
  render(
    <OnboardingModelStep
      {...p}
      advancedOpen
      inputsLocked
      capability={{ saveAndEnable: true }}
      enableActionLabel="核对提交状态"
    />,
  );
  expect((screen.getByLabelText("配置名称") as HTMLInputElement).disabled).toBe(
    true,
  );
  expect(
    (
      screen.getByRole("button", {
        name: "跳过，先进入应用",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "跳过，先进入应用" }));
  expect(p.onBack).toHaveBeenCalledOnce();
  expect(
    (screen.getByRole("button", { name: "核对提交状态" }) as HTMLButtonElement)
      .disabled,
  ).toBe(false);
});
it.each(["testing", "saving", "enabling"] as const)(
  "%s locks controlled actions and fields",
  (phase) => {
    const p = props();
    render(<OnboardingModelStep {...p} phase={phase} />);
    expect(
      (screen.getByLabelText("API Key") as HTMLInputElement).disabled,
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "跳过，先进入应用" }));
    expect(p.onBack).not.toHaveBeenCalled();
    if (phase === "saving")
      expect(screen.queryByRole("status", { name: "模型提交状态" })).toBeNull();
    else
      expect(screen.getByRole("status", { name: "模型提交状态" })).toBeTruthy();
  },
);
it("one in-flight callback across double click, errors stay owner-controlled without exposing thrown secrets", async () => {
  const p = props();
  let reject!: (e: Error) => void;
  p.onSaveAndEnable = vi.fn(
    () =>
      new Promise<void>((_, r) => {
        reject = r;
      }),
  );
  render(
    <OnboardingModelStep
      {...p}
      capability={{ saveAndEnable: true }}
      probe={{ state: "passed", message: "测试通过" }}
    />,
  );
  const button = screen.getByRole("button", { name: "确认启用" });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(p.onSaveAndEnable).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("button", { name: "测试连接" })).toBeNull();
  expect(screen.queryByRole("button", { name: "保存草稿" })).toBeNull();
  await act(async () => reject(new Error("SECRET_TOKEN")));
  expect(p.onActionError).toHaveBeenCalledWith("save-and-enable");
  expect(screen.queryByText(/SECRET_TOKEN/)).toBeNull();
  await waitFor(() =>
    expect((button as HTMLButtonElement).disabled).toBe(false),
  );
});
it("renders field errors with links and preserves custom embedding in advanced mode", () => {
  const p = props();
  const view = render(
    <OnboardingModelStep
      {...p}
      error="配置已变化，请核对。"
      fieldErrors={{ api_key: "密钥未通过验证" }}
    />,
  );
  const key = screen.getByLabelText("API Key");
  expect(key.getAttribute("aria-invalid")).toBe("true");
  expect(
    document.getElementById(key.getAttribute("aria-describedby") ?? "")
      ?.textContent,
  ).toContain("密钥未通过验证");
  view.rerender(
    <OnboardingModelStep
      {...p}
      advancedOpen
      draft={{
        ...p.draft,
        embedding_mode: "custom",
        embedding_model: "saved-vector",
        embedding_base_url: "http://127.0.0.1:11434",
      }}
    />,
  );
  expect(
    (screen.getByLabelText("向量模型名称") as HTMLInputElement).value,
  ).toBe("saved-vector");
  expect(screen.queryByRole("button", { name: "收起高级配置" })).toBeNull();
  expect(p.onDraftChange).not.toHaveBeenCalled();
});

it("discloses that testing does not send historical conversations", () => {
  render(<OnboardingModelStep {...props()} />);

  expect(screen.getByText(/测试连接只发送测试内容/)).toBeTruthy();
  expect(screen.getByText(/已导入 37 条对话/)).toBeTruthy();
  expect(screen.getByText(/启用后会使用此模型整理/)).toBeTruthy();
});

it("does not expose an extra backlog selector in the first-run model page", () => {
  const p = props();
  render(<OnboardingModelStep {...p} capability={{ saveAndEnable: true }} />);
  fireEvent.click(screen.getByRole("button", { name: "确认启用" }));
  expect(p.onSaveAndEnable).not.toHaveBeenCalled();
  expect(screen.getByText(/先测试模型连通性/)).toBeTruthy();
  expect(screen.queryByLabelText("启用前积压")).toBeNull();
});
it("separates connectivity testing from final enable", async () => {
  const p = props();
  render(<OnboardingModelStep {...p} capability={{ saveAndEnable: true }} />);
  expect(screen.getByRole("button", { name: "测试模型连通性" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "保存草稿" })).toBeNull();
  expect(screen.getByRole("button", { name: "确认启用" })).toHaveProperty(
    "disabled",
    true,
  );
  fireEvent.click(screen.getByRole("button", { name: "测试模型连通性" }));
  await act(async () => {});
  expect(p.onTest).toHaveBeenCalledTimes(1);
  expect(p.onSaveDraft).not.toHaveBeenCalled();
  expect(p.onSaveAndEnable).not.toHaveBeenCalled();
});

it("requires owner validation as well as policy capability and truthful recipients", () => {
  const p = props();
  const view = render(
    <OnboardingModelStep
      {...p}
      capability={{ saveAndEnable: true }}
      canSaveAndEnable={false}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "确认启用" }));
  view.rerender(
    <OnboardingModelStep
      {...p}
      capability={{ saveAndEnable: true }}
      generationRecipient=""
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "确认启用" }));
  expect(p.onSaveAndEnable).not.toHaveBeenCalled();
});

it("opens the advanced design state with a route back to provider selection", () => {
  const p = props();
  const view = render(<OnboardingModelStep {...p} />);
  const toggle = screen.getByRole("button", { name: "配置详情与高级设置" });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  expect(
    document.getElementById(toggle.getAttribute("aria-controls") ?? ""),
  ).toBeTruthy();
  toggle.focus();
  fireEvent.click(toggle);
  view.rerender(<OnboardingModelStep {...p} advancedOpen />);
  expect(screen.getByRole("button", { name: "返回服务商选择" })).toBeTruthy();
  expect(screen.getByLabelText("API Key")).toBeTruthy();
  expect(
    screen.getByText("预设已填好，只有自定义服务或特殊需求才需要修改。"),
  ).toBeTruthy();
  expect(p.onDraftChange).not.toHaveBeenCalled();
});

it("uses the failure-state title and omits consent and scope-management UI", () => {
  const p = props();
  render(
    <OnboardingModelStep
      {...p}
      capability={{ saveAndEnable: true }}
      probe={{ state: "failed", message: "synthetic failure" }}
    />,
  );
  expect(
    screen.getByRole("heading", { name: "连接未通过，配置已保留" }),
  ).toBeTruthy();
  expect(screen.getByText(/连接未通过，请检查 API Key/)).toBeTruthy();
  expect(screen.getByText("synthetic failure")).toBeTruthy();
  expect(screen.queryByText("调整处理范围")).toBeNull();
  expect(screen.queryByText(/允许发送少量测试内容/)).toBeNull();
});

it("keeps the first-run model form free of a second consent strip", () => {
  const p = props();
  render(
    <OnboardingModelStep
      {...p}
      advancedOpen
      draft={{ ...p.draft, embedding_mode: "custom" }}
    />,
  );
  const fields = screen.getByRole("region", { name: "模型参数" });
  expect(within(fields).getByLabelText("向量 Base URL")).toBeTruthy();
  expect(screen.queryByText(/允许发送少量测试内容/)).toBeNull();
  expect(screen.queryByText(/通过后自动启用/)).toBeNull();
  expect(screen.queryByText(p.generationRecipient)).toBeNull();
  expect(screen.queryByText(p.embeddingRecipient)).toBeNull();
  expect(screen.queryByLabelText("启用前积压")).toBeNull();
});
