import { afterEach, describe, expect, it, vi } from "vitest";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";

vi.mock("openclaw/plugin-sdk/plugin-entry", () => ({
  definePluginEntry: (entry: unknown) => entry,
}));

import plugin from "./index.ts";

const messages = [
  { role: "user", content: "Remember that my preferred editor is Vim." },
  { role: "assistant", content: "I will remember your preferred editor." },
];
const later = [
  ...messages,
  { role: "user", content: "I also prefer the solarized color scheme." },
];

function setup(serverUrl?: string) {
  const hooks = new Map<string, (...args: any[]) => Promise<void>>();
  const logger = { info: vi.fn(), warn: vi.fn(), error: vi.fn(), debug: vi.fn() };
  plugin.register({
    pluginConfig: { autoCapture: true, memoryWriteTurnWindow: 1, ...(serverUrl ? { serverUrl } : {}) },
    logger,
    on: (event: string, handler: (...args: any[]) => Promise<void>) => hooks.set(event, handler),
    registerTool: vi.fn(),
    registerCli: vi.fn(),
  } as any);
  return {
    agentEnd: (items = messages, sessionKey = "session-a") =>
      hooks.get("agent_end")!({ success: true, messages: items }, { sessionKey }),
    sessionEnd: () => hooks.get("session_end")!({}, { sessionKey: "session-a" }),
    logger,
  };
}

function success() {
  return new Response(JSON.stringify({ success: true }), { status: 200 });
}

afterEach(() => vi.unstubAllGlobals());

describe("OpenClaw auto-capture acknowledgement", () => {
  it("retries a loopback server's 503 response through the real HTTP provider", async () => {
    const bodies: any[] = [];
    const server = createServer(async (req, res) => {
      let body = "";
      for await (const chunk of req) body += chunk;
      bodies.push(JSON.parse(body));
      res.writeHead(bodies.length === 1 ? 503 : 200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(bodies.length === 1 ? { error: "temporarily unavailable" } : { success: true }));
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    try {
      const hooks = setup(`http://127.0.0.1:${(server.address() as AddressInfo).port}`);
      await hooks.agentEnd();
      await hooks.sessionEnd();
      expect(bodies).toHaveLength(2);
      expect(bodies[1].messages).toEqual(messages);
    } finally {
      server.closeAllConnections();
      await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
    }
  });

  it("retries a rejected HTTP write when the session ends", async () => {
    const fetch = vi.fn().mockRejectedValueOnce(new Error("connection lost"))
      .mockResolvedValueOnce(success());
    vi.stubGlobal("fetch", fetch);
    const hooks = setup();
    await hooks.agentEnd();
    await hooks.sessionEnd();
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(JSON.parse(fetch.mock.calls[1][1].body).messages).toEqual(messages);
  });

  it("keeps failed SDK writes pending and retries them with new messages", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({
      success: false, error_message: "embedding unavailable",
    }))).mockResolvedValueOnce(success());
    vi.stubGlobal("fetch", fetch);
    const hooks = setup();
    await hooks.agentEnd();
    expect(hooks.logger.warn).toHaveBeenCalledWith(expect.stringContaining("embedding unavailable"));
    await hooks.agentEnd(later);
    expect(JSON.parse(fetch.mock.calls[1][1].body).messages).toEqual(later);
  });

  it("serializes same-session writes and retries the pending prefix after failure", async () => {
    let reject!: (reason: Error) => void;
    const fetch = vi.fn().mockImplementationOnce(() => new Promise((_, fail) => { reject = fail; }))
      .mockResolvedValueOnce(success());
    vi.stubGlobal("fetch", fetch);
    const hooks = setup();
    const first = hooks.agentEnd();
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    const second = hooks.agentEnd(later);
    await Promise.resolve();
    expect(fetch).toHaveBeenCalledTimes(1);
    reject(new Error("temporary failure"));
    await Promise.all([first, second]);
    expect(JSON.parse(fetch.mock.calls[1][1].body).messages).toEqual(later);
  });

  it("does not replay acknowledged messages or block another session", async () => {
    let resolve!: (response: Response) => void;
    const fetch = vi.fn().mockImplementationOnce(() => new Promise((done) => { resolve = done; }))
      .mockImplementation(() => Promise.resolve(success()));
    vi.stubGlobal("fetch", fetch);
    const hooks = setup();
    const first = hooks.agentEnd();
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    await hooks.agentEnd(messages, "session-b");
    expect(fetch).toHaveBeenCalledTimes(2);
    resolve(success());
    await first;
    await hooks.agentEnd(later);
    await hooks.sessionEnd();
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(JSON.parse(fetch.mock.calls[2][1].body).messages).toEqual(later.slice(messages.length));
  });

  it("waits for a pending capture before flushing and does not duplicate a successful write", async () => {
    let resolve!: (response: Response) => void;
    const fetch = vi.fn().mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    vi.stubGlobal("fetch", fetch);
    const hooks = setup();
    const capture = hooks.agentEnd();
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    const flushed = vi.fn();
    const flush = hooks.sessionEnd().then(flushed);
    await Promise.resolve();
    await Promise.resolve();
    expect(flushed).not.toHaveBeenCalled();
    resolve(success());
    await Promise.all([capture, flush]);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(flushed).toHaveBeenCalledOnce();
  });

  it("does not acknowledge messages appended while the request is pending", async () => {
    let resolve!: (response: Response) => void;
    const fetch = vi.fn().mockImplementationOnce(() => new Promise((done) => { resolve = done; }))
      .mockResolvedValueOnce(success());
    vi.stubGlobal("fetch", fetch);
    const hooks = setup();
    const mutableMessages = [...messages];
    const capture = hooks.agentEnd(mutableMessages);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    mutableMessages.push(later[2]);
    resolve(success());
    await capture;
    await hooks.sessionEnd();
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(JSON.parse(fetch.mock.calls[1][1].body).messages).toEqual([later[2]]);
  });
});
