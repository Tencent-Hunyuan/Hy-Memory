import { beforeEach, describe, expect, it, vi } from "vitest";

import { run } from "./cli.js";
import { parseLanguageCommand } from "./memory-language-flow.js";
import { composeStatusReport, renderStatusText } from "./status-report.js";

const transport = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}));
vi.mock("axios", () => ({
  default: {
    create: vi.fn(() => transport),
    get: vi.fn(),
    post: vi.fn(),
    isAxiosError: () => false,
  },
}));

const runtime = vi.hoisted(() => ({
  inspectRuntimeStatus: vi.fn(),
  discoverRuntimeAttachment: vi.fn(),
}));
vi.mock("@one-memory/runtime-control", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@one-memory/runtime-control")>()),
  ...runtime,
}));

function languageState(uiLanguage: string | null, extra = {}) {
  const sdk = uiLanguage === null ? "auto" : uiLanguage;
  return {
    ui_language: uiLanguage,
    sdk_language: sdk,
    languages: [
      "en",
      "zh-Hans",
      "zh-Hant",
      "ja",
      "es",
      "de",
      "pt",
      "fr",
      "ko",
      "ru",
      "id",
    ],
    ...extra,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  for (const method of Object.values(transport)) method.mockReset();
  runtime.inspectRuntimeStatus.mockResolvedValue({
    state: "running",
    code: "runtime_verified",
    instanceId: "instance-1",
    host: "127.0.0.1",
    port: 8765,
    version: "1.2.3",
  });
  runtime.discoverRuntimeAttachment.mockResolvedValue({
    instanceId: "instance-1",
    apiToken: "synthetic-runtime-token",
    host: "127.0.0.1",
    port: 8765,
    servicePid: 4242,
    health: {
      status: "ok",
      service: "one-memory-core",
      version: "1.2.3",
      instance_id: "instance-1",
    },
  });
  transport.get.mockImplementation(async (url: string) =>
    url === "/api/v1/settings/ui-language"
      ? { status: 200, data: languageState(null) }
      : { status: 404, data: {} },
  );
});

describe("memhub settings language", () => {
  it("shows the display language and the SDK prompt language", async () => {
    const result = await capturedRun(["settings", "language", "--json"]);
    expect(result.code).toBe(0);
    const json = JSON.parse(result.stdout);
    expect(json).toMatchObject({
      command: "settings language",
      state: "ok",
      language: { ui_language: null, sdk_language: "auto" },
    });
    expect(transport.put).not.toHaveBeenCalled();
    const text = (await capturedRun(["settings", "language"])).stdout;
    expect(text).toContain("界面语言：未设置（SDK 自动识别）");
    expect(text).toContain("SDK 提示语言：auto");
  });

  it("sets a language at once and reports the SDK language", async () => {
    transport.put.mockResolvedValue({
      status: 200,
      data: languageState("ja", { rebuilt: true }),
    });
    const result = await capturedRun(["settings", "language", "ja", "--json"]);
    expect(result.code).toBe(0);
    expect(transport.put).toHaveBeenCalledWith(
      "/api/v1/settings/ui-language",
      { ui_language: "ja" },
      undefined,
    );
    expect(JSON.parse(result.stdout)).toMatchObject({
      state: "committed",
      rebuilt: true,
      language: { ui_language: "ja", sdk_language: "ja" },
    });
    const text = (await capturedRun(["settings", "language", "ja"])).stdout;
    expect(text).toContain("界面语言：ja（日本語）");
  });

  it("sends null for system and maps the legacy zh-CN code", async () => {
    transport.put.mockResolvedValue({
      status: 200,
      data: languageState("zh-Hans", { rebuilt: false }),
    });
    await capturedRun(["settings", "language", "zh-CN"]);
    expect(transport.put).toHaveBeenLastCalledWith(
      "/api/v1/settings/ui-language",
      { ui_language: "zh-Hans" },
      undefined,
    );
    transport.get.mockResolvedValue({
      status: 200,
      data: languageState("en"),
    });
    transport.put.mockResolvedValue({
      status: 200,
      data: languageState(null, { rebuilt: true }),
    });
    await capturedRun(["settings", "language", "system"]);
    expect(transport.put).toHaveBeenLastCalledWith(
      "/api/v1/settings/ui-language",
      { ui_language: null },
      undefined,
    );
  });

  it("does not write a language Core already has", async () => {
    const result = await capturedRun(["settings", "language", "system"]);
    expect(result.code).toBe(0);
    expect(
      JSON.parse(
        (await capturedRun(["settings", "language", "system", "--json"]))
          .stdout,
      ).state,
    ).toBe("unchanged");
    expect(transport.put).not.toHaveBeenCalled();
  });

  it("refuses unknown codes before the Runtime", async () => {
    for (const bad of [
      ["settings", "language", "it"],
      ["settings", "language", "EN"],
      ["settings", "language", "ja", "ko"],
    ]) {
      expect((await capturedRun(bad)).code, bad.join(" ")).toBe(2);
    }
    expect(transport.put).not.toHaveBeenCalled();
  });

  it("parses the forms the help shows", () => {
    expect(parseLanguageCommand([])).toEqual({ kind: "show" });
    expect(parseLanguageCommand(["system"])).toEqual({
      kind: "set",
      language: null,
    });
    expect(parseLanguageCommand(["zh-Hant", "--json"])).toEqual({
      kind: "set",
      language: "zh-Hant",
    });
  });
});

