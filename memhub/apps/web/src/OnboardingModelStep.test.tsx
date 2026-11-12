// @vitest-environment jsdom
import { guiMessages, SUPPORTED_LOCALES } from "@one-memory/i18n";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "./gui-language";
import {
  OnboardingModelStep,
  type OnboardingModelStepProps,
} from "./OnboardingModelStep";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

function props(): OnboardingModelStepProps {
  return {
    draft: {
      provider_id: "deepseek",
      name: "DeepSeek",
      provider_kind: "openai_compatible",
      model: "deepseek-flash",
      base_url: "https://api.deepseek.com",
      api_key: "",
      disable_thinking: true,
      embedding_mode: "local",
      embedding_provider_kind: "openai_compatible",
      embedding_model: "",
      embedding_base_url: "",
      timeout_seconds: "",
      max_concurrency: "",
      context_window: "",
    },
    providers: [
      {
        id: "deepseek",
        label: "DeepSeek",
        keyRequired: true,
        supported: true,
      },
      {
        id: "ollama",
        label: "Ollama（本机）",
        keyRequired: false,
        supported: true,
      },
    ],
    advancedOpen: false,
    onAdvancedChange: vi.fn(),
    onDraftChange: vi.fn(),
    phase: "idle",
    capability: { saveAndEnable: true },
    canTest: true,
    canSaveDraft: true,
    canSaveAndEnable: false,
    sourceNames: ["Cursor", "Codex"],
    importedConversationCount: 12,
    generationRecipient: "https://api.deepseek.com",
    embeddingRecipient: "本机",
    onTest: vi.fn().mockResolvedValue(undefined),
    onSaveAndEnable: vi.fn().mockResolvedValue(undefined),
    onSaveDraft: vi.fn().mockResolvedValue(undefined),
    onBack: vi.fn(),
  };
}

