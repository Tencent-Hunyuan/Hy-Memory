import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import * as filesystem from "node:fs/promises";
import {
  chmod,
  mkdir,
  mkdtemp,
  readdir,
  readFile,
  rm,
  stat,
  symlink,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { inspect, promisify } from "node:util";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HostGrantStore } from "./host-grants.js";

vi.mock("node:fs/promises", async (importOriginal) => ({
  ...(await importOriginal<typeof import("node:fs/promises")>()),
}));

const roots: string[] = [];
const workspace = `workspace:v1:${"a".repeat(64)}`;
const otherWorkspace = `workspace:v1:${"b".repeat(64)}`;
const runFile = promisify(execFile);

afterEach(async () => {
  vi.restoreAllMocks();
  await Promise.all(
    roots.splice(0).map((root) => rm(root, { recursive: true })),
  );
});

async function fixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), "om-host-grants-"));
  roots.push(root);
  const directory = path.join(root, "grants");
  const store = await HostGrantStore.initialize(directory);
  return {
    root,
    directory,
    file: path.join(directory, "host-grants.json"),
    store,
  };
}

async function approved() {
  const context = await fixture();
  const issued = await context.store.createInstallation({ hostId: "cursor" });
  const receipt = await context.store.approveGrants({
    installationId: issued.receipt.installationId,
    expectedRevision: issued.receipt.revision,
    allowedTools: ["om_recall", "om_ask", "om_conversations"],
    allowedScopes: ["all-apps", workspace],
  });
  return { ...context, issued, receipt };
}

const invalidScopes: unknown[] = [
  undefined,
  null,
  [],
  [""],
  [" "],
  ["*"],
  ["workspace:*"],
  ["private"],
  ["private:local"],
  ["ALL-APPS"],
  [" all-apps"],
  ["all-apps\n"],
  ["workspace:v1:abc"],
  ["unscoped-conversation:123"],
  ["all-apps", "*"],
  ["all-apps", 42],
  "all-apps",
];

