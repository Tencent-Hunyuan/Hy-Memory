// @vitest-environment jsdom

import type { ModelProbeResult, ModelProfile } from "@one-memory/contracts";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRef, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ModelSettings,
  type ModelSettingsActions,
  probeLabel,
} from "./ModelSettings";
import type { PendingModelProbe } from "./pending-model-probe";
import { PROVIDER_PRESETS } from "./provider-presets";

const create = vi.fn();
const probeProfile = vi.fn();
const activate = vi.fn();
const remove = vi.fn();
const update = vi.fn();

it("explains an insufficient model balance with the required recovery", () => {
  expect(probeLabel("insufficient_balance")).toBe(
    "模型账户余额不足；充值或换 key 后再检测，会自动接着处理",
  );
});

let profiles: ModelProfile[];
let probes: Record<string, ModelProbeResult> = {};
let pendingProbe: PendingModelProbe | null = null;

const profile: ModelProfile = {
  profile_id: "profile-1",
  name: "Saved provider",
  provider_kind: "openai_compatible",
  base_url: "https://models.example.com/v1",
  model: "memory-model",
  embedding_model: "bge-small-zh-v1.5",
  embedding_provider_kind: "local_onnx",
  embedding_base_url: null,
  embedding_dimensions: 512,
  has_api_key: true,
  timeout_seconds: 60,
  max_concurrency: 1,
  context_window: null,
  disable_thinking: false,
  capabilities: {
    endpoint_reachable: false,
    model_available: false,
    structured_output: false,
    embedding_available: false,
  },
  revision: 1,
  is_active: false,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

/** A downstream distribution preset: everything filled but the key. */
const preset: ModelProfile = {
  ...profile,
  profile_id: "preset-1",
  name: "预配置模型服务",
  base_url: "https://models.example.com/v1",
  model: "memory-model",
  has_api_key: false,
  disable_thinking: true,
};

/** Re-renders on demand so a cleared journal is picked up, like a real refresh. */
function ProbeRecoveryHarness({
  controls,
}: {
  controls: { current: ModelSettingsActions | null };
}) {
  const [, setTick] = useState(0);
  return (
    <ModelSettings
      compactSingle
      controlsRef={controls}
      onProfilesChanged={() => setTick((value) => value + 1)}
    />
  );
}

vi.mock("./use-model-profiles", () => ({
  useModelProfiles: () => ({
    profiles,
    probe: null,
    probeFor: (value: ModelProfile) =>
      probes[value.profile_id]?.profile_revision === value.revision
        ? probes[value.profile_id]
        : null,
    probeErrorFor: () => null,
    busy: null,
    error: null,
    pendingProbe,
    probeStorageFailed: false,
    create,
    probeProfile,
    activate,
    remove,
    update,
  }),
}));

describe("ModelSettings", () => {
  it("exposes distinct edit, add and test actions from the same owner without activating", async () => {
    const controls = createRef<ModelSettingsActions>();
    render(<ModelSettings controlsRef={controls} />);
    act(() => {
      expect(controls.current?.edit(profile.profile_id)).toBe(true);
    });
    expect((screen.getByLabelText("配置名称") as HTMLInputElement).value).toBe(
      profile.name,
    );
    act(() => {
      expect(controls.current?.add()).toBe(true);
    });
    expect((screen.getByLabelText("配置名称") as HTMLInputElement).value).toBe(
      "",
    );
    await act(async () => {
      await controls.current?.test(profile.profile_id);
    });
    expect(probeProfile).toHaveBeenCalledExactlyOnceWith(profile.profile_id);
    expect(activate).not.toHaveBeenCalled();
    expect(create).not.toHaveBeenCalled();
    expect(update).not.toHaveBeenCalled();
    expect(controls.current?.edit("missing")).toBe(false);
    await expect(controls.current?.test("missing")).rejects.toThrow(
      "model_profile_not_available",
    );
  });
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
    probes = {};
    pendingProbe = null;
    profiles = [profile];
    create.mockResolvedValue(profile);
    update.mockResolvedValue({ ...preset, has_api_key: true, revision: 2 });
  });

  it("submits a generation-only profile and leaves embedding to the builtin", async () => {
    const user = userEvent.setup();
    render(<ModelSettings />);

    expect((screen.getByLabelText(/^API Key/) as HTMLInputElement).type).toBe(
      "password",
    );
    // 默认视图里不该有 embedding。它是内置的、用户改不了的实现细节，
    // 摆一个只读输入框在这儿，读者只会以为自己漏配了什么。
    expect(screen.queryByLabelText("Embedding")).toBeNull();
    expect(screen.queryByText(/已内置 bge-small-zh-v1.5/)).toBeNull();
    await user.type(screen.getByLabelText("配置名称"), "My provider");
    await user.type(screen.getByLabelText("生成模型名"), "model-v1");
    await user.type(screen.getByLabelText(/^API Key/), "secret-value");
    await user.click(screen.getByRole("button", { name: "保存配置" }));

    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "My provider",
        provider_kind: "openai_compatible",
        model: "model-v1",
        api_key: "secret-value",
      }),
    );
    expect(create.mock.calls[0][0].embedding_model).toBeUndefined();
    expect(screen.getByText(/只需配置生成模型/)).toBeTruthy();
  });

  it("edits an existing custom profile without creating another UUID or refilling the stored key", async () => {
    update.mockResolvedValue(profile);
    render(<ModelSettings />);
    await userEvent.click(screen.getByRole("button", { name: "编辑配置" }));
    expect(screen.getByLabelText("生成模型名")).toHaveProperty(
      "value",
      profile.model,
    );
    expect(screen.getByLabelText("Base URL")).toHaveProperty(
      "value",
      profile.base_url,
    );
    expect(screen.getByLabelText("配置名称")).toHaveProperty(
      "value",
      profile.name,
    );
    expect(screen.getByLabelText(/^API Key/)).toHaveProperty("value", "");
    expect(screen.getByText(/留空保留已保存密钥/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));
    expect(update).toHaveBeenCalledExactlyOnceWith(profile.profile_id, {
      expected_revision: 1,
    });
    expect(create).not.toHaveBeenCalled();
    expect(probeProfile).not.toHaveBeenCalled();
    expect(activate).not.toHaveBeenCalled();
    expect(
      screen.getByRole("article", { name: "已保存的模型配置" }).textContent,
    ).toContain("版本 1");
  });

  it("offers the same vendor choices while editing and does not reuse the saved key", async () => {
    const user = userEvent.setup();
    profiles = [
      {
        ...profile,
        name: "DeepSeek",
        base_url: "https://api.deepseek.com",
        model: "deepseek-flash",
        has_api_key: true,
      },
    ];
    render(<ModelSettings />);
    await user.click(screen.getByRole("button", { name: "编辑配置" }));
    const vendor = screen.getByLabelText("服务商预设");
    expect(vendor.textContent).toContain("DeepSeek");
    await user.click(vendor);
    expect(
      within(screen.getByRole("listbox"))
        .getAllByRole("option")
        .map((item) => item.textContent),
    ).toEqual([
      ...PROVIDER_PRESETS.map((item) => item.label),
      "自定义兼容服务",
    ]);
    await user.click(screen.getByRole("option", { name: "Kimi（月之暗面）" }));
    expect(screen.getByLabelText("Base URL")).toHaveProperty(
      "value",
      "https://api.moonshot.cn/v1",
    );
    expect(screen.getByLabelText("生成模型名")).toHaveProperty(
      "value",
      "kimi-k3",
    );
    expect(screen.getByLabelText("配置名称")).toHaveProperty(
      "value",
      "Kimi（月之暗面）",
    );
    expect(screen.getByText(/不会带到新地址/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "保存修改" }));
    expect(update).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText(/^API Key/), "new-key");
    await user.click(screen.getByRole("button", { name: "保存修改" }));
    expect(update).toHaveBeenCalledExactlyOnceWith(profile.profile_id, {
      expected_revision: 1,
      name: "Kimi（月之暗面）",
      model: "kimi-k3",
      base_url: "https://api.moonshot.cn/v1",
      api_key: "new-key",
      disable_thinking: true,
    });
    expect(create).not.toHaveBeenCalled();
  });

  it("starts a new profile when an edit switches to a different protocol", async () => {
    const user = userEvent.setup();
    profiles = [
      {
        ...profile,
        name: "DeepSeek",
        base_url: "https://api.deepseek.com",
        model: "deepseek-flash",
        has_api_key: true,
      },
    ];
    render(<ModelSettings />);
    await user.click(screen.getByRole("button", { name: "编辑配置" }));
    await user.click(screen.getByLabelText("服务商预设"));
    await user.click(
      within(screen.getByRole("listbox")).getByRole("option", {
        name: "Ollama（本机）",
      }),
    );
    await user.type(screen.getByLabelText("生成模型名"), "qwen2.5");
    await user.click(screen.getByRole("button", { name: "保存修改" }));
    expect(update).not.toHaveBeenCalled();
    expect(create).toHaveBeenCalledExactlyOnceWith({
      name: "Ollama（本机）",
      provider_kind: "ollama",
      model: "qwen2.5",
      base_url: "http://127.0.0.1:11434",
      disable_thinking: true,
    });
  });

  it("preserves edited inputs and replacement key on failure then saves the same identity", async () => {
    update.mockResolvedValueOnce(undefined).mockResolvedValueOnce({
      ...profile,
      revision: 2,
      model: "changed",
      is_active: false,
    });
    render(<ModelSettings />);
    await userEvent.click(screen.getByRole("button", { name: "编辑配置" }));
    await userEvent.clear(screen.getByLabelText("生成模型名"));
    await userEvent.type(screen.getByLabelText("生成模型名"), "changed");
    await userEvent.type(
      screen.getByLabelText(/^API Key/),
      "synthetic-replacement",
    );
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));
    expect(screen.getByLabelText("生成模型名")).toHaveProperty(
      "value",
      "changed",
    );
    expect(screen.getByLabelText(/^API Key/)).toHaveProperty(
      "value",
      "synthetic-replacement",
    );
    expect(screen.getByRole("alert").textContent).not.toContain(
      "synthetic-replacement",
    );
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));
    expect(update).toHaveBeenLastCalledWith(profile.profile_id, {
      expected_revision: 1,
      model: "changed",
      api_key: "synthetic-replacement",
    });
    expect(create).not.toHaveBeenCalled();
    expect(activate).not.toHaveBeenCalled();
    expect(probeProfile).not.toHaveBeenCalled();
    expect(
      screen.getByRole("article", { name: "已保存的模型配置" }).textContent,
    ).toContain("版本 2");
    expect(screen.queryByLabelText(/^API Key/)).toBeNull();
  });

  it("keeps unrelated model fields when editing an existing advanced embedding configuration", async () => {
    const existing = {
      ...profile,
      embedding_model: "local-embedding",
      embedding_provider_kind: "ollama" as const,
      embedding_base_url: "http://127.0.0.1:11434",
      context_window: 32000,
      timeout_seconds: 90,
      max_concurrency: 2,
    };
    profiles = [existing];
    update.mockResolvedValue(existing);
    render(<ModelSettings />);
    await userEvent.click(screen.getByRole("button", { name: "编辑配置" }));
    expect(screen.getByLabelText("向量模型名")).toHaveProperty(
      "value",
      "local-embedding",
    );
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));
    expect(update).toHaveBeenCalledExactlyOnceWith(profile.profile_id, {
      expected_revision: 1,
    });
  });

  it("does not replace an edit draft with a newer external profile revision", async () => {
    const view = render(<ModelSettings />);
    await userEvent.click(screen.getByRole("button", { name: "编辑配置" }));
    await userEvent.type(screen.getByLabelText("生成模型名"), "-draft");
    profiles = [{ ...profile, revision: 2, model: "external" }];
    view.rerender(<ModelSettings />);
    expect(screen.getByLabelText("生成模型名")).toHaveProperty(
      "value",
      "memory-model-draft",
    );
    expect(screen.getByRole("button", { name: "保存修改" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(screen.getByText(/配置已在其他位置变化/)).toBeTruthy();
    expect(update).not.toHaveBeenCalled();
  });

  it("preserves a new custom embedding when its disclosure is collapsed", async () => {
    render(<ModelSettings />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("生成模型名"), "model-v1");
    await user.type(screen.getByLabelText(/^API Key/), "synthetic-key");
    const disclosure = screen.getByRole("button", {
      name: "高级：自行配置向量模型",
    });
    await user.click(disclosure);
    await user.selectOptions(screen.getByLabelText("向量配置模式"), "custom");
    await user.type(screen.getByLabelText("向量模型名"), "custom-vector");
    await user.type(
      screen.getByLabelText("向量模型 Base URL"),
      "https://api.openai.com/v1",
    );
    await user.click(disclosure);
    await user.click(screen.getByRole("button", { name: "保存配置" }));
    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({
        embedding_provider_kind: "openai_compatible",
        embedding_model: "custom-vector",
        embedding_base_url: "https://api.openai.com/v1",
      }),
    );
    expect(probeProfile).not.toHaveBeenCalled();
    expect(activate).not.toHaveBeenCalled();
  });

  it("does not turn opening advanced fields into an embedding mode change", async () => {
    render(<ModelSettings />);
    await userEvent.type(screen.getByLabelText("生成模型名"), "model-v1");
    await userEvent.type(screen.getByLabelText(/^API Key/), "synthetic-key");
    await userEvent.click(
      screen.getByRole("button", { name: "高级：自行配置向量模型" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "保存配置" }));
    expect(create).toHaveBeenCalledOnce();
    expect(create.mock.calls[0][0].embedding_model).toBeUndefined();
  });

  it("saves an edited custom embedding after collapsing its fields", async () => {
    profiles = [
      {
        ...profile,
        embedding_provider_kind: "ollama",
        embedding_model: "vector-old",
        embedding_base_url: "http://127.0.0.1:11434",
      },
    ];
    render(<ModelSettings />);
    await userEvent.click(screen.getByRole("button", { name: "编辑配置" }));
    await userEvent.clear(screen.getByLabelText("向量模型名"));
    await userEvent.type(screen.getByLabelText("向量模型名"), "vector-new");
    await userEvent.click(
      screen.getByRole("button", { name: "高级：自行配置向量模型" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));
    expect(update).toHaveBeenCalledWith(profile.profile_id, {
      expected_revision: 1,
      embedding_model: "vector-new",
    });
  });

  it("explicitly switches an edited remote embedding to builtin without retaining its dimensions", async () => {
    profiles = [
      {
        ...profile,
        embedding_provider_kind: "ollama",
        embedding_model: "vector-old",
        embedding_base_url: "http://127.0.0.1:11434",
        embedding_dimensions: 768,
      },
    ];
    render(<ModelSettings />);
    await userEvent.click(screen.getByRole("button", { name: "编辑配置" }));
    await userEvent.selectOptions(
      screen.getByLabelText("向量配置模式"),
      "local",
    );
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));
    expect(update).toHaveBeenCalledWith(profile.profile_id, {
      expected_revision: 1,
      embedding_provider_kind: "local_onnx",
      embedding_model: "bge-small-zh-v1.5",
      embedding_dimensions: 512,
    });
  });

  it("reopens invalid custom fields instead of saving builtin after collapse", async () => {
    render(<ModelSettings />);
    await userEvent.type(screen.getByLabelText("生成模型名"), "model-v1");
    await userEvent.type(screen.getByLabelText(/^API Key/), "synthetic-key");
    const disclosure = screen.getByRole("button", {
      name: "高级：自行配置向量模型",
    });
    await userEvent.click(disclosure);
    await userEvent.selectOptions(
      screen.getByLabelText("向量配置模式"),
      "custom",
    );
    await userEvent.click(disclosure);
    await userEvent.click(screen.getByRole("button", { name: "保存配置" }));
    expect(create).not.toHaveBeenCalled();
    expect(disclosure.getAttribute("aria-expanded")).toBe("true");
    expect(
      screen.getByLabelText(/^向量模型名/).getAttribute("aria-invalid"),
    ).toBe("true");
  });

  it("attaches test feedback to the named profile revision and not a different saved card", async () => {
    profiles = [
      profile,
      {
        ...profile,
        profile_id: "second",
        name: "Second",
        base_url: "https://second.example/v1",
      },
    ];
    probes = {
      [profile.profile_id]: {
        profile_id: profile.profile_id,
        profile_revision: 1,
        capabilities: profile.capabilities,
        stages: [
          {
            stage: "authentication",
            ok: false,
            code: "unauthorized",
            latency_ms: 1,
          },
        ],
      },
    };
    const view = render(<ModelSettings />);
    const result = screen.getByRole("region", {
      name: "Saved provider 版本 1 测试结果",
    });
    expect(result.textContent).toContain("认证失败");
    expect(result.textContent).toContain("https://models.example.com/v1");
    expect(
      screen.queryByRole("region", { name: "Second 版本 1 测试结果" }),
    ).toBeNull();
    profiles = [{ ...profile, revision: 2 }, profiles[1]];
    view.rerender(<ModelSettings />);
    expect(screen.queryByText(/认证失败/)).toBeNull();
  });

  it("does not enable activation from older capability flags after this revision's failed test", () => {
    profiles = [
      {
        ...profile,
        capabilities: {
          ...profile.capabilities,
          endpoint_reachable: true,
          model_available: true,
          embedding_available: true,
        },
      },
    ];
    probes = {
      [profile.profile_id]: {
        profile_id: profile.profile_id,
        profile_revision: 1,
        capabilities: profile.capabilities,
        stages: [
          {
            stage: "authentication",
            ok: false,
            code: "unauthorized",
            latency_ms: 1,
          },
        ],
      },
    };
    render(<ModelSettings />);
    expect(screen.getByRole("button", { name: "设为当前模型" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(
      screen.getByRole("region", { name: "Saved provider 版本 1 测试结果" })
        .textContent,
    ).toContain("认证失败");
    expect(activate).not.toHaveBeenCalled();
  });

  it("distinguishes a configured but unreadable key without telling the user the whole config changed", () => {
    profiles = [
      {
        ...profile,
        has_api_key: false,
        has_configured_api_key: true,
        credential_state: "unavailable",
      },
    ];
    render(<ModelSettings />);
    expect(screen.getByText(/需要允许 MemHub 访问钥匙串/)).toBeTruthy();
    expect(screen.getByText(/始终允许/)).toBeTruthy();
    expect(screen.queryByText(/此版本尚不能读取钥匙串中的旧凭据/)).toBeNull();
    expect(screen.queryByText(/等待 key/)).toBeNull();
  });

  it("does not render a stored key and exposes explicit activation", async () => {
    profiles = [
      {
        ...profile,
        capabilities: {
          ...profile.capabilities,
          endpoint_reachable: true,
          model_available: true,
          embedding_available: true,
        },
      },
    ];
    const user = userEvent.setup();
    const { container } = render(<ModelSettings />);

    expect(container.textContent).not.toContain("secret-value");
    await user.click(screen.getByRole("button", { name: "设为当前模型" }));
    expect(activate).toHaveBeenCalledWith("profile-1");
  });

  it("hands a successful save to its card, clears only the key, and never tests or activates", async () => {
    profiles = [];
    const user = userEvent.setup();
    render(<ModelSettings />);
    await user.type(screen.getByLabelText("生成模型名"), "memory-model");
    await user.type(screen.getByLabelText(/^API Key/), "test-only-key");
    await user.click(screen.getByRole("button", { name: "保存配置" }));
    const card = screen.getByRole("article", { name: "已保存的模型配置" });
    expect(card.textContent).toContain("已保存，待测试");
    expect(card.textContent).toContain("models.example.com");
    expect(document.activeElement).toBe(card);
    expect(screen.queryByRole("button", { name: "保存配置" })).toBeNull();
    expect(probeProfile).not.toHaveBeenCalled();
    expect(activate).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "新增配置" }));
    expect(screen.getByLabelText(/^API Key/)).toHaveProperty("value", "");
    expect(create).toHaveBeenCalledTimes(1);
  });

  it("suggests an editable name without changing the endpoint or model", async () => {
    const user = userEvent.setup();
    render(<ModelSettings />);
    await user.type(screen.getByLabelText("生成模型名"), "model-v1");
    expect(screen.getByLabelText("配置名称")).toHaveProperty(
      "value",
      "OpenAI-compatible · model-v1",
    );
    await user.clear(screen.getByLabelText("配置名称"));
    await user.type(screen.getByLabelText("配置名称"), "My choice");
    await user.type(screen.getByLabelText("生成模型名"), "-v2");
    await user.clear(screen.getByLabelText("Base URL"));
    await user.type(
      screen.getByLabelText("Base URL"),
      "https://gateway.example/v1",
    );
    await user.selectOptions(screen.getByLabelText("协议"), "ollama");
    expect(screen.getByLabelText("配置名称")).toHaveProperty(
      "value",
      "My choice",
    );
    expect(screen.getByLabelText("生成模型名")).toHaveProperty(
      "value",
      "model-v1-v2",
    );
    expect(screen.getByLabelText("Base URL")).toHaveProperty(
      "value",
      "https://gateway.example/v1",
    );
  });

  it("shows field-local Chinese required errors for mouse and keyboard submission", async () => {
    const user = userEvent.setup();
    render(<ModelSettings />);
    await user.click(screen.getByRole("button", { name: "保存配置" }));
    expect(screen.getByText("请填写生成模型名。")).toBeTruthy();
    expect(screen.getByText("请填写 API Key。")).toBeTruthy();
    expect(
      screen.getByLabelText("生成模型名").getAttribute("aria-invalid"),
    ).toBe("true");
    expect(document.activeElement).toBe(screen.getByLabelText("生成模型名"));
    await user.keyboard("{Enter}");
    expect(create).not.toHaveBeenCalled();
  });

  it.each([
    "https://key.example/private-value",
    "www.key.example/private-value",
  ])(
    "rejects URL-shaped keys locally without echoing the value (%#)",
    async (key) => {
      const user = userEvent.setup();
      const { container } = render(<ModelSettings />);
      await user.type(screen.getByLabelText("生成模型名"), "model-v1");
      await user.type(screen.getByLabelText(/^API Key/), key);
      await user.click(screen.getByRole("button", { name: "保存配置" }));
      expect(
        screen.getByText(
          "这里需要 API Key，不是网址。请填写服务方提供的密钥。",
        ),
      ).toBeTruthy();
      expect(container.textContent).not.toContain(key);
      expect(screen.getByLabelText(/^API Key/)).toHaveProperty(
        "type",
        "password",
      );
      expect(screen.getByLabelText(/^API Key/)).toHaveProperty("value", key);
      expect(create).not.toHaveBeenCalled();
    },
  );

  it("preserves failed input and single-flights rapid submits", async () => {
    let resolve!: (value: ModelProfile | undefined) => void;
    create.mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const user = userEvent.setup();
    render(<ModelSettings />);
    await user.type(screen.getByLabelText("生成模型名"), "model-v1");
    await user.type(screen.getByLabelText(/^API Key/), "test-only-key");
    const form = screen
      .getByRole("button", { name: "保存配置" })
      .closest("form");
    if (!form) throw new Error("Missing model form");
    fireEvent.submit(form);
    fireEvent.submit(form);
    expect(create).toHaveBeenCalledTimes(1);
    await act(async () => resolve(undefined));
    expect(screen.getByLabelText("生成模型名")).toHaveProperty(
      "value",
      "model-v1",
    );
    expect(screen.getByLabelText(/^API Key/)).toHaveProperty(
      "value",
      "test-only-key",
    );
    await user.click(screen.getByRole("button", { name: "保存配置" }));
    expect(create).toHaveBeenCalledTimes(2);
  });

  it("rejects invalid endpoint input without changing it", async () => {
    const user = userEvent.setup();
    render(<ModelSettings />);
    await user.type(screen.getByLabelText("生成模型名"), "model-v1");
    await user.type(screen.getByLabelText(/^API Key/), "test-only-key");
    await user.clear(screen.getByLabelText("Base URL"));
    await user.type(screen.getByLabelText("Base URL"), "not-an-endpoint");
    await user.click(screen.getByRole("button", { name: "保存配置" }));
    expect(
      screen.getByText("请填写完整的 http:// 或 https:// 接口地址。"),
    ).toBeTruthy();
    expect(screen.getByLabelText("Base URL")).toHaveProperty(
      "value",
      "not-an-endpoint",
    );
    expect(create).not.toHaveBeenCalled();
  });

  it("exposes an advanced path to pair a remote generator with local Ollama embeddings", async () => {
    const user = userEvent.setup();
    render(<ModelSettings />);

    // 能力还在，只是收进了折叠区：默认看不见，展开才有。
    expect(screen.queryByLabelText("向量模型名")).toBeNull();
    await user.click(
      screen.getByRole("button", { name: "高级：自行配置向量模型" }),
    );
    await user.selectOptions(screen.getByLabelText("向量配置模式"), "custom");
    await user.type(
      screen.getByLabelText("配置名称"),
      "Remote with local embedding",
    );
    await user.type(screen.getByLabelText("生成模型名"), "remote-model");
    await user.type(screen.getByLabelText("向量模型名"), "nomic-embed-text");
    await user.selectOptions(screen.getByLabelText("向量模型来源"), "ollama");
    await user.type(
      screen.getByLabelText("向量模型 Base URL"),
      "http://127.0.0.1:11434",
    );
    await user.type(screen.getByLabelText(/^API Key/), "secret-value");
    await user.click(screen.getByRole("button", { name: "保存配置" }));

    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({
        embedding_provider_kind: "ollama",
        embedding_base_url: "http://127.0.0.1:11434",
      }),
    );
  });

  it("后端激活不了的协议根本不出现在下拉里", () => {
    render(<ModelSettings />);

    const providerSelect = screen.getByLabelText("协议") as HTMLSelectElement;
    const labels = Array.from(providerSelect.options).map(
      (option) => option.textContent ?? "",
    );

    expect(labels.some((text) => text.startsWith("OpenAI-compatible"))).toBe(
      true,
    );
    expect(labels.some((text) => text.startsWith("Ollama（本机）"))).toBe(true);

    // ModelProfileStore.activate() 对这两个 provider 抛
    // model_provider_not_supported。之前它们以 disabled 的形态留在下拉里，
    // 等于把两条死路摆在明面上让人去读懂括号里的「暂不支持」。
    expect(labels.some((text) => text.includes("Anthropic"))).toBe(false);
    expect(labels.some((text) => text.includes("HY Memory"))).toBe(false);
    expect(providerSelect.options.length).toBe(2);
  });

  it("已保存的配置不印内置向量模型", () => {
    profiles = [profile];
    render(<ModelSettings />);

    // Core 给每个配置都回填 bge-small-zh-v1.5，所以这行原本每条都在印一个
    // 用户既没选过也改不了的东西。
    expect(screen.queryByText(/bge-small-zh-v1\.5/)).toBeNull();
    expect(screen.getByText(/memory-model/)).toBeTruthy();
  });

  it("但用户自己配过的向量模型要印出来", () => {
    profiles = [
      {
        ...profile,
        embedding_model: "nomic-embed-text",
        embedding_provider_kind: "ollama",
        embedding_base_url: "http://127.0.0.1:11434",
      },
    ];
    render(<ModelSettings />);

    expect(screen.getByText(/nomic-embed-text/)).toBeTruthy();
  });

  it("有预置档案时，先给贴 key 的那条路，自定义表单收起来", () => {
    profiles = [preset];
    render(<ModelSettings />);

    expect(screen.getByRole("region", { name: "预配置模型服务" })).toBeTruthy();
    // 贴一个 key 就能用的时候，没理由让人先面对协议、Base URL 和超时。
    expect(screen.queryByLabelText("协议")).toBeNull();
    expect(screen.getByRole("button", { name: "自定义一个模型" })).toBeTruthy();
  });

  it("没有预置档案时表单直接摊开，不用多点一下", () => {
    profiles = [];
    render(<ModelSettings />);

    expect(screen.getByLabelText("协议")).toBeTruthy();
  });

  it("no longer promises an HY default model that will not arrive", () => {
    render(<ModelSettings />);
    expect(screen.queryByText(/尚未开放/)).toBeNull();
  });

  describe("预置档案", () => {
    it("只问一个 key，端点与模型名不摆在明面上", async () => {
      profiles = [preset];
      render(<ModelSettings />);

      const card = screen.getByRole("region", {
        name: "预配置模型服务",
      });
      expect(card.textContent).toContain("粘贴 API Key");
      // 用户不需要知道 Base URL 是什么，也不需要知道该写哪个模型名：
      // 这两件事摆出来只会让人以为自己还得做点什么。
      expect(card.textContent).not.toContain("models.example.com");
      expect(card.textContent).not.toContain("memory-model");
    });

    it("key 为空时说等待 key，不说配置错误", async () => {
      // 「等着你贴一个 key」和「你填错了」是两件事。混成一句，用户会去
      // 检查自己没填错的东西，而真正要做的那一步反倒没提。
      profiles = [preset];
      render(<ModelSettings />);

      const card = screen.getByRole("region", {
        name: "预配置模型服务",
      });
      expect(card.textContent).toContain("等待 key");
      expect(card.textContent).not.toContain("配置错误");
      expect(card.textContent).not.toContain("配置有误");
    });

    it("给出可点击的申请入口", async () => {
      profiles = [preset];
      render(<ModelSettings />);

      const link = screen.getByRole("link", { name: /申请/ });
      expect(link.getAttribute("href")).toBe("https://models.example.com");
    });

    it("粘上 key 就走，不必再填别的", async () => {
      profiles = [preset];
      const user = userEvent.setup();
      render(<ModelSettings />);

      await user.type(screen.getByLabelText("粘贴 API Key"), "preset-key");
      await user.click(screen.getByRole("button", { name: "保存 key" }));

      expect(update).toHaveBeenCalledWith("preset-1", {
        expected_revision: 1,
        api_key: "preset-key",
      });
      expect(probeProfile).not.toHaveBeenCalled();
      expect(activate).not.toHaveBeenCalled();
    });

    it("also rejects a URL pasted into a preset key without a request", async () => {
      profiles = [preset];
      const user = userEvent.setup();
      render(<ModelSettings />);
      await user.type(
        screen.getByLabelText("粘贴 API Key"),
        "https://keys.example/path",
      );
      await user.click(screen.getByRole("button", { name: "保存 key" }));
      expect(
        screen.getByText(
          "这里需要 API Key，不是网址。请填写服务方提供的密钥。",
        ),
      ).toBeTruthy();
      expect(update).not.toHaveBeenCalled();
    });

    it("预置档案在场也不让自定义变难", async () => {
      const user = userEvent.setup();
      profiles = [preset];
      render(<ModelSettings />);

      // 「必须先删掉预置才能新建」这类顺序依赖不许出现。表单现在默认收起，
      // 但收起不是顺序依赖：一次点开，不用先动预置分毫。
      await user.click(screen.getByRole("button", { name: "自定义一个模型" }));

      expect(screen.getByLabelText("配置名称")).toBeTruthy();
      expect(screen.getByRole("button", { name: "保存配置" })).toHaveProperty(
        "disabled",
        false,
      );
      expect(screen.getByRole("button", { name: "删除这张卡" })).toBeTruthy();
    });

    it("普通档案不会被当成预置来渲染", () => {
      // 断言的是「没有以它命名的预置卡片」，而不是「没有出现某句文案」：
      // 后者在这个档案已有 key 时压根不会出现，测过也测不出东西来。
      render(<ModelSettings />);

      expect(
        screen.queryByRole("region", { name: "Saved provider" }),
      ).toBeNull();
      expect(screen.getByRole("button", { name: "测试连接" })).toBeTruthy();
    });
  });

  it("reopens the form a pending probe refused, and does not re-send the test", async () => {
    const user = userEvent.setup();
    const controls = createRef<ModelSettingsActions>();
    pendingProbe = {
      operationId: "11111111-1111-1111-1111-111111111111",
      profileId: profile.profile_id,
      inputRevision: profile.revision,
    };
    profiles = [profile];
    probeProfile.mockImplementationOnce(async (profileId: string) => {
      // The backend reaches a terminal state and the journal entry is dropped.
      pendingProbe = null;
      return {
        profile_id: profileId,
        profile_revision: profile.revision,
        stages: [],
        capabilities: { ...profile.capabilities },
      };
    });
    render(<ProbeRecoveryHarness controls={controls} />);

    // The fence is untouched: opening the area did not loosen it.
    act(() => {
      expect(controls.current?.editProfile(profile)).toBe(false);
    });
    const nameField = () =>
      screen.getByLabelText("配置名称") as HTMLInputElement;
    expect(nameField().disabled).toBe(true);

    await user.click(screen.getByRole("button", { name: "核对测试结果" }));

    expect(probeProfile).toHaveBeenCalledExactlyOnceWith(profile.profile_id);
    // The refused edit is replayed once the fence clears, so the person lands
    // in the form they asked for instead of a blank one that invites a
    // duplicate. Waiting rather than asserting immediately: reconciliation is
    // an async handler plus a refresh, and settling is not instant.
    await waitFor(() => {
      expect(nameField().value).toBe(profile.name);
      expect(nameField().disabled).toBe(false);
    });
  });
});