describe("status language", () => {
  it.each([
    [null, "auto", "跟随系统", "auto（自动识别中/英）"],
    ["ja", "ja", "ja", "SDK 提示语言 ja"],
  ])(
    "shows the UI language %s and the SDK language %s",
    async (uiLanguage, sdkLanguage, uiText, sdkText) => {
      const processing = {
        pending: 0,
        completed: 0,
        failed: 0,
        blocked_stages: [],
        pipeline: {
          mode: "pro",
          configured_mode: "pro",
          forced_by_env: false,
          last_evolve_at: null,
          graph_build: { state: "idle", users: {} },
          evolve: { mode: "pro", schedule: null, running: [], users: {} },
          features: {},
          ui_language: uiLanguage,
          sdk_language: sdkLanguage,
        },
      };
      const report = await composeStatusReport({
        dataDir: "/tmp/isolated",
        runtime: { state: "running", code: "ok", host: "127.0.0.1", port: 1 },
        background: null,
        reader: (async (path: string) =>
          path === "/api/v1/memory-processing"
            ? { state: "ok", data: processing }
            : { state: "unavailable", code: "x" }) as never,
      });
      expect(report.pipeline?.ui_language).toBe(uiLanguage);
      expect(report.pipeline?.sdk_language).toBe(sdkLanguage);
      const text = renderStatusText(report, "MemHub");
      expect(text).toContain(uiText);
      expect(text).toContain(sdkText);
    },
  );

  it("reads an older Runtime without the setting as auto", async () => {
    const report = await composeStatusReport({
      dataDir: "/tmp/isolated",
      runtime: { state: "running", code: "ok", host: "127.0.0.1", port: 1 },
      background: null,
      reader: (async () => ({
        state: "ok",
        data: {
          pipeline: {
            mode: "pro",
            configured_mode: "pro",
            evolve: {},
            graph_build: {},
          },
        },
      })) as never,
    });
    expect(report.pipeline?.sdk_language).toBe("auto");
    expect(report.pipeline?.ui_language).toBeNull();
  });
});

async function capturedRun(arguments_: string[]) {
  let stdout = "";
  let stderr = "";
  const stdoutSpy = vi
    .spyOn(process.stdout, "write")
    .mockImplementation((chunk) => {
      stdout += String(chunk);
      return true;
    });
  const stderrSpy = vi
    .spyOn(process.stderr, "write")
    .mockImplementation((chunk) => {
      stderr += String(chunk);
      return true;
    });
  try {
    return {
      code: await run(arguments_, { ONE_MEMORY_DATA_DIR: "/synthetic/data" }),
      stdout,
      stderr,
    };
  } finally {
    stdoutSpy.mockRestore();
    stderrSpy.mockRestore();
  }
}