describe.skipIf(process.platform === "win32")("HostGrantStore", () => {
  it("creates distinct install-only identities without any read authority", async () => {
    const { store } = await fixture();
    const first = await store.createInstallation({ hostId: "cursor" });
    const second = await store.createInstallation({ hostId: "cursor" });
    expect(first.receipt).toMatchObject({
      hostId: "cursor",
      revision: 1,
      status: "install-only",
      allowedTools: [],
      allowedScopes: [],
    });
    expect(first.receipt.installationId).not.toBe(
      second.receipt.installationId,
    );
    expect(first.secret).not.toBe(second.secret);
    expect(first.secret).toMatch(/^omhg_v1_[A-Za-z0-9_-]{43}$/);
    await expect(store.authenticate(first.secret)).rejects.toMatchObject({
      code: "host_grant_not_approved",
    });
    await expect(
      store.authorize(first.secret, {
        tool: "om_recall",
        scopes: ["all-apps"],
      }),
    ).rejects.toMatchObject({ code: "host_grant_not_approved" });
    await expect(
      store.createInstallation({
        hostId: "cursor",
        allowedScopes: ["all-apps"],
      } as never),
    ).rejects.toMatchObject({ code: "host_grant_invalid_input" });
    expect(await store.listInstallations()).toHaveLength(2);
  });

  it("returns verified identity and checks each stable tool and exact scope", async () => {
    const { store, issued, receipt } = await approved();
    expect(await store.authenticate(issued.secret)).toMatchObject({
      ...receipt,
      storeRevision: 2,
    });
    for (const tool of ["om_recall", "om_ask", "om_conversations"]) {
      expect(
        await store.authorize(issued.secret, {
          tool,
          scopes: ["all-apps", workspace],
        }),
      ).toMatchObject({
        installationId: receipt.installationId,
        hostId: "cursor",
      });
    }
    await expect(
      store.authorize(issued.secret, {
        tool: "om_matters",
        scopes: ["all-apps"],
      }),
    ).rejects.toMatchObject({ code: "host_grant_tool_denied" });
    await expect(
      store.authorize(issued.secret, { tool: "recall", scopes: ["all-apps"] }),
    ).rejects.toMatchObject({ code: "host_grant_tool_denied" });
    await expect(
      store.authorize(issued.secret, {
        tool: "om_recall",
        scopes: [otherWorkspace],
      }),
    ).rejects.toMatchObject({ code: "host_grant_scope_denied" });
    await expect(
      store.authorize(issued.secret, {
        tool: "om_recall",
        scopes: [workspace, otherWorkspace],
      }),
    ).rejects.toMatchObject({ code: "host_grant_scope_denied" });
    const matters = await store.approveGrants({
      installationId: receipt.installationId,
      expectedRevision: receipt.revision,
      allowedTools: ["om_matters"],
      allowedScopes: [workspace],
    });
    expect(matters.revision).toBe(3);
    await expect(
      store.authorize(issued.secret, {
        tool: "om_matters",
        scopes: [workspace],
      }),
    ).resolves.toMatchObject({ allowedTools: ["om_matters"] });
    await expect(
      store.authorize(issued.secret, {
        tool: "om_recall",
        scopes: [workspace],
      }),
    ).rejects.toMatchObject({ code: "host_grant_tool_denied" });
  });

  it.each([
    "cursor",
    "claude-code",
    "codex",
    "workbuddy",
    "0",
    "a-",
    "a".repeat(128),
  ])(
    "persists an explicitly approved source ID %s across restart",
    async (sourceId) => {
      const { store, directory, issued, receipt } = await approved();
      const sourceScope = `source:${sourceId}`;
      const allowedScopes = ["all-apps", workspace, sourceScope];
      const updated = await store.approveGrants({
        installationId: receipt.installationId,
        expectedRevision: receipt.revision,
        allowedTools: ["om_conversations", "om_recall", "om_ask"],
        allowedScopes,
      });
      const restarted = await HostGrantStore.open(directory);
      expect(
        (await restarted.authenticate(issued.secret)).allowedScopes,
      ).toEqual(allowedScopes);
      await expect(
        restarted.authorize(issued.secret, {
          tool: "om_conversations",
          scopes: ["all-apps", sourceScope],
        }),
      ).resolves.toMatchObject(updated);
      for (const tool of ["om_recall", "om_ask"]) {
        await expect(
          restarted.authorize(issued.secret, {
            tool,
            scopes: ["all-apps", workspace],
          }),
        ).resolves.toMatchObject(updated);
        await expect(
          restarted.authorize(issued.secret, {
            tool,
            scopes: [otherWorkspace],
          }),
        ).rejects.toMatchObject({ code: "host_grant_scope_denied" });
      }
    },
  );

  it.each([
    "source:",
    "source:*",
    "source:cursor*",
    "source:-cursor",
    "source:Cursor",
    "Source:cursor",
    "source:claude_code",
    "source:cursor/other",
    "source:cursor:other",
    "source:../cursor",
    "source:cursor,workbuddy",
    " source:cursor",
    "source:cursor ",
    "source:cursor\n",
    "source:cursor\r",
    "source:cursor\u2028",
    "source:cursor\0",
    "source:curs\u00f6r",
    `source:${"a".repeat(129)}`,
  ])(
    "rejects malformed source scope %j in approvals, requests, and saved grants",
    async (sourceScope) => {
      const { store, directory, file, issued, receipt } = await approved();
      const before = await readFile(file, "utf8");
      await expect(
        store.approveGrants({
          installationId: receipt.installationId,
          expectedRevision: receipt.revision,
          allowedTools: ["om_conversations"],
          allowedScopes: ["all-apps", sourceScope],
        }),
      ).rejects.toMatchObject({ code: "host_grant_invalid_input" });
      await expect(
        store.authorize(issued.secret, {
          tool: "om_conversations",
          scopes: ["all-apps", sourceScope],
        }),
      ).rejects.toMatchObject({ code: "host_grant_scope_denied" });
      expect(await readFile(file, "utf8")).toBe(before);
      const corrupt = JSON.parse(before);
      corrupt.installations[0].allowedScopes.push(sourceScope);
      await writeFile(file, JSON.stringify(corrupt));
      await expect(HostGrantStore.open(directory)).rejects.toMatchObject({
        code: "host_grant_store_corrupt",
      });
    },
  );

  it("never derives source or tool grants from all-apps or a host label", async () => {
    const { store, issued, receipt } = await approved();
    await expect(
      store.authorize(issued.secret, {
        tool: "om_conversations",
        scopes: ["all-apps", "source:cursor"],
      }),
    ).rejects.toMatchObject({ code: "host_grant_scope_denied" });
    const updated = await store.approveGrants({
      installationId: receipt.installationId,
      expectedRevision: receipt.revision,
      allowedTools: ["om_recall"],
      allowedScopes: ["all-apps", "source:cursor"],
    });
    await expect(
      store.authorize(issued.secret, {
        tool: "om_conversations",
        scopes: ["all-apps", "source:cursor"],
      }),
    ).rejects.toMatchObject({ code: "host_grant_tool_denied" });
    await store.approveGrants({
      installationId: receipt.installationId,
      expectedRevision: updated.revision,
      allowedTools: ["om_conversations"],
      allowedScopes: ["all-apps", "source:cursor"],
    });
    await expect(
      store.authorize(issued.secret, {
        tool: "om_conversations",
        scopes: ["all-apps", "source:cursor"],
      }),
    ).resolves.toMatchObject({ allowedScopes: ["all-apps", "source:cursor"] });
    for (const sourceScope of [
      "source:workbuddy",
      "source:all",
      "source:all-apps",
    ]) {
      await expect(
        store.authorize(issued.secret, {
          tool: "om_conversations",
          scopes: ["all-apps", sourceScope],
        }),
      ).rejects.toMatchObject({ code: "host_grant_scope_denied" });
    }
  });

  it("keeps source grants independent of all-apps and removes them on replacement", async () => {
    const { store, directory, issued, receipt } = await approved();
    const sourceOnly = await store.approveGrants({
      installationId: receipt.installationId,
      expectedRevision: receipt.revision,
      allowedTools: ["om_conversations"],
      allowedScopes: ["source:cursor"],
    });
    expect((await store.authenticate(issued.secret)).allowedScopes).toEqual([
      "source:cursor",
    ]);
    await expect(
      store.authorize(issued.secret, {
        tool: "om_conversations",
        scopes: ["all-apps", "source:cursor"],
      }),
    ).rejects.toMatchObject({ code: "host_grant_scope_denied" });
    await store.approveGrants({
      installationId: receipt.installationId,
      expectedRevision: sourceOnly.revision,
      allowedTools: ["om_conversations"],
      allowedScopes: ["all-apps"],
    });
    const restarted = await HostGrantStore.open(directory);
    expect((await restarted.authenticate(issued.secret)).allowedScopes).toEqual(
      ["all-apps"],
    );
    await expect(
      restarted.authorize(issued.secret, {
        tool: "om_conversations",
        scopes: ["all-apps", "source:cursor"],
      }),
    ).rejects.toMatchObject({ code: "host_grant_scope_denied" });
  });

  it.each(invalidScopes.map((scopes, index) => ({ scopes, index })))(
    "denies invalid or missing request scopes ($index)",
    async ({ scopes }) => {
      const { store, issued } = await approved();
      await expect(
        store.authorize(issued.secret, { tool: "om_recall", scopes } as never),
      ).rejects.toMatchObject({ code: "host_grant_scope_denied" });
    },
  );

  it("does not accept invalid approvals, partial approval, or implicit tool grants", async () => {
    const { store, file } = await fixture();
    const { receipt } = await store.createInstallation({ hostId: "codex" });
    const before = await readFile(file, "utf8");
    for (const allowedScopes of invalidScopes) {
      await expect(
        store.approveGrants({
          installationId: receipt.installationId,
          expectedRevision: receipt.revision,
          allowedTools: ["om_recall"],
          allowedScopes,
        } as never),
      ).rejects.toMatchObject({ code: "host_grant_invalid_input" });
    }
    for (const allowedTools of [
      [],
      ["*"],
      ["om_install"],
      ["om_recall", "bogus"],
      undefined,
    ]) {
      await expect(
        store.approveGrants({
          installationId: receipt.installationId,
          expectedRevision: receipt.revision,
          allowedTools,
          allowedScopes: ["all-apps"],
        } as never),
      ).rejects.toMatchObject({ code: "host_grant_invalid_input" });
    }
    expect(await readFile(file, "utf8")).toBe(before);
  });

  it("never lets a claimed host, installation, header, or receipt substitute for a secret", async () => {
    const { store, issued, receipt } = await approved();
    const unapproved = await store.createInstallation({ hostId: "cursor" });
    await expect(
      store.authorize(unapproved.secret, {
        tool: "om_recall",
        scopes: ["all-apps"],
      }),
    ).rejects.toMatchObject({ code: "host_grant_not_approved" });
    for (const input of [
      receipt.hostId,
      receipt.installationId,
      "cursor-mcp",
      receipt,
      undefined,
      `omhg_v1_${"a".repeat(43)}`,
      `${issued.secret.slice(0, -1)}${issued.secret.endsWith("a") ? "b" : "a"}`,
    ]) {
      await expect(store.authenticate(input as never)).rejects.toMatchObject({
        code: "host_grant_authentication_failed",
      });
    }
    for (const claims of [
      { host_id: "cursor" },
      { hostId: "cursor" },
      { installationId: receipt.installationId },
      { headers: { "X-One-Memory-Client": "cursor-mcp" } },
    ]) {
      await expect(
        store.authorize(unapproved.secret, {
          tool: "om_recall",
          scopes: ["all-apps"],
          ...claims,
        }),
      ).rejects.toBeDefined();
      await expect(
        store.authorize(issued.secret, {
          tool: "om_recall",
          scopes: ["all-apps"],
          ...claims,
        }),
      ).rejects.toMatchObject({ code: "host_grant_invalid_input" });
    }
    expect((await store.authenticate(issued.secret)).hostId).toBe("cursor");
  });

  it("persists revocation, clears rights, and cannot revive a revoked installation", async () => {
    const { store, directory, issued, receipt } = await approved();
    const peer = await HostGrantStore.open(directory);
    await peer.authenticate(issued.secret);
    const revoked = await store.revoke({
      installationId: receipt.installationId,
      expectedRevision: receipt.revision,
    });
    expect(revoked).toMatchObject({
      status: "revoked",
      revision: 3,
      allowedTools: [],
      allowedScopes: [],
    });
    for (const reader of [store, peer, await HostGrantStore.open(directory)]) {
      await expect(reader.authenticate(issued.secret)).rejects.toMatchObject({
        code: "host_grant_authentication_failed",
      });
    }
    await expect(
      store.approveGrants({
        installationId: receipt.installationId,
        expectedRevision: revoked.revision,
        allowedTools: ["om_recall"],
        allowedScopes: ["all-apps"],
      }),
    ).rejects.toMatchObject({ code: "host_grant_revoked" });
    await expect(
      store.rotateSecret({
        installationId: receipt.installationId,
        expectedRevision: revoked.revision,
      }),
    ).rejects.toMatchObject({ code: "host_grant_revoked" });
  });

  it("rotates secrets durably without expanding approval or unblocking install-only identities", async () => {
    const { store, issued, receipt, directory } = await approved();
    const rotated = await store.rotateSecret({
      installationId: receipt.installationId,
      expectedRevision: receipt.revision,
    });
    expect(rotated.secret).not.toBe(issued.secret);
    expect(rotated.receipt).toMatchObject({ ...receipt, revision: 3 });
    const restarted = await HostGrantStore.open(directory);
    await expect(restarted.authenticate(issued.secret)).rejects.toMatchObject({
      code: "host_grant_authentication_failed",
    });
    await expect(restarted.authenticate(rotated.secret)).resolves.toMatchObject(
      rotated.receipt,
    );
    const pending = await store.createInstallation({ hostId: "claude-code" });
    const pendingRotated = await store.rotateSecret({
      installationId: pending.receipt.installationId,
      expectedRevision: pending.receipt.revision,
    });
    expect(pendingRotated.receipt).toMatchObject({
      status: "install-only",
      allowedTools: [],
      allowedScopes: [],
    });
    await expect(
      store.authenticate(pendingRotated.secret),
    ).rejects.toMatchObject({ code: "host_grant_not_approved" });
  });

  it("serializes concurrent writers and rejects every stale mutation without losing other installations", async () => {
    const { directory, store, issued, receipt } = await approved();
    const peer = await HostGrantStore.open(directory);
    const results = await Promise.allSettled([
      store.revoke({
        installationId: receipt.installationId,
        expectedRevision: receipt.revision,
      }),
      peer.rotateSecret({
        installationId: receipt.installationId,
        expectedRevision: receipt.revision,
      }),
    ]);
    expect(
      results.filter((result) => result.status === "fulfilled"),
    ).toHaveLength(1);
    expect(results.filter((result) => result.status === "rejected")).toEqual([
      expect.objectContaining({
        reason: expect.objectContaining({
          code: "host_grant_revision_conflict",
        }),
      }),
    ]);
    for (const mutate of [
      () =>
        store.revoke({
          installationId: receipt.installationId,
          expectedRevision: 2,
        }),
      () =>
        store.rotateSecret({
          installationId: receipt.installationId,
          expectedRevision: 2,
        }),
      () =>
        store.approveGrants({
          installationId: receipt.installationId,
          expectedRevision: 2,
          allowedTools: ["om_recall"],
          allowedScopes: [workspace],
        }),
    ]) {
      await expect(mutate()).rejects.toMatchObject({
        code: "host_grant_revision_conflict",
      });
    }
    const creations = await Promise.all(
      Array.from({ length: 12 }, (_, index) =>
        (index % 2 ? store : peer).createInstallation({ hostId: "codex" }),
      ),
    );
    const current = await (
      await HostGrantStore.open(directory)
    ).listInstallations();
    expect(current).toHaveLength(13);
    expect(new Set(current.map((item) => item.installationId)).size).toBe(13);
    expect(
      creations.every((item) =>
        current.some(
          (saved) => saved.installationId === item.receipt.installationId,
        ),
      ),
    ).toBe(true);
    await expect(store.authenticate(issued.secret)).rejects.toMatchObject({
      code: "host_grant_authentication_failed",
    });
  });

  it("prevents lost updates across independent Node processes", async () => {
    const { directory, store } = await fixture();
    const moduleUrl = new URL("./host-grants.ts", import.meta.url).href;
    const script = `
      import { HostGrantStore } from ${JSON.stringify(moduleUrl)};
      const store = await HostGrantStore.open(process.argv[1]);
      for (let i = 0; i < 4; i++) await store.createInstallation({ hostId: 'codex' });
      console.log('complete');
    `;
    const results = await Promise.all(
      Array.from({ length: 3 }, () =>
        runFile(process.execPath, [
          "--input-type=module",
          "--eval",
          script,
          directory,
        ]),
      ),
    );
    expect(results.every((result) => result.stdout.trim() === "complete")).toBe(
      true,
    );
    expect(await store.listInstallations()).toHaveLength(12);
  });

  it("persists only secret hashes in private files and redacts public receipts and errors", async () => {
    const { store, directory, file, issued, receipt } = await approved();
    const serialized = await readFile(file, "utf8");
    const digest = createHash("sha256").update(issued.secret).digest("hex");
    expect(serialized).not.toContain(issued.secret);
    expect(serialized).toContain(digest);
    expect((await stat(directory)).mode & 0o777).toBe(0o700);
    expect((await stat(file)).mode & 0o777).toBe(0o600);
    expect(await readdir(directory)).toEqual(["host-grants.json"]);
    for (const publicValue of [
      receipt,
      issued,
      await store.authenticate(issued.secret),
      await store.listInstallations(),
    ]) {
      expect(JSON.stringify(publicValue)).not.toContain(issued.secret);
      expect(JSON.stringify(publicValue)).not.toContain(digest);
      expect(inspect(publicValue)).not.toContain(issued.secret);
    }
    const error = await store
      .authenticate(`${issued.secret}bad`)
      .catch((failure: unknown) => failure);
    expect(inspect(error)).not.toContain(issued.secret);
    expect(JSON.stringify(error)).not.toContain(issued.secret);
    expect(error).not.toHaveProperty("cause");
    expect(Object.isFrozen(receipt)).toBe(true);
    expect(Object.isFrozen(receipt.allowedScopes)).toBe(true);
  });

  it("fails closed for malformed JSON without echoing its contents or resetting it", async () => {
    const { store, directory, file, issued } = await approved();
    const corrupt = `{"secret":"${issued.secret}",broken`;
    await writeFile(file, corrupt);
    for (const operation of [
      () => store.authenticate(issued.secret),
      () => store.createInstallation({ hostId: "codex" }),
      () => HostGrantStore.open(directory),
    ]) {
      const error = await operation().catch((failure: unknown) => failure);
      expect(error).toMatchObject({ code: "host_grant_store_corrupt" });
      expect(inspect(error)).not.toContain(issued.secret);
      expect(error).not.toHaveProperty("cause");
      expect(await readFile(file, "utf8")).toBe(corrupt);
    }
    await expect(HostGrantStore.initialize(directory)).rejects.toMatchObject({
      code: "host_grant_store_exists",
    });
  });

  it("rejects malformed grants, duplicate identities, and unsupported store versions", async () => {
    const { store, file, issued } = await approved();
    const original = JSON.parse(await readFile(file, "utf8"));
    const cases = [
      { ...original, schemaVersion: 2 },
      { ...original, revision: -1 },
      {
        ...original,
        installations: [...original.installations, original.installations[0]],
      },
      {
        ...original,
        installations: [{ ...original.installations[0], allowedScopes: ["*"] }],
      },
      {
        ...original,
        installations: [
          { ...original.installations[0], status: "install-only" },
        ],
      },
      {
        ...original,
        installations: [
          { ...original.installations[0], secret: issued.secret },
        ],
      },
    ];
    for (const value of cases) {
      const text = JSON.stringify(value);
      await writeFile(file, text);
      await expect(store.authenticate(issued.secret)).rejects.toBeDefined();
      expect(await readFile(file, "utf8")).toBe(text);
    }
  });

  it("rejects a missing store, unsafe permissions, symlinks, and an abandoned lock", async () => {
    const { directory, file, store, issued, root } = await approved();
    await chmod(file, 0o644);
    await expect(store.authenticate(issued.secret)).rejects.toMatchObject({
      code: "host_grant_store_unsafe",
    });
    await chmod(file, 0o600);
    await chmod(directory, 0o755);
    await expect(store.authenticate(issued.secret)).rejects.toMatchObject({
      code: "host_grant_store_unsafe",
    });
    await chmod(directory, 0o700);
    const alias = path.join(root, "alias");
    await symlink(directory, alias);
    await expect(HostGrantStore.open(alias)).rejects.toMatchObject({
      code: "host_grant_store_unsafe",
    });
    const text = await readFile(file, "utf8");
    const lock = path.join(directory, ".mutation-lock");
    await mkdir(lock, { mode: 0o700 });
    const blocked = await HostGrantStore.open(directory, { lockTimeoutMs: 20 });
    await expect(
      blocked.createInstallation({ hostId: "codex" }),
    ).rejects.toMatchObject({ code: "host_grant_store_busy" });
    expect(await readFile(file, "utf8")).toBe(text);
    await rm(lock, { recursive: true });
    await rm(file);
    await expect(store.authenticate(issued.secret)).rejects.toMatchObject({
      code: "host_grant_store_unavailable",
    });
    await expect(
      store.createInstallation({ hostId: "codex" }),
    ).rejects.toMatchObject({ code: "host_grant_store_unavailable" });
    await expect(HostGrantStore.open(directory)).rejects.toMatchObject({
      code: "host_grant_store_unavailable",
    });
    await expect(HostGrantStore.initialize(directory)).rejects.toMatchObject({
      code: "host_grant_store_exists",
    });
    const target = path.join(root, "outside.json");
    await writeFile(target, text, { mode: 0o600 });
    await symlink(target, file);
    await expect(store.authenticate(issued.secret)).rejects.toMatchObject({
      code: "host_grant_store_unsafe",
    });
    expect(await readFile(target, "utf8")).toBe(text);
  });

  it("rejects observed revision rollback and a different store identity", async () => {
    const { store, file } = await fixture();
    const empty = await readFile(file, "utf8");
    await store.createInstallation({ hostId: "codex" });
    const current = JSON.parse(await readFile(file, "utf8"));
    await writeFile(file, empty);
    await expect(store.listInstallations()).rejects.toMatchObject({
      code: "host_grant_store_conflict",
    });
    const other = await fixture();
    current.storeId = JSON.parse(await readFile(other.file, "utf8")).storeId;
    await writeFile(file, JSON.stringify(current));
    await expect(store.listInstallations()).rejects.toMatchObject({
      code: "host_grant_store_conflict",
    });
  });

  it("rejects replacement of observed grants without a revision change", async () => {
    const { store, file, issued } = await approved();
    const changed = JSON.parse(await readFile(file, "utf8"));
    changed.installations[0].allowedScopes.push(otherWorkspace);
    await writeFile(file, JSON.stringify(changed));
    await expect(store.authenticate(issued.secret)).rejects.toMatchObject({
      code: "host_grant_store_conflict",
    });
    await expect(
      store.createInstallation({ hostId: "codex" }),
    ).rejects.toMatchObject({
      code: "host_grant_store_conflict",
    });
  });

  it("preserves the last committed store on atomic rename failure and sanitizes errors", async () => {
    const { store, file, directory, issued, receipt } = await approved();
    const before = await readFile(file, "utf8");
    vi.spyOn(filesystem, "rename").mockRejectedValueOnce(
      new Error(issued.secret),
    );
    const error = await store
      .revoke({
        installationId: receipt.installationId,
        expectedRevision: receipt.revision,
      })
      .catch((failure: unknown) => failure);
    expect(error).toMatchObject({ code: "host_grant_store_unavailable" });
    expect(inspect(error)).not.toContain(issued.secret);
    expect(error).not.toHaveProperty("cause");
    expect(await readFile(file, "utf8")).toBe(before);
    expect(await readdir(directory)).toEqual(["host-grants.json"]);
    await expect(store.authenticate(issued.secret)).resolves.toMatchObject(
      receipt,
    );
    await expect(
      store.revoke({
        installationId: receipt.installationId,
        expectedRevision: receipt.revision,
      }),
    ).resolves.toMatchObject({ status: "revoked", revision: 3 });
  });

  it("keeps receipt revisions independent of other installations and snapshots mutation input", async () => {
    const { store, issued, receipt } = await approved();
    const input = {
      installationId: receipt.installationId,
      expectedRevision: receipt.revision,
      allowedTools: ["om_recall"] as const,
      allowedScopes: ["all-apps"],
    };
    const update = store.approveGrants(input);
    input.allowedScopes.push(otherWorkspace);
    input.installationId = "changed";
    await expect(update).resolves.toMatchObject({
      allowedScopes: ["all-apps"],
    });
    const unrelated = await store.createInstallation({ hostId: "workbuddy" });
    await store.createInstallation({ hostId: "codex" });
    await expect(
      store.approveGrants({
        installationId: unrelated.receipt.installationId,
        expectedRevision: unrelated.receipt.revision,
        allowedTools: ["om_conversations"],
        allowedScopes: [workspace],
      }),
    ).resolves.toMatchObject({ revision: 2 });
    await expect(
      store.authorize(issued.secret, {
        tool: "om_recall",
        scopes: [otherWorkspace],
      }),
    ).rejects.toMatchObject({ code: "host_grant_scope_denied" });
  });

  it("also checks expected revisions across independent Node processes", async () => {
    const { directory, store, receipt } = await approved();
    const moduleUrl = new URL("./host-grants.ts", import.meta.url).href;
    const script = `
      import { HostGrantStore } from ${JSON.stringify(moduleUrl)};
      const store = await HostGrantStore.open(process.argv[1]);
      try {
        const receipt = await store.revoke({ installationId: process.argv[2], expectedRevision: 2 });
        console.log(JSON.stringify({ revision: receipt.revision }));
      } catch (error) { console.log(JSON.stringify({ code: error.code })); }
    `;
    const results = await Promise.all(
      Array.from({ length: 3 }, () =>
        runFile(process.execPath, [
          "--input-type=module",
          "--eval",
          script,
          directory,
          receipt.installationId,
        ]),
      ),
    );
    const outputs = results.map((result) => JSON.parse(result.stdout));
    expect(outputs.filter((output) => output.revision === 3)).toHaveLength(1);
    expect(
      outputs.filter(
        (output) => output.code === "host_grant_revision_conflict",
      ),
    ).toHaveLength(2);
    expect((await store.listInstallations())[0]).toMatchObject({
      revision: 3,
      status: "revoked",
    });
  });
});