describe("把一张预置卡设为当前模型", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
    // 粘完 key 之后的样子：key 有了，但从来没探测过，所以能力位全是假。
    profiles = [{ ...preset, has_api_key: true, revision: 2 }];
    probeProfile.mockResolvedValue({
      profile_id: "preset-1",
      profile_revision: 2,
      stages: [],
      capabilities: {
        endpoint_reachable: true,
        model_available: true,
        // A compatible provider may generate successfully without schema constraints.
        structured_output: false,
        embedding_available: true,
      },
    });
    activate.mockResolvedValue(undefined);
  });

  it("tests a preset only on the explicit test action and does not activate it", async () => {
    const user = userEvent.setup();
    render(<ModelSettings />);

    // Saving, testing and activation are separate user actions.
    await user.click(screen.getByRole("button", { name: "测试连接" }));

    expect(probeProfile).toHaveBeenCalledWith("preset-1");
    expect(activate).not.toHaveBeenCalled();
  });

  it("探测失败就不去启用，也不假装成功", async () => {
    const user = userEvent.setup();
    // 真实的 probeProfile 从不抛异常 —— 它把失败吞进 error 再返回 undefined。
    // 第一版这条测试用 mockRejectedValue 写，于是它守的是一个生产里不存在的
    // 行为。改成返回一张探测失败的卡，这才是真会发生的那件事。
    probeProfile.mockResolvedValue({
      profile_id: "preset-1",
      profile_revision: 2,
      stages: [],
      capabilities: {
        endpoint_reachable: false,
        model_available: false,
        structured_output: false,
        embedding_available: false,
      },
    });
    render(<ModelSettings />);

    await user.click(screen.getByRole("button", { name: "测试连接" }));

    // 正向对照：没有这条，上一条可以被「不管探测结果都启用」骗过。
    expect(probeProfile).toHaveBeenCalledWith("preset-1");
    expect(activate).not.toHaveBeenCalled();
  });

  it("余额不足时仍然能换到这份配置，只是不能据此启用生成", async () => {
    profiles = [
      {
        ...profiles[0],
        capabilities: {
          ...profile.capabilities,
          endpoint_reachable: true,
          model_available: true,
          embedding_available: true,
        },
      },
    ];
    const exhausted: ModelProbeResult = {
      profile_id: "preset-1",
      profile_revision: 2,
      stages: [
        {
          stage: "structured_output",
          ok: false,
          code: "insufficient_balance",
          latency_ms: 9,
        },
      ],
      capabilities: {
        endpoint_reachable: true,
        model_available: true,
        structured_output: false,
        embedding_available: true,
      },
    };
    probeProfile.mockResolvedValue(exhausted);
    probes = { "preset-1": exhausted };
    render(<ModelSettings />);
    const card = screen.getByRole("region", { name: preset.name });
    await userEvent.click(
      within(card).getByRole("button", { name: "测试连接" }),
    );
    expect(await screen.findByText(/余额不足/)).toBeTruthy();
    await userEvent.click(
      within(card).getByRole("button", { name: "设为当前模型" }),
    );
    expect(activate).toHaveBeenCalledWith("preset-1");
  });

  it("activates a tested preset only on the separate activation action", async () => {
    profiles = [
      {
        ...profiles[0],
        capabilities: {
          ...profile.capabilities,
          endpoint_reachable: true,
          model_available: true,
          embedding_available: true,
        },
      },
    ];
    render(<ModelSettings />);
    const card = screen.getByRole("region", { name: preset.name });
    await userEvent.click(
      within(card).getByRole("button", { name: "设为当前模型" }),
    );
    expect(activate).toHaveBeenCalledWith("preset-1");
    expect(probeProfile).not.toHaveBeenCalled();
  });
});

