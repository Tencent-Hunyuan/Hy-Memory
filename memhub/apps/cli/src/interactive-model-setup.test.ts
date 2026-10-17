import { describe, expect, it, vi } from "vitest";

import type { CoreReader } from "./core-client.js";
import type { CoreOperations } from "./core-operations.js";
import type { WizardPrompter } from "./init-wizard.js";
import { runInteractiveModelSetup } from "./interactive-model-setup.js";

const PROFILE_ID = "22222222-2222-4222-8222-222222222222";

function profile(active = false) {
  return {
    profile_id: PROFILE_ID,
    name: "DeepSeek",
    provider_kind: "openai_compatible",
    base_url: "https://api.deepseek.com",
    model: "deepseek-flash",
    embedding_model: "bge-small-zh-v1.5",
    embedding_provider_kind: "local_onnx",
    embedding_base_url: null,
    embedding_dimensions: 512,
    has_api_key: true,
    credential_state: "available",
    timeout_seconds: 90,
    max_concurrency: 4,
    context_window: null,
    disable_thinking: true,
    capabilities: {
      endpoint_reachable: true,
      model_available: true,
      structured_output: true,
      embedding_available: true,
    },
    revision: 1,
    is_active: active,
    created_at: "2026-10-07T00:00:00Z",
    updated_at: "2026-10-07T00:00:00Z",
  } as const;
}

function prompter(lines: string[], secret = "sk-do-not-print") {
  let index = 0;
  const written: string[] = [];
  return {
    written,
    value: {
      async ask() {
        const value = lines[index++];
        if (value === undefined)
          throw new Error(
            `script exhausted after ${index - 1}: ${written.join(" | ")}`,
          );
        return value;
      },
      async askSecret() {
        return secret;
      },
      write(line: string) {
        written.push(line);
      },
    } satisfies WizardPrompter,
  };
}

function clients(options: { failedProbes?: number } = {}) {
  let failedProbes = options.failedProbes ?? 0;
  const reader = vi.fn(async (path: string) => {
    if (path === "/api/v1/model-profiles") {
      return { state: "ok", data: [profile(true)] };
    }
    if (path.startsWith("/api/v1/model-probe-operations/")) {
      return {
        state: "ok",
        data: {
          schema_version: 1,
          operation_id: path.split("/").at(-1),
          profile_id: null,
          expected_profile_revision: null,
          state: "not_found",
          result: null,
          code: null,
          stale: null,
        },
      };
    }
    return { state: "not_found", code: "http_404" };
  });
  type PostResult =
    | { state: "committed"; data: unknown }
    | { state: "rejected"; code: string; http_status: number };
  const post = vi.fn(
    async (
      path: string,
      body: Record<string, unknown>,
    ): Promise<PostResult> => {
      if (path === "/api/v1/model-profiles") {
        return { state: "committed" as const, data: profile(false) };
      }
      if (path.endsWith("/probe")) {
        const probeOk = failedProbes === 0;
        failedProbes = Math.max(0, failedProbes - 1);
        return {
          state: "committed" as const,
          data: {
            schema_version: 1,
            operation_id: body.operation_id,
            profile_id: PROFILE_ID,
            expected_profile_revision: 1,
            state: "completed",
            result: {
              profile_id: PROFILE_ID,
              profile_revision: 1,
              input_profile_revision: 1,
              stages: [
                {
                  stage: "model",
                  ok: probeOk,
                  code: probeOk ? "ok" : "unauthorized",
                  latency_ms: 1,
                  embedding_dimensions: null,
                },
              ],
              capabilities: {
                ...profile().capabilities,
                model_available: probeOk,
              },
            },
            code: null,
            stale: false,
          },
        };
      }
      if (path.endsWith("/activate")) {
        return { state: "committed" as const, data: profile(true) };
      }
      return {
        state: "rejected" as const,
        code: "unexpected_path",
        http_status: 404,
      };
    },
  );
  const remove = vi.fn().mockResolvedValue({
    state: "committed" as const,
    data: { removed: true },
  });
  return {
    reader: reader as unknown as CoreReader,
    operations: { post, delete: remove } as CoreOperations & {
      delete: typeof remove;
    },
    post,
    remove,
  };
}