describe("OnboardingModelStep", () => {
  it.each(SUPPORTED_LOCALES)("renders named controls in %s", (locale) => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, locale);
    const title = guiMessages[locale]["配置记忆整理模型"];
    const back = guiMessages[locale]["返回选择模式"];
    const summary = guiMessages[locale]["记忆整理"];
    if (!title || !back || !summary)
      throw new Error(`missing ${locale} model copy`);
    render(
      <LanguageProvider>
        <OnboardingModelStep {...props()} />
      </LanguageProvider>,
    );
    expect(
      screen.getByRole("heading", {
        name: title,
      }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", {
        name: back,
      }),
    ).toBeTruthy();
    expect(screen.getByLabelText(/API Key/)).toBeTruthy();
    expect(screen.getByRole("complementary", { name: summary })).toBeTruthy();
    expect(screen.getAllByRole("button").length).toBeGreaterThanOrEqual(4);
  });

  it("keeps current behavior in the restored open-source composition", () => {
    render(<OnboardingModelStep {...props()} />);
    expect(
      screen.getByRole("heading", { name: "配置记忆整理模型" }),
    ).toBeTruthy();
    expect(screen.getByText("智能记忆 Pro · 配置记忆整理")).toBeTruthy();
    expect(
      screen
        .getByRole("navigation", { name: "首次设置进度" })
        .querySelector('[aria-current="step"]')?.textContent,
    ).toContain("记忆模式");
    expect(screen.getByText("DeepSeek · deepseek-flash")).toBeTruthy();
    expect(screen.queryByText("本次沉淀")).toBeNull();
    expect(screen.queryByLabelText(/向量|Embedding/)).toBeNull();
    expect(document.querySelector(".onboarding-model-layout")).toBeTruthy();
    expect(document.querySelector(".onboarding-model-summary")).toBeTruthy();
    expect(screen.getByText("让记忆从这里开始")).toBeTruthy();
  });

  it("focuses the page title when entering the model substep", async () => {
    render(<OnboardingModelStep {...props()} focusToken={7} />);
    const title = screen.getByRole("heading", {
      name: "配置记忆整理模型",
    });
    await waitFor(() => expect(document.activeElement).toBe(title));
  });

  it("uses back, test, and enable actions without a skip path", async () => {
    const p = props();
    render(<OnboardingModelStep {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "返回选择模式" }));
    fireEvent.click(screen.getByRole("button", { name: "测试连接" }));
    await act(async () => {});
    expect(p.onBack).toHaveBeenCalledOnce();
    expect(p.onTest).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: /跳过/ })).toBeNull();
    expect(screen.getByRole("button", { name: "启用并进入" })).toHaveProperty(
      "disabled",
      true,
    );
  });

  it("renders shared technical generation fields without embedding", () => {
    const p = props();
    const view = render(<OnboardingModelStep {...p} />);
    const mountedModel =
      document.querySelector<HTMLInputElement>('[name="model"]');
    expect(mountedModel).toBeTruthy();
    expect(mountedModel?.closest("[hidden]")).toBeTruthy();
    const disclosure = screen.getByRole("button", {
      name: "配置详情与高级设置",
    });
    expect(disclosure.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(disclosure);
    expect(p.onAdvancedChange).toHaveBeenCalledWith(true);
    view.rerender(<OnboardingModelStep {...p} advancedOpen />);
    expect(screen.getByLabelText("协议")).toBeTruthy();
    expect(screen.getByLabelText("生成模型名")).toHaveProperty(
      "value",
      "deepseek-flash",
    );
    expect(screen.getByLabelText("Base URL")).toBeTruthy();
    expect(screen.getByLabelText("超时（秒）")).toHaveProperty("value", "");
    expect(screen.getByLabelText("最大并发")).toBeTruthy();
    expect(screen.getByLabelText("上下文窗口")).toBeTruthy();
    expect(screen.getByLabelText("关闭思考模式")).toBeTruthy();
    expect(screen.getByText("留空使用 Core 默认值。")).toBeTruthy();
    expect(screen.queryByText(/向量模型/)).toBeNull();
  });

  it("expands technical details and focuses the first invalid required field", async () => {
    const p = props();
    p.draft = { ...p.draft, model: "" };
    p.fieldErrors = { model: "请填写实际可用的模型 ID。" };
    const view = render(<OnboardingModelStep {...p} />);
    await waitFor(() => expect(p.onAdvancedChange).toHaveBeenCalledWith(true));
    view.rerender(<OnboardingModelStep {...p} advancedOpen />);
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByLabelText("生成模型名")),
    );
  });

  it("returns to the visible API key field when it fails in advanced mode", async () => {
    const p = props();
    p.advancedOpen = true;
    p.fieldErrors = { api_key: "请填写 API Key。" };
    const view = render(<OnboardingModelStep {...p} />);
    await waitFor(() => expect(p.onAdvancedChange).toHaveBeenCalledWith(false));
    view.rerender(<OnboardingModelStep {...p} advancedOpen={false} />);
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByLabelText("API Key")),
    );
  });

  it("links field errors and keeps secrets out of callback errors", async () => {
    const p = props();
    p.fieldErrors = { api_key: "请填写 API Key。" };
    p.canSaveAndEnable = true;
    p.probe = { state: "passed", message: "模型连接测试通过，可以启用。" };
    p.onSaveAndEnable = vi.fn().mockRejectedValue(new Error("SECRET_TOKEN"));
    render(<OnboardingModelStep {...p} />);
    const key = screen.getByLabelText("API Key");
    expect(key.getAttribute("aria-invalid")).toBe("true");
    expect(
      document.getElementById(key.getAttribute("aria-describedby") ?? "")
        ?.textContent,
    ).toContain("请填写 API Key");
    const enable = screen.getByRole("button", { name: "启用并进入" });
    fireEvent.click(enable);
    fireEvent.click(enable);
    await act(async () => {});
    expect(p.onSaveAndEnable).toHaveBeenCalledOnce();
    expect(screen.queryByText(/SECRET_TOKEN/)).toBeNull();
  });

  it("keeps recovery controls while embedding UI remains hidden", async () => {
    const p = props();
    p.onRecoverCreation = vi.fn().mockResolvedValue(undefined);
    p.onDiscardMissingCreation = vi.fn().mockResolvedValue(undefined);
    render(<OnboardingModelStep {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "核对创建结果" }));
    fireEvent.click(
      screen.getByRole("button", { name: "放弃旧记录，重新配置" }),
    );
    await act(async () => {});
    expect(p.onRecoverCreation).toHaveBeenCalledOnce();
    expect(p.onDiscardMissingCreation).toHaveBeenCalledOnce();
  });
});