it("keeps the form open and explains a 401 under the key input instead of collapsing it", async () => {
  const user = userEvent.setup();
  activate.mockResolvedValue(undefined);
  probes = {};
  profiles = [];
  create.mockResolvedValue({ ...profile, profile_id: "created", revision: 1 });
  probeProfile.mockResolvedValue({
    profile_id: "created",
    profile_revision: 1,
    stages: [
      { stage: "model", ok: false, code: "unauthorized", latency_ms: 3 },
    ],
    capabilities: {
      endpoint_reachable: true,
      model_available: false,
      structured_output: false,
      embedding_available: true,
    },
  });
  render(<ModelSettings />);
  await user.type(screen.getByLabelText("配置名称"), "DeepSeek");
  await user.type(screen.getByLabelText("生成模型名"), "deepseek-flash");
  await user.type(screen.getByLabelText(/^API Key/), "wrong-key");
  await user.click(screen.getByRole("button", { name: "保存并测试启用" }));
  await act(async () => {});
  await act(async () => {});
  expect(await screen.findByLabelText(/^API Key/)).toBeTruthy();
  expect(
    screen.getByText(
      "服务商拒绝了这个 API Key（401）。请确认密钥属于当前填写的服务地址。",
    ),
  ).toBeTruthy();
  expect((screen.getByLabelText(/^API Key/) as HTMLInputElement).value).toBe(
    "wrong-key",
  );
  expect(screen.queryByText("已保存，连接通过")).toBeNull();
  expect(screen.queryByText("已保存，测试未通过")).toBeNull();
});