describe("interactive model setup", () => {
  it("uses App defaults and keeps the key out of output", async () => {
    const prompt = prompter(["", "", ""]);
    const api = clients();

    const result = await runInteractiveModelSetup(prompt.value, api, {
      dataDir: "/private/tmp/memhub-interactive-test/data",
      hostHome: "/private/tmp/memhub-interactive-test/home",
      installHosts: false,
    });

    expect(result).toMatchObject({ state: "configured" });
    expect(api.post).toHaveBeenCalledWith(
      "/api/v1/model-profiles",
      expect.objectContaining({
        name: "DeepSeek",
        base_url: "https://api.deepseek.com",
        model: "deepseek-flash",
        api_key: "sk-do-not-print",
        embedding_provider_kind: "local_onnx",
      }),
    );
    expect(api.post).not.toHaveBeenCalledWith(
      "/api/v1/model-content-permission",
      expect.anything(),
    );
    expect(prompt.written.join("\n")).not.toContain("sk-do-not-print");
  });

  it("can skip model setup without writing anything", async () => {
    const prompt = prompter(["0"]);
    const api = clients();

    await expect(
      runInteractiveModelSetup(prompt.value, api, {
        dataDir: "/private/tmp/memhub-interactive-test/data",
        hostHome: "/private/tmp/memhub-interactive-test/home",
        installHosts: false,
      }),
    ).resolves.toEqual({ state: "skipped" });
    expect(api.post).not.toHaveBeenCalled();
  });

  it("removes an unverified profile before skipping after a failed probe", async () => {
    const prompt = prompter(["", "", "", "0"]);
    const api = clients();
    api.post.mockImplementation(
      async (path: string, body: Record<string, unknown>) => {
        if (path === "/api/v1/model-profiles") {
          return { state: "committed" as const, data: profile(false) };
        }
        if (path.endsWith("/probe")) {
          return {
            state: "committed" as const,
            data: {
              schema_version: 1,
              operation_id: body.operation_id,
              profile_id: PROFILE_ID,
              expected_profile_revision: 1,
              state: "completed",
              result: {
                profile_id: PROFILE_ID,
                profile_revision: 1,
                input_profile_revision: 1,
                stages: [
                  {
                    stage: "authentication",
                    ok: false,
                    code: "unauthorized",
                    latency_ms: 1,
                    embedding_dimensions: null,
                  },
                ],
                capabilities: {
                  ...profile().capabilities,
                  model_available: false,
                },
              },
              code: null,
              stale: false,
            },
          };
        }
        return {
          state: "rejected" as const,
          code: "unexpected_path",
          http_status: 404,
        };
      },
    );

    await expect(
      runInteractiveModelSetup(prompt.value, api, {
        dataDir: "/private/tmp/memhub-interactive-test/data",
        hostHome: "/private/tmp/memhub-interactive-test/home",
        installHosts: false,
      }),
    ).resolves.toEqual({ state: "skipped" });
    expect(api.remove).toHaveBeenCalledWith(
      `/api/v1/model-profiles/${PROFILE_ID}`,
    );
    expect(prompt.written.join("\n")).not.toContain("sk-do-not-print");
  });

  it("returns to provider selection before any write", async () => {
    const prompt = prompter(["", "back", "0"]);
    const api = clients();

    await expect(
      runInteractiveModelSetup(prompt.value, api, {
        dataDir: "/private/tmp/memhub-interactive-test/data",
        hostHome: "/private/tmp/memhub-interactive-test/home",
        installHosts: false,
      }),
    ).resolves.toEqual({ state: "skipped" });
    expect(api.post).not.toHaveBeenCalled();
  });

  it("cleans a failed probe and lets the user re-enter the model settings", async () => {
    const prompt = prompter(["", "", "", "", "", "", ""]);
    const api = clients({ failedProbes: 1 });

    const result = await runInteractiveModelSetup(prompt.value, api, {
      dataDir: "/private/tmp/memhub-interactive-test/data",
      hostHome: "/private/tmp/memhub-interactive-test/home",
      installHosts: false,
    });

    expect(result).toMatchObject({ state: "configured" });
    expect(api.remove).toHaveBeenCalledTimes(1);
    expect(
      api.post.mock.calls.filter(([path]) => path === "/api/v1/model-profiles"),
    ).toHaveLength(2);
    expect(prompt.written.join("\n")).not.toContain("sk-do-not-print");
  });

  it("leaves no profile when the user interrupts during prompts", async () => {
    let asked = 0;
    const prompt = prompter(["", "", ""]);
    prompt.value.ask = async () => {
      asked += 1;
      if (asked === 3) throw new Error("interrupted");
      return "";
    };
    const api = clients();

    await expect(
      runInteractiveModelSetup(prompt.value, api, {
        dataDir: "/private/tmp/memhub-interactive-test/data",
        hostHome: "/private/tmp/memhub-interactive-test/home",
        installHosts: false,
      }),
    ).rejects.toThrow("interrupted");
    expect(api.post).not.toHaveBeenCalled();
    expect(api.remove).not.toHaveBeenCalled();
  });

  it("installs selected detected hosts through preview and fingerprint apply", async () => {
    const prompt = prompter(["", "", "", "", ""]);
    const api = clients();
    const read = vi
      .fn()
      .mockResolvedValueOnce({
        schema_version: 1,
        product: "memhub",
        command: "preview",
        host_id: "codex",
        operation: "previewInstall",
        result: { fingerprint: "a".repeat(64), files: [] },
      })
      .mockResolvedValueOnce({
        schema_version: 1,
        product: "memhub",
        command: "apply",
        host_id: "codex",
        operation: "install",
        result: { installed: true },
      });

    const result = await runInteractiveModelSetup(prompt.value, api, {
      dataDir: "/private/tmp/memhub-interactive-test/data",
      hostHome: "/private/tmp/memhub-interactive-test/home",
      installHosts: true,
      hostApi: {
        list: vi.fn().mockResolvedValue({
          schema_version: 1,
          product: "memhub",
          command: "list",
          hosts: [{ host_id: "codex", label: "Codex", present: true }],
        }),
        read,
      },
    });

    expect(result).toMatchObject({
      state: "configured",
      installed_hosts: ["codex"],
    });
    expect(read).toHaveBeenNthCalledWith(
      2,
      "/private/tmp/memhub-interactive-test/data",
      "codex",
      "install",
      ["a".repeat(64)],
    );
  });

  it("lists and installs only hosts that are actually present", async () => {
    const prompt = prompter(["", "", "", "", ""]);
    const api = clients();
    const read = vi
      .fn()
      .mockResolvedValueOnce({
        schema_version: 1,
        product: "memhub",
        command: "preview",
        host_id: "cursor",
        operation: "previewInstall",
        result: { fingerprint: "b".repeat(64), files: [] },
      })
      .mockResolvedValueOnce({
        schema_version: 1,
        product: "memhub",
        command: "apply",
        host_id: "cursor",
        operation: "install",
        result: { installed: true },
      });

    const result = await runInteractiveModelSetup(prompt.value, api, {
      dataDir: "/private/tmp/memhub-interactive-test/data",
      hostHome: "/private/tmp/memhub-interactive-test/home",
      installHosts: true,
      hostApi: {
        list: vi.fn().mockResolvedValue({
          schema_version: 1,
          product: "memhub",
          command: "list",
          hosts: [
            { host_id: "cursor", label: "Cursor", present: true },
            { host_id: "opencode", label: "OpenCode", present: false },
          ],
        }),
        read,
      },
    });

    expect(result).toMatchObject({ installed_hosts: ["cursor"] });
    expect(prompt.written.join("\n")).toContain("Cursor");
    expect(prompt.written.join("\n")).not.toContain("OpenCode");
    expect(read).toHaveBeenCalledTimes(2);
    expect(read.mock.calls.every((call) => call[1] === "cursor")).toBe(true);
  });

  it("completes the wizard and keeps onboarding reachable when one host install fails", async () => {
    const prompt = prompter(["", "", "", "", ""]);
    const api = clients();
    const read = vi
      .fn()
      // Codex previews and installs fine.
      .mockResolvedValueOnce({
        schema_version: 1,
        product: "memhub",
        command: "preview",
        host_id: "codex",
        operation: "previewInstall",
        result: { fingerprint: "a".repeat(64), files: [] },
      })
      .mockResolvedValueOnce({
        schema_version: 1,
        product: "memhub",
        command: "apply",
        host_id: "codex",
        operation: "install",
        result: { installed: true },
      })
      // OpenClaw is listed by `host list` but rejected by the connection
      // client, so the install throws instead of returning a receipt.
      .mockRejectedValueOnce(new Error("connection_client_invalid_input"));

    const result = await runInteractiveModelSetup(prompt.value, api, {
      dataDir: "/private/tmp/memhub-interactive-test/data",
      hostHome: "/private/tmp/memhub-interactive-test/home",
      installHosts: true,
      hostApi: {
        list: vi.fn().mockResolvedValue({
          schema_version: 1,
          product: "memhub",
          command: "list",
          hosts: [
            { host_id: "codex", label: "Codex", present: true },
            { host_id: "openclaw", label: "OpenClaw", present: true },
          ],
        }),
        read,
      },
    });

    // The wizard must finish: the other host is still installed and the
    // failure is summarised for the user instead of aborting the run. OpenClaw
    // is App-only, so the summary points the user at the App instead of
    // calling the install a failure.
    expect(result).toMatchObject({
      state: "configured",
      installed_hosts: ["codex"],
    });
    const summary = prompt.written.join("\n");
    expect(summary).toContain("请在 App 里接入：OpenClaw");
    expect(summary).not.toContain("接入失败：OpenClaw");
    expect(read).toHaveBeenCalledTimes(3);
  });

  it("tells the user to install App-only hosts in the App instead of calling them failed", async () => {
    const prompt = prompter(["", "", "", "", ""]);
    const api = clients();
    const read = vi
      .fn()
      // The connection client refuses OpenClaw before any plan is produced,
      // so the wizard sees the same `connection_client_*` throw as before.
      .mockRejectedValueOnce(new Error("connection_client_invalid_input"));

    const result = await runInteractiveModelSetup(prompt.value, api, {
      dataDir: "/private/tmp/memhub-interactive-test/data",
      hostHome: "/private/tmp/memhub-interactive-test/home",
      installHosts: true,
      hostApi: {
        list: vi.fn().mockResolvedValue({
          schema_version: 1,
          product: "memhub",
          command: "list",
          hosts: [{ host_id: "openclaw", label: "OpenClaw", present: true }],
        }),
        read,
      },
    });

    // The wizard still finishes; the only host the user picked is the one
    // only the App can install, so nothing counts as "installed".
    expect(result).toMatchObject({
      state: "configured",
      installed_hosts: [],
    });
    const summary = prompt.written.join("\n");
    expect(summary).toContain("请在 App 里接入：OpenClaw");
    expect(summary).not.toContain("接入失败：OpenClaw");
  });
});
