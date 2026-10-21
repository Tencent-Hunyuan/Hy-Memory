import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/** Guard the two halves of a traced write.
 *
 * A `ui.action` only forms a complete four-line chain when the same trace id
 * reaches Core on the request. It is easy to add a trace and forget the
 * argument, which yields a log with `ui.action -> ui.result` and no kernel
 * legs — exactly the failure the native verifier rejects. This check reads the
 * real sources so that mistake fails in the web suite instead of only in a
 * packaged-App run.
 */

const ROOT = path.resolve(__dirname, "..");

function source(file: string): string {
  return readFileSync(path.join(ROOT, "src", file), "utf8");
}

describe("ui trace write coverage", () => {
  it("passes a trace id on every traced memory write", () => {
    const api = source("api.ts");
    // The remaining legacy memory write functions accept a trace id and forward it.
    for (const fn of ["mutateMemory", "correctMemory"]) {
      const start = api.indexOf(`export async function ${fn}(`);
      expect(start, `${fn} not found`).toBeGreaterThan(-1);
      const body = api.slice(start, api.indexOf("\n}", start));
      expect(body, `${fn} must accept traceId`).toContain("traceId?: string");
      expect(body, `${fn} must send the trace header`).toContain(
        "X-One-Memory-Trace-Id",
      );
    }
  });

  it("threads the trace id from each discovering surface", () => {
    // SourcesView already carried the id; assert it stays.
    expect(source("SourcesView.tsx")).toMatch(
      /imports\.setSync\(source, enabled, trace\.traceId\)/,
    );
    // MemorySpace passes the id on both remaining governance writes.
    const memory = source("MemorySpace.tsx");
    expect(memory).toMatch(/mutateMemory\([\s\S]{0,160}trace\.traceId/);
    expect(memory).toMatch(/correctMemory\([\s\S]{0,200}trace\.traceId/);
    // SelectedMemoryDetail traces its correction, delete and undo.
    const detail = source("SelectedMemoryDetail.tsx");
    expect(detail).toMatch(/correctMemory\([\s\S]{0,200}trace\.traceId/);
    expect(detail).toContain('traceUiAction("memory", "confirm")');
    expect(detail).toContain('traceUiAction("memory", "retry")');
    // The composer's save is the native four-level sample.
    expect(source("MemoryNoteComposer.tsx")).toMatch(
      /saveMemoryNote\([\s\S]{0,200}trace\.traceId/,
    );
  });

  it("traces the history-import and graph-width writes", () => {
    const history = source("HistoryImport.tsx");
    expect(history).toMatch(
      /uploadHistoryFile\(file, inputKind, trace\.traceId\)/,
    );
    expect(history).toMatch(
      /commitHistoryFile\(receipt\.upload_id, trace\.traceId\)/,
    );
    const graph = source("GraphWidthPrompt.tsx");
    expect(graph).toMatch(/decideGraphWidth\(decision, trace\.traceId\)/);
    const api = source("api.ts");
    for (const fn of [
      "uploadHistoryFile",
      "commitHistoryFile",
      "decideGraphWidth",
    ]) {
      const start = api.indexOf(`export async function ${fn}(`);
      expect(start, `${fn} not found`).toBeGreaterThan(-1);
      const body = api.slice(start, start + 900);
      expect(body, `${fn} must accept traceId`).toContain("traceId?: string");
    }
    // uploadHistoryFile threads the id through the recoverable transport.
    expect(api).toMatch(/recoverableFileUpload\(/);
    const uploadBody = api.slice(
      api.indexOf("export async function uploadHistoryFile("),
      api.indexOf("export async function uploadHistoryFile(") + 700,
    );
    expect(uploadBody).toMatch(/historyUploadTransport,\s*\n?\s*traceId/);
    // commit and graph forward the header directly.
    const commitBody = api.slice(
      api.indexOf("export async function commitHistoryFile("),
      api.indexOf("export async function commitHistoryFile(") + 700,
    );
    expect(commitBody).toContain("X-One-Memory-Trace-Id");
    const graphBody = api.slice(
      api.indexOf("export async function decideGraphWidth("),
      api.indexOf("export async function decideGraphWidth(") + 700,
    );
    expect(graphBody).toContain("X-One-Memory-Trace-Id");
  });

  it("traces the source-folder save", () => {
    // Choosing a source folder is a real Core write: `PUT
    // /api/v1/sources/{id}/candidate` is a non-GET under `/api/v1/sources`, so
    // Core logs kernel-call/kernel-return. This path had no trace at all, so
    // those legs were orphaned and the log showed a write with no matching user
    // action. The guard also pins the transport half: a trace that is opened but
    // never handed to `saveSourceDirectory` still produces a half chain.
    const picker = source("SourceDirectoryPicker.tsx");
    expect(picker).toMatch(/traceUiAction\("sources", "confirm"\)/);
    expect(picker).toMatch(/saveSourceRoot\(\[[\s\S]{0,400}?trace\.traceId,/);
    const api = source("api.ts");
    const start = api.indexOf("export async function saveSourceDirectory(");
    expect(start, "saveSourceDirectory not found").toBeGreaterThan(-1);
    const body = api.slice(start, api.indexOf("\n}", start));
    expect(body, "saveSourceDirectory must accept traceId").toContain(
      "traceId?: string",
    );
    expect(body, "saveSourceDirectory must send the trace header").toContain(
      "X-One-Memory-Trace-Id",
    );
  });

  it("does not reissue a leftover sync write after onboarding", () => {
    // Connecting a source already opens the one Harness processing switch.
    // Leaving onboarding must not submit a second sync write for the same
    // choice; Freeze/Thaw remains a later explicit user action.
    const onboarding = source("OnboardingFlow.tsx");
    expect(onboarding).not.toMatch(/setSourceSync\(/);
  });

  it("traces matter discovery, refresh, patch and note writes", () => {
    // The matters workspace is gated by VITE_ENABLE_MATTERS, but when enabled
    // its buttons are real POST/PATCH operations under `/api/v1/matters`.
    // Leaving them untraced produces kernel-only orphan chains. The shared
    // action wrapper opens exactly one pair per user action.
    const view = source("MatterWorkspace.tsx");
    expect(view).toMatch(/traceUiAction\("matters", "submit"\)/);
    expect(view).toMatch(/discoverMatters\(traceId\)/);
    expect(view).toMatch(/refreshMatter\(matter\.matter_id, traceId\)/);
    expect(view).toMatch(/patchMatter\([\s\S]{0,320}?traceId\s*[),]/);
    expect(view).toMatch(/addMatterNote\([\s\S]{0,240}?traceId\s*[),]/);
    const api = source("api.ts");
    for (const fn of [
      "discoverMatters",
      "patchMatter",
      "addMatterNote",
      "refreshMatter",
    ]) {
      const start = api.indexOf(`export async function ${fn}(`);
      expect(start, `${fn} not found`).toBeGreaterThan(-1);
      const body = api.slice(start, start + 500);
      expect(body, `${fn} must accept traceId`).toContain("traceId?: string");
      expect(body, `${fn} must send the trace header`).toContain(
        "X-One-Memory-Trace-Id",
      );
    }
  });

  it("traces every remembered-preference sync write", () => {
    // The remembered-preference reconciler runs outside a click handler: the
    // app applies saved sync selections on entry and during onboarding. Each
    // write must still open a chain at the point of the request instead of
    // leaving kernel-only legs behind.
    const reconcile = source("connection-reconcile.ts");
    expect(reconcile).toMatch(/traceUiAction\("sources", "confirm"\)/);
    expect(reconcile).toMatch(/setSync\(source, true, trace\.traceId\)/);
  });

  it("gives every settle() guard exactly one finally close", () => {
    // The write paths that can return early between opening a trace and
    // finishing it use an idempotent `settle()` helper whose only guaranteed
    // call site is a `finally`. If that call is dropped, an early return leaves
    // a `ui.action` with no `ui.result` — an incomplete chain the native
    // verifier rejects. This ties each `const settle =` definition to exactly
    // one `finally { ... settle(` close in the same file.
    for (const file of ["HistoryImport.tsx"]) {
      const text = source(file);
      const definitions = (text.match(/const settle\s*=/g) ?? []).length;
      const finallyCloses = (
        text.match(/\}\s*finally\s*\{[^}]*\bsettle\(/g) ?? []
      ).length;
      expect(
        definitions,
        `${file} should define at least one settle`,
      ).toBeGreaterThan(0);
      expect(
        finallyCloses,
        `${file} has ${definitions} settle definition(s) but ${finallyCloses} finally close(s)`,
      ).toBe(definitions);
    }
  });

  it("traces the index-sync, graph-digest, extraction and manual-extract writes", () => {
    // These four are non-GET writes on the memory surface that previously ran
    // with no trace at all.
    const memory = source("MemorySpace.tsx");
    expect(memory).toMatch(/syncMemoryIndex\([\s\S]{0,80}trace\.traceId/);
    expect(memory).toMatch(/digestMemoryGraph\(trace\.traceId\)/);
    expect(memory).toMatch(/setExtractionWindow\([\s\S]{0,80}trace\.traceId/);
    expect(memory).toMatch(/manualExtract\(\[\], trace\.traceId\)/);
    const api = source("api.ts");
    for (const fn of [
      "syncMemoryIndex",
      "digestMemoryGraph",
      "setExtractionWindow",
      "manualExtract",
    ]) {
      const start = api.indexOf(`export async function ${fn}(`);
      expect(start, `${fn} not found`).toBeGreaterThan(-1);
      const body = api.slice(start, start + 600);
      expect(body, `${fn} must accept traceId`).toContain("traceId?: string");
      expect(body, `${fn} must send the trace header`).toContain(
        "X-One-Memory-Trace-Id",
      );
    }
  });

  it("does not close an index-sync trace as succeeded when Core returns pending", () => {
    const memory = source("MemorySpace.tsx");
    const start = memory.indexOf("async function syncIndex()");
    expect(start).toBeGreaterThan(-1);
    const body = memory.slice(
      start,
      memory.indexOf("\n  async function", start + 1),
    );
    expect(body).toMatch(
      /if \(result\.status === "synced"\) trace\.done\("succeeded"\);\s*else\s+trace\.done\("failed"/,
    );
  });

  it("traces the destructive call-history clears", () => {
    // Clearing call records deletes retained history, so it is a real write and
    // must form a four-line chain like the other writes. It previously emitted
    // nothing: the api helpers did not even accept a trace id.
    const view = source("CallHistory.tsx");
    expect(view).toMatch(/traceUiAction\("calls", "confirm"\)/);
    expect(view).toMatch(/clearAllCallEvents\(trace\.traceId\)/);
    expect(view).toMatch(
      /clearCallEvents\(\[\.\.\.confirmation\.ids\], trace\.traceId\)/,
    );

    const client = source("call-history-client.ts");
    for (const fn of ["clearCallEvents", "clearAllCallEvents"]) {
      const start = client.indexOf(`export async function ${fn}(`);
      expect(start, `${fn} not found`).toBeGreaterThan(-1);
      const body = client.slice(start, client.indexOf("\n}", start));
      expect(body, `${fn} must accept traceId`).toContain("traceId");
      expect(body, `${fn} must forward traceId`).toMatch(/traceId\)/);
    }
    const api = source("api.ts");
    for (const fn of ["clearCallEventsRequest", "clearAllCallEventsRequest"]) {
      const start = api.indexOf(`export async function ${fn}(`);
      expect(start, `${fn} not found`).toBeGreaterThan(-1);
      const body = api.slice(start, start + 500);
      expect(body, `${fn} must accept traceId`).toContain("traceId?: string");
      expect(body, `${fn} must send the trace header`).toContain(
        "X-One-Memory-Trace-Id",
      );
    }
  });

  it("traces the streaming answer write", () => {
    // `askMemoriesStream` is a raw `fetch`, so it never passes through the axios
    // interceptor that stamps the trace header. If the caller does not open a
    // trace and hand the id down, Core still logs its kernel legs but under a
    // different id, and the interaction is a half-chain. Both halves are pinned:
    // the call site opens a trace and passes the id, and the stream forwards it.
    const view = source("MemorySpace.tsx");
    expect(view).toMatch(/traceUiAction\("memory", "submit"\)/);
    expect(view).toMatch(
      /askMemoriesStream\(\s*question,\s*scope,\s*undefined,\s*trace\.traceId,?\s*\)/,
    );
    const api = source("api.ts");
    const start = api.indexOf("export async function* askMemoriesStream(");
    expect(start, "askMemoriesStream not found").toBeGreaterThan(-1);
    const body = api.slice(start, api.indexOf("\n}", start));
    expect(body, "askMemoriesStream must accept traceId").toContain(
      "traceId?: string",
    );
    expect(body, "askMemoriesStream must send the trace header").toContain(
      "X-One-Memory-Trace-Id",
    );
  });

  it("traces the answer note save and the bulk memory mutations", () => {
    // `saveAnswer` writes a memory note, and each bulk item issues its own
    // mutateMemory request; both must form four-line chains. A bulk action is
    // one gesture but N kernel calls, so per-item traces are the correct shape.
    const view = source("MemorySpace.tsx");
    expect(view).toMatch(
      /saveMemoryNote\(\s*\{[\s\S]{0,240}?scope: "all-apps",\s*\},?\s*trace\.traceId,?\s*\)/,
    );
    expect(view).toMatch(
      /mutateMemory\(\s*target\.memoryId,[\s\S]{0,200}?trace\.traceId,\s*\)/,
    );
  });

  it("traces the Harness Freeze/Thaw toggle", () => {
    const control = source("SourcesView.tsx");
    expect(control).toMatch(/traceUiAction\("sources", "toggle"\)/);
    expect(control).toMatch(
      /imports\.setSync\(source, enabled, trace\.traceId\)/,
    );

    const hook = source("use-import-sources.ts");
    expect(hook).toMatch(
      /syncWrite\.mutateAsync\(\{ source, enabled, traceId \}\)/,
    );

    const state = source("source-state.ts");
    expect(state).toMatch(/setSourceSync\(current, enabled, traceId\)/);

    const api = source("api.ts");
    const start = api.indexOf("export async function setSourceSync(");
    expect(start).toBeGreaterThan(-1);
    const body = api.slice(start, start + 600);
    expect(body).toContain("traceId?: string");
    expect(body).toContain("X-One-Memory-Trace-Id");
    expect(api).toContain("X-One-Memory-Trace-Id");
  });

  it("traces the onboarding completion write", () => {
    // Finishing first-run setup records durable onboarding state. It is a
    // non-GET and had no trace.
    const flow = source("OnboardingFlow.tsx");
    expect(flow).toMatch(/traceUiAction\("onboarding", "submit"\)/);
    expect(flow).toMatch(/completeOnboarding\(trace\.traceId\)/);
    const api = source("api.ts");
    const start = api.indexOf("export async function completeOnboarding(");
    expect(start).toBeGreaterThan(-1);
    const body = api.slice(start, start + 400);
    expect(body).toContain("traceId?: string");
    expect(body).toContain("X-One-Memory-Trace-Id");
  });

  it("traces the memory-note process write", () => {
    // "重新整理" starts note processing: a non-GET write that had no trace.
    const composer = source("MemoryNoteComposer.tsx");
    expect(composer).toMatch(/traceUiAction\("memory", "retry"\)/);
    expect(composer).toMatch(
      /processMemoryNote\(note\.evidence_id, trace\.traceId\)/,
    );
    const api = source("api.ts");
    const start = api.indexOf("export async function processMemoryNote(");
    expect(start).toBeGreaterThan(-1);
    const body = api.slice(start, start + 600);
    expect(body).toContain("traceId?: string");
    expect(body).toContain("X-One-Memory-Trace-Id");
  });

  it("traces every model-profile write through the hook's single choke point", () => {
    // create / activate / update / delete / probe are all non-GET writes. The
    // hook's `run` funnel starts one trace per write, so each must be handed
    // that trace id and the api functions must forward it.
    const hook = source("use-model-profiles.ts");
    expect(hook).toMatch(/traceUiAction\("settings", traceAction\(key\)\)/);
    // The failure reason must be a bounded code, not the raw key: `key` holds
    // `activate:<profile-id>`, and logging it verbatim would put a profile UUID
    // in the line and make every failure its own ungroupable value.
    // The outcome now depends on whether Core refused or the result is unknown,
    // so the call is `done(<status>, { reason: ... })` rather than a literal
    // `"failed"`. Both reason helpers must stay bounded codes tied to `key`.
    expect(hook).toMatch(
      /trace\.done\(isDefiniteRefusal\(reason\) \? "failed" : "unknown", \{/,
    );
    expect(hook).toMatch(
      /traceReason\(key\)[\s\S]{0,120}?traceReasonUnknown\(key\)/,
    );
    expect(hook).toMatch(
      /function traceReasonUnknown\(key: string\): string \{/,
    );
    expect(hook).not.toMatch(/trace\.done\("failed", \{ reason: key \}\)/);
    expect(hook).toMatch(
      /createModelProfile\(\s*\{[\s\S]{0,200}?traceId,\s*\)/,
    );
    expect(hook).toMatch(/activateModelProfile\(profileId, traceId\)/);
    expect(hook).toMatch(/updateModelProfile\(profileId, patch, traceId\)/);
    expect(hook).toMatch(/deleteModelProfile\(profileId, traceId\)/);
    expect(hook).toMatch(
      /probeModelProfile\(profileId, inputRevision, traceId\)/,
    );
    const api = source("api.ts");
    for (const fn of [
      "createModelProfile",
      "probeModelProfile",
      "activateModelProfile",
      "updateModelProfile",
      "deleteModelProfile",
    ]) {
      const start = api.indexOf(`export async function ${fn}(`);
      expect(start, `${fn} not found`).toBeGreaterThan(-1);
      // Slice to the function's real end, not a fixed window: a fixed window
      // silently stopped covering the header once a comment pushed it past the
      // boundary, which reports a missing header that is actually there.
      const end = api.indexOf("\n}", start);
      expect(end, `${fn} body end not found`).toBeGreaterThan(start);
      const body = api.slice(start, end);
      expect(body, `${fn} must accept traceId`).toContain("traceId?: string");
      expect(body, `${fn} must send the trace header`).toContain(
        "X-One-Memory-Trace-Id",
      );
    }
  });

  it("names one Core endpoint per poll key", () => {
    // A poll key is a *data plane* name. When two call sites name the same key
    // but lead to different Core endpoints, one is misnamed — the scheduler
    // runs every subscriber, so the key stops meaning "this surface". That is
    // Walk each `subscribePoll({...})` body and require the reachable
    // endpoints to agree per key.
    const files = readdirSync(path.join(ROOT, "src"))
      .filter((name) => /\.(ts|tsx)$/.test(name) && !/\.test\./.test(name))
      .map((name) => path.join(ROOT, "src", name));
    const allSources = files.map((file) => readFileSync(file, "utf8"));
    const values = files.map((file, i) => ({ file, text: allSources[i] }));

    /** Core reads reachable from a poll body, following named local thunks. */
    const endpointsOf = (body: string): string[] => {
      const found = new Set<string>();
      const scan = (fragment: string) => {
        for (const m of fragment.matchAll(/\b(get[A-Z][A-Za-z0-9]*)\s*\(/g))
          found.add(m[1]);
        for (const m of fragment.matchAll(
          /\b([a-z][A-Za-z0-9]*)\.(refresh|load)\s*\(/g,
        ))
          found.add(`${m[1]}.${m[2]}`);
      };
      scan(body);
      for (const m of body.matchAll(
        /\b(?:run|fetch):\s*([A-Za-z_$][\w$]*)\b/g,
      )) {
        for (const { text } of values) {
          const decl = new RegExp(
            `(?:async\\s+)?function\\s+${m[1]}\\s*\\(|const\\s+${m[1]}\\s*=`,
          ).exec(text);
          if (!decl) continue;
          scan(text.slice(decl.index, decl.index + 1200));
        }
      }
      return [...found].sort();
    };

    const readersByKey = new Map<string, Map<string, string>>();
    for (const { file, text } of values) {
      let index = text.indexOf("subscribePoll({");
      while (index >= 0) {
        const open = text.indexOf("{", index);
        let depth = 0;
        let end = open;
        for (let i = open; i < text.length; i += 1) {
          if (text[i] === "{") depth += 1;
          else if (text[i] === "}") {
            depth -= 1;
            if (depth === 0) {
              end = i;
              break;
            }
          }
        }
        const body = text.slice(open, end + 1);
        const key = /key:\s*"([a-z-]+)"/.exec(body)?.[1];
        if (key) {
          const endpoints = endpointsOf(body).join("+") || "unknown";
          const perKey = readersByKey.get(key) ?? new Map<string, string>();
          perKey.set(endpoints, path.basename(file));
          readersByKey.set(key, perKey);
        }
        index = text.indexOf("subscribePoll({", end);
      }
    }
    const conflicts = [...readersByKey.entries()]
      .filter(([, readers]) => readers.size > 1)
      .map(
        ([key, readers]) =>
          `${key}: ${[...readers.entries()]
            .map(([r, f]) => `${r} (${f})`)
            .join(" vs ")}`,
      );
    expect(conflicts).toEqual([]);
  });
});
