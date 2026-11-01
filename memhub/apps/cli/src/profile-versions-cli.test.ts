import { beforeEach, describe, expect, it, vi } from "vitest";

import { run } from "./cli.js";

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

const VERSIONS = {
  user_id: "wife",
  request_id: "r-versions",
  elapsed_ms: 2,
  chains: [
    {
      memory_id: "l0-head",
      content: "The user's name is Fei.",
      status: "active",
      memory_at: null,
      gmt_created: 1_790_000_100,
      basic_info: { name: "Fei" },
      versions: [
        {
          memory_id: "l0-head",
          content: "The user's name is Fei.",
          memory_at: null,
          gmt_created: 1_790_000_100,
          current: true,
        },
        {
          memory_id: "l0-old",
          content: "The user's name is 费天翔.",
          memory_at: null,
          gmt_created: 1_790_000_000,
          current: false,
        },
      ],
    },
  ],
};

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
    url === "/api/v2/user-profile/versions"
      ? { status: 200, data: VERSIONS }
      : { status: 404, data: {} },
  );
});

describe("memhub profile --versions", () => {
  it("reads the L0 version chain for the named user", async () => {
    const result = await capturedRun([
      "profile",
      "--versions",
      "--user-id",
      "wife",
      "--json",
    ]);
    expect(result.code).toBe(0);
    expect(transport.get).toHaveBeenCalledWith(
      "/api/v2/user-profile/versions",
      { params: { user_id: "wife" } },
    );
    const document = JSON.parse(result.stdout);
    expect(document.state).toBe("ok");
    expect(document.chains[0].versions).toHaveLength(2);
  });

  it("marks the current version in text and names no user for local", async () => {
    const result = await capturedRun(["profile", "--versions"]);
    expect(result.code).toBe(0);
    const [url, config] = transport.get.mock.calls[0] ?? [];
    expect(url).toBe("/api/v2/user-profile/versions");
    expect(config?.params?.user_id).toBeUndefined();
    expect(result.stdout).toContain("* l0-head");
    expect(result.stdout).toContain("  l0-old");
    expect(result.stdout).toContain("费天翔");
  });

  it("refuses an unknown flag and a repeated --versions", async () => {
    expect((await capturedRun(["profile", "--version-chain"])).code).toBe(2);
    expect(
      (await capturedRun(["profile", "--versions", "--versions"])).code,
    ).toBe(2);
    expect(transport.get).not.toHaveBeenCalled();
  });

  it("reports a malformed answer as invalid", async () => {
    transport.get.mockResolvedValue({ status: 200, data: { chains: "x" } });
    const result = await capturedRun(["profile", "--versions", "--json"]);
    expect(result.code).toBe(1);
    expect(JSON.parse(result.stdout).code).toBe("response_invalid");
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
      code: await run(arguments_, {
        ONE_MEMORY_DATA_DIR: "/synthetic/data",
      }),
      stdout,
      stderr,
    };
  } finally {
    stdoutSpy.mockRestore();
    stderrSpy.mockRestore();
  }
}