it("accepts one profile segment in a host id and still rejects malformed ones", async () => {
  // DeepSeek Harness is configured per profile and reports
  // `deepseek-harness:<profile>`; the installation directory is a digest of the
  // id, so the colon is a label rather than a path component. The boundary
  // cases themselves live in `@one-memory/contracts`; this tests that the grant
  // store reads the same grammar and revalidates it on load.
  const context = await fixture();
  const issued = await context.store.createInstallation({
    hostId: "deepseek-harness:desktop",
  });
  expect(issued.receipt.hostId).toBe("deepseek-harness:desktop");
  // Every row is validated again on load; the colon has to survive that pass.
  const reopened = await HostGrantStore.open(context.directory);
  expect(
    (await reopened.listInstallations()).map((row) => row.hostId),
  ).toContain("deepseek-harness:desktop");
  for (const hostId of ["9lives", "a:b", `a:${"b".repeat(32)}`]) {
    await expect(
      context.store.createInstallation({ hostId }),
    ).resolves.toMatchObject({ receipt: { hostId } });
  }
  for (const hostId of [
    "deepseek-harness:",
    "deepseek-harness:a:b",
    "DeepSeek-Harness:web",
    "deepseek harness:web",
  ]) {
    await expect(
      context.store.createInstallation({ hostId }),
    ).rejects.toMatchObject({ code: "host_grant_invalid_input" });
  }
});
