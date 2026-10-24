// @vitest-environment jsdom

import type { ImportJob, SourceResponse } from "@one-memory/contracts";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  LANGUAGE_STORAGE_KEY,
  LanguageProvider,
  useGuiLanguage,
} from "./gui-language";
import { ImportProgressStrip } from "./ImportProgressStrip";

function job(overrides: Partial<ImportJob> = {}): ImportJob {
  return {
    job_id: "job-1",
    user_id: "local",
    source_id: "cursor",
    status: "running",
    phase: "emit",
    checkpoint: null,
    discovered: 1240,
    discovered_targets: 18,
    emitted: 300,
    stored: 300,
    revised: 0,
    merged: 0,
    skipped: 0,
    errors: 0,
    created_at: "2026-09-04T00:00:01Z",
    updated_at: "2026-09-04T00:00:02Z",
    ...overrides,
  };
}

function source(
  sourceId: string,
  displayName: string,
  overrides: Partial<SourceResponse> = {},
): SourceResponse {
  return {
    source_id: sourceId,
    display_name: displayName,
    storage_kinds: ["jsonl"],
    available: true,
    authorized: true,
    detected: true,
    root_label: null,
    candidate_path: `~/.${sourceId}`,
    candidate_kind: "default",
    imported_messages: 0,
    imported_conversations: 0,
    capabilities: {
      historical_import: "implemented",
      incremental_import: "implemented",
      live_sync: "not_implemented",
      recall: "not_implemented",
      installer: "not_implemented",
      verification: "real_replayed",
    },
    sync_enabled: false,
    sync_completeness: "additions_only",
    ...overrides,
  };
}

const sources = [source("cursor", "Cursor"), source("claude", "Claude Code")];
const confirmedAt = "2026-09-14T02:20:30.000Z";
const staleSnapshot = { confirmedAt, stale: true };

function deferred() {
  let resolve!: (value?: unknown) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<unknown>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

describe("ImportProgressStrip", () => {
  afterEach(cleanup);

  it("translates user-facing progress and live settings CTA without invoking actions", async () => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
    const open = vi.fn();
    function Switch() {
      const { setPreference } = useGuiLanguage();
      return (
        <button type="button" onClick={() => void setPreference("zh-CN")}>
          Cambiar idioma
        </button>
      );
    }
    const view = render(
      <LanguageProvider>
        <Switch />
        <ImportProgressStrip
          userFacing
          jobs={[job()]}
          sources={[
            source("cursor", "Cursor", { imported_conversations: 12345 }),
          ]}
          backfilling={false}
          onOpenSources={open}
        />
      </LanguageProvider>,
    );
    expect((await screen.findByRole("status")).textContent).toContain(
      "12.345 conversaciones",
    );
    view.rerender(
      <LanguageProvider>
        <Switch />
        <ImportProgressStrip
          userFacing
          jobs={[job({ status: "failed" })]}
          sources={sources}
          backfilling={false}
          onOpenSources={open}
          onConfigureModel={open}
        />
      </LanguageProvider>,
    );
    await screen.findByRole("button", { name: "Ir a configuración" });
    expect(open).not.toHaveBeenCalled();
    await userEvent.click(
      screen.getByRole("button", { name: "Cambiar idioma" }),
    );
    await screen.findByRole("button", { name: "去设置" });
    expect(open).not.toHaveBeenCalled();
    localStorage.clear();
  });

  it("keeps daily sync silent in the user-facing shell", () => {
    const { container } = render(
      <ImportProgressStrip
        userFacing
        jobs={[job({ operation: "sync" })]}
        sources={sources}
        backfilling={false}
        onOpenSources={vi.fn()}
      />,
    );
    expect(container.textContent).toBe("");
  });

  it("shows one plain sentence for first history import without details", () => {
    render(
      <ImportProgressStrip
        userFacing
        jobs={[job()]}
        sources={sources}
        backfilling={false}
        onOpenSources={vi.fn()}
      />,
    );
    expect(screen.getByRole("status").textContent).toContain(
      "正在导入你的历史对话",
    );
    expect(screen.queryByText("查看进度与详情")).toBeNull();
  });

  it("import jobs and backfill preparation alone do not assert active generation", () => {
    const { container, rerender } = render(
      <ImportProgressStrip
        jobs={[job()]}
        sources={sources}
        backfilling
        onOpenSources={vi.fn()}
      />,
    );
    expect(
      container.querySelector(".import-strip-headline")?.textContent,
    ).toContain("正在导入对话");
    expect(container.textContent).not.toContain("更多记忆会陆续出现");
    rerender(
      <ImportProgressStrip
        jobs={[job({ status: "completed" })]}
        sources={sources}
        backfilling
        onOpenSources={vi.fn()}
      />,
    );
    expect(
      container.querySelector(".import-strip-headline")?.textContent,
    ).toContain("正在准备整理");
    expect(container.textContent).not.toContain("正在沉淀记忆");
  });

  it.each(["failed", "processing"] as const)(
    "shows %s generation separately from active imports and preserves every failure",
    (phase) => {
      const { container } = render(
        <ImportProgressStrip
          jobs={[job()]}
          sources={sources}
          backfilling
          onOpenSources={vi.fn()}
          arrival={{
            importing: true,
            importedMessages: 7865,
            importedConversations: 30,
            memoryCount: 0,
            phase,
            blockedStages: [
              {
                kind: "extract",
                reason: "hy_memory_model_request_failed",
                count: 1,
              },
              {
                kind: "extract",
                reason: "hy_memory_input_exceeds_window",
                count: 5,
              },
            ],
          }}
        />,
      );
      const headline = container.querySelector(
        ".import-strip-headline",
      )?.textContent;
      expect(headline).toContain(
        phase === "processing" ? "正在沉淀记忆" : "待继续",
      );
      expect(headline).toContain("0 条记忆");
      expect(container.querySelectorAll(".import-strip-pulse")).toHaveLength(
        phase === "processing" ? 1 : 0,
      );
      expect(container.querySelector("details")?.textContent).toContain(
        "正在导入 Cursor",
      );
      expect(container.querySelector("details")?.textContent).toContain(
        "模型请求未成功",
      );
      expect(container.querySelector("details")?.textContent).toContain(
        "这段对话含过长原文，暂未整理；原文已保留",
      );
      // Named in the reader's language, never in the pipeline's.
      expect(container.querySelector("details")?.textContent).not.toContain(
        "hy_memory_",
      );
      expect(screen.queryByRole("alert")).toBeNull();
    },
  );

  it("shows one calm outer band and leaves partial errors in collapsed details", () => {
    const { container } = render(
      <ImportProgressStrip
        jobs={[job({ status: "completed", errors: 2 })]}
        sources={sources}
        backfilling={false}
        onOpenSources={vi.fn()}
      />,
    );
    expect(
      container.querySelector(".import-strip-headline")?.textContent,
    ).toContain("待继续");
    expect(
      container.querySelector(".import-strip-headline")?.textContent,
    ).not.toMatch(/失败|异常/);
    expect(container.querySelector("details")?.open).toBe(false);
    expect(container.querySelector("details")?.textContent).toContain(
      "2 条异常",
    );
    expect(container.querySelector(".import-strip-pulse")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows active copy once while preserving separate receipts", () => {
    const { container } = render(
      <ImportProgressStrip
        jobs={[
          job(),
          job({
            job_id: "other",
            source_id: "claude",
            status: "completed",
            errors: 2,
          }),
        ]}
        sources={sources}
        arrival={{
          importing: true,
          importedMessages: 300,
          importedConversations: 2,
          phase: "processing",
        }}
        backfilling
        onOpenSources={vi.fn()}
      />,
    );
    expect(screen.getAllByText("正在沉淀记忆")).toHaveLength(1);
    expect(screen.getByText("先整理近期对话，更多记忆会陆续出现")).toBeTruthy();
    expect(container.querySelectorAll(".import-strip-pulse")).toHaveLength(1);
    expect(container.querySelector("details")?.textContent).toContain(
      "2 条异常",
    );
    expect(
      container.querySelector(".import-strip-headline")?.textContent,
    ).not.toContain("异常");
  });

  it.each(["model-required", "failed"] as const)(
    "does not override critical %s with running import/backfill",
    (phase) => {
      const { container } = render(
        <ImportProgressStrip
          jobs={[job()]}
          sources={sources}
          backfilling
          onOpenSources={vi.fn()}
          arrival={{
            importing: true,
            importedMessages: 300,
            importedConversations: 2,
            phase,
          }}
          onConfigureModel={vi.fn()}
        />,
      );
      expect(
        container.querySelector(".import-strip-headline")?.textContent,
      ).not.toContain("正在沉淀");
      expect(container.querySelector(".import-strip-pulse")).toBeNull();
      expect(
        screen.getByRole("button", {
          name: phase === "model-required" ? "设置模型" : "继续整理",
        }),
      ).toBeTruthy();
    },
  );

  it("distinguishes zero-addition sync from completed history and preserves source totals", () => {
    render(
      <ImportProgressStrip
        jobs={[
          job({
            job_id: "history",
            operation: "history",
            status: "completed",
            stored: 81614,
          }),
          job({
            job_id: "sync",
            operation: "sync",
            stored: 0,
            merged: 81614,
            discovered: 0,
            created_at: "2026-09-14T00:00:00Z",
          }),
        ]}
        sources={[
          source("cursor", "Cursor", {
            imported_messages: 81614,
            imported_conversations: 1761,
          }),
        ]}
        backfilling={false}
        onOpenSources={vi.fn()}
      />,
    );
    const text = screen.getByRole("status").textContent;
    expect(text).toContain("正在检查 Cursor 的新增对话");
    expect(text).toContain("本次新增 0 条原文");
    expect(text).toContain("累计已保存：Cursor 81,614 条原文");
    expect(text).toContain("1,761 个会话");
    expect(text).toContain("81,614 条已存在");
    expect(text).not.toMatch(/正在导入|0 条原文已保存|本次新增 81,614/);
  });

  it("keeps mixed history and sync counters separate", () => {
    render(
      <ImportProgressStrip
        jobs={[
          job({ operation: "sync", stored: 0 }),
          job({
            job_id: "history",
            source_id: "claude",
            operation: "history",
            stored: 7,
          }),
        ]}
        sources={sources}
        backfilling={false}
        onOpenSources={vi.fn()}
      />,
    );
    const text = screen.getByRole("status").textContent;
    expect(text).toContain("正在导入 Claude Code 的历史");
    expect(text).toContain("正在检查 Cursor 的新增对话");
    expect(text).toContain("本次新增 0 条原文");
    expect(text).toContain("7 条原文已保存");
    expect(text).not.toContain("本次新增 7");
  });

  it.each([
    ["pending", "Cursor 新增同步等待检查"],
    ["paused", "Cursor 新增同步已暂停"],
    ["interrupted", "Cursor 新增同步已中断"],
    ["failed", "Cursor 新增同步失败"],
    ["completed", "Cursor 本次新增检查已完成，仍有读取异常"],
  ] as const)(
    "names sync %s without implying unread history",
    (status, label) => {
      render(
        <ImportProgressStrip
          jobs={[
            job({
              operation: "sync",
              status,
              stored: 0,
              errors: status === "completed" ? 2 : 0,
            }),
          ]}
          sources={[source("cursor", "Cursor", { imported_messages: 81614 })]}
          backfilling={false}
          onOpenSources={vi.fn()}
        />,
      );
      const text = screen.getByRole("status").textContent;
      expect(text).toContain(label);
      expect(text).toContain("本次新增 0 条原文");
      expect(text).toContain("81,614 条原文");
      expect(text).not.toMatch(/尚未导完|没能读完|正在导入/);
    },
  );

  it.each(["pending", "paused", "interrupted", "cancelled"] as const)(
    "does not animate %s-only jobs or promise background processing",
    (status) => {
      const { container } = render(
        <ImportProgressStrip
          jobs={[job({ status })]}
          sources={sources}
          backfilling={false}
          onOpenSources={vi.fn()}
        />,
      );
      expect(
        container.querySelector(".import-strip-headline")?.textContent,
      ).toContain("待继续");
      expect(container.querySelector(".import-strip-pulse")).toBeNull();
      expect(
        screen.queryByText("先整理近期对话，更多记忆会陆续出现"),
      ).toBeNull();
      expect(
        screen.queryByText("存量数据较大，为保证质量，正在后台分批整理"),
      ).toBeNull();
    },
  );

  it("opens all supplied exact issues and task receipts without triggering processing", async () => {
    const open = vi.fn();
    const { container } = render(
      <ImportProgressStrip
        jobs={[job({ status: "completed", errors: 2 })]}
        sources={sources}
        backfilling={false}
        onOpenSources={open}
        issues={[
          {
            issue_id: 1,
            job_id: "job-1",
            kind: "error",
            reason: "source_permission_changed",
            conversation_id: null,
            created_at: confirmedAt,
          },
        ]}
      />,
    );
    const details = container.querySelector("details");
    expect(details?.open).toBe(false);
    await userEvent.click(screen.getByText("查看进度与详情"));
    expect(details?.open).toBe(true);
    // Said in the reader's language. A raw reason code, a job id or an ISO
    // timestamp in here is what this test exists to prevent.
    expect(details?.textContent).toContain("这个来源的读取授权变了");
    expect(details?.textContent).not.toContain("source_permission_changed");
    expect(details?.textContent).not.toContain(confirmedAt);
    expect(details?.textContent).not.toContain("job-1");
    expect(screen.getByRole("list", { name: "完整任务回执" })).toBeTruthy();
    expect(details?.textContent).toContain("已完成");
    expect(details?.textContent).not.toContain("completed");
    expect(open).not.toHaveBeenCalled();
  });

  it("makes authentication failure actionable when no generation task is running", async () => {
    const configure = vi.fn();
    const { container } = render(
      <ImportProgressStrip
        jobs={[job()]}
        sources={sources}
        backfilling
        onOpenSources={vi.fn()}
        onConfigureModel={configure}
        arrival={{
          importing: true,
          importedMessages: 300,
          importedConversations: 2,
          phase: "failed",
          blockedStages: [
            {
              kind: "extract",
              reason: "hy_memory_model_authentication_failed",
              count: 1,
            },
          ],
        }}
      />,
    );
    expect(
      container.querySelector(".import-strip-headline")?.textContent,
    ).toContain("待继续");
    expect(container.querySelector(".import-strip-pulse")).toBeNull();
    expect(container.querySelector("details")?.textContent).toContain(
      "模型认证失败。更新 key 后",
    );
    await userEvent.click(screen.getByRole("button", { name: "设置模型" }));
    expect(configure).toHaveBeenCalledOnce();
  });

  it("keeps stale sync counts as a last receipt without claiming a live check", () => {
    const { container } = render(
      <ImportProgressStrip
        jobs={[job({ operation: "sync", stored: 0 })]}
        sources={[source("cursor", "Cursor", { imported_messages: 81614 })]}
        backfilling={false}
        sourceSnapshot={staleSnapshot}
        onOpenSources={vi.fn()}
      />,
    );
    expect(screen.getByRole("status").textContent).toContain("新增同步");
    expect(screen.getByRole("status").textContent).toContain(
      "本次新增 0 条原文",
    );
    expect(screen.getByRole("status").textContent).toContain("81,614 条原文");
    expect(screen.getByRole("status").textContent).not.toMatch(
      /正在检查|正在导入|正在整理/,
    );
    expect(container.querySelector(".import-strip-pulse")).toBeNull();
  });

  it.each(["paused", "interrupted"] as const)(
    "keeps %s imports visible with recovery",
    (status) => {
      render(
        <ImportProgressStrip
          jobs={[job({ status })]}
          sources={sources}
          backfilling={false}
          onOpenSources={vi.fn()}
        />,
      );
      expect(screen.getByRole("status").textContent).toContain("尚未导完");
      expect(screen.getByRole("button", { name: "查看来源" })).toBeTruthy();
    },
  );

  it("distinguishes discovered messages from stored originals and skipped records", () => {
    render(
      <ImportProgressStrip
        jobs={[job({ stored: 280, merged: 10, skipped: 8, errors: 2 })]}
        sources={sources}
        backfilling={false}
        onOpenSources={vi.fn()}
      />,
    );
    expect(screen.getByRole("status").textContent).toContain(
      "280 条原文已保存",
    );
    expect(screen.getByRole("status").textContent).toContain("10 条已存在");
    expect(screen.getByRole("status").textContent).toContain("8 条跳过");
    expect(screen.getByRole("status").textContent).toContain("2 条异常");
  });

  it("导入进行中时报出真实来源名，计数是各来源之和", () => {
    render(
      <ImportProgressStrip
        jobs={[
          job(),
          job({ job_id: "job-2", source_id: "claude", discovered: 60 }),
        ]}
        sources={sources}
        backfilling={false}
        onOpenSources={vi.fn()}
      />,
    );

    const strip = screen.getByRole("status");
    expect(strip.textContent).toContain("Cursor");
    expect(strip.textContent).toContain("Claude Code");
    expect(strip.textContent).toContain("1,300");
  });

  it("does not include completed sources in the active group but preserves their receipts", () => {
    render(
      <ImportProgressStrip
        jobs={[
          job(),
          job({ job_id: "job-2", source_id: "claude", status: "completed" }),
        ]}
        sources={sources}
        backfilling={false}
        onOpenSources={vi.fn()}
      />,
    );

    const strip = screen.getByRole("status");
    expect(strip.textContent).toContain("Cursor");
    expect(
      strip.querySelector(".import-strip-operation")?.textContent,
    ).not.toContain("Claude Code");
    expect(strip.querySelector("details")?.textContent).toContain(
      "Claude Code",
    );
  });

  it("没有任何在跑的任务时整条消失", () => {
    render(
      <ImportProgressStrip
        jobs={[job({ status: "completed" })]}
        sources={sources}
        backfilling={false}
        onOpenSources={vi.fn()}
      />,
    );

    expect(screen.queryByRole("status")).toBeNull();
  });

  it("distinguishes backfill preparation from running generation after import", () => {
    render(
      <ImportProgressStrip
        jobs={[job({ status: "completed" })]}
        sources={sources}
        backfilling
        onOpenSources={vi.fn()}
      />,
    );

    expect(screen.getByRole("status").textContent).toContain(
      "正在准备已授权对话的整理任务",
    );
    expect(screen.getByRole("status").textContent).not.toContain(
      "正在沉淀记忆",
    );
  });

  it("失败的来源不假装还在跑，而是说出来并给出口", async () => {
    const user = userEvent.setup();
    const onOpenSources = vi.fn();
    render(
      <ImportProgressStrip
        jobs={[job({ status: "failed", errors: 2 })]}
        sources={sources}
        backfilling={false}
        onOpenSources={onOpenSources}
      />,
    );

    const strip = screen.getByRole("status");
    expect(strip.textContent).toContain("Cursor");
    expect(strip.textContent).toContain("没能读完");

    await user.click(screen.getByRole("button", { name: "查看来源" }));
    expect(onOpenSources).toHaveBeenCalledOnce();
  });

  it("同一个来源多次任务只看最新那次", () => {
    render(
      <ImportProgressStrip
        jobs={[
          job({
            job_id: "old",
            status: "failed",
            updated_at: "2026-09-04T00:00:00Z",
          }),
          job({
            job_id: "new",
            status: "running",
            updated_at: "2026-09-04T00:05:00Z",
          }),
        ]}
        sources={sources}
        backfilling={false}
        onOpenSources={vi.fn()}
      />,
    );

    const strip = screen.getByRole("status");
    expect(strip.textContent).not.toContain("没能读完");
  });

  it("keeps stale running jobs static with distinct source totals, job counts and confirmed time", () => {
    const { container } = render(
      <ImportProgressStrip
        jobs={[job({ stored: 280, merged: 10, skipped: 8, errors: 2 })]}
        sources={[
          source("cursor", "Cursor", {
            imported_messages: 900,
            imported_conversations: 21,
          }),
        ]}
        backfilling
        sourceSnapshot={staleSnapshot}
        onOpenSources={vi.fn()}
      />,
    );
    const strip = screen.getByRole("status");
    expect(strip.textContent).toContain("状态待核对");
    expect(strip.textContent).toContain("Cursor");
    expect(strip.textContent).toContain("上次确认来源");
    expect(strip.textContent).toContain("900 条原文");
    expect(strip.textContent).toContain("21 个会话");
    expect(strip.textContent).toContain("任务回执");
    expect(strip.textContent).toContain("280 条原文已保存");
    expect(strip.textContent).toContain("10 条已存在");
    expect(strip.textContent).toContain("8 条跳过");
    expect(strip.textContent).toContain("2 条异常");
    expect(strip.textContent).not.toMatch(/正在导入|正在整理|%/);
    expect(container.querySelector(".import-strip-pulse")).toBeNull();
    expect(container.querySelector("time")?.dateTime).toBe(confirmedAt);
    expect(container.querySelector("time")?.textContent).toBe(
      new Date(confirmedAt).toLocaleString("zh-CN", { hour12: false }),
    );
  });

  it("keeps a stale strip visible even when the last confirmed jobs are complete", () => {
    const { container } = render(
      <ImportProgressStrip
        jobs={[job({ status: "completed" })]}
        sources={sources}
        backfilling={false}
        sourceSnapshot={staleSnapshot}
        onOpenSources={vi.fn()}
      />,
    );
    expect(screen.getByRole("status").textContent).toContain("状态待核对");
    expect(container.querySelector(".import-strip-pulse")).toBeNull();
    expect(screen.getByRole("button", { name: "查看来源" })).toBeTruthy();
  });

  it.each([null, "not-a-date"])(
    "does not fabricate confirmed counts or time after an initial failure (%s)",
    (time) => {
      const { container } = render(
        <ImportProgressStrip
          jobs={[]}
          sources={sources}
          backfilling
          sourceSnapshot={{ confirmedAt: time, stale: true }}
          onOpenSources={vi.fn()}
        />,
      );
      const text = screen.getByRole("status").textContent;
      expect(text).toContain("状态待核对");
      expect(text).toContain("尚无已确认的来源状态");
      expect(text).not.toMatch(/0 条|已导入|正在整理|Invalid Date/);
      expect(container.querySelector("time")).toBeNull();
      expect(container.querySelector(".import-strip-pulse")).toBeNull();
    },
  );

  it.each(["failed", "paused", "interrupted"] as const)(
    "preserves stale %s source recovery without a live claim",
    async (status) => {
      const open = vi.fn();
      render(
        <ImportProgressStrip
          jobs={[job({ status })]}
          sources={sources}
          backfilling={false}
          sourceSnapshot={staleSnapshot}
          onOpenSources={open}
        />,
      );
      expect(screen.getByRole("status").textContent).toContain("Cursor");
      expect(screen.getByRole("status").textContent).toContain(
        status === "failed" ? "没能读完" : "尚未导完",
      );
      await userEvent.click(screen.getByRole("button", { name: "查看来源" }));
      expect(open).toHaveBeenCalledOnce();
    },
  );

  it("refreshes through the callback only, single-flight, without certifying stale props on resolution", async () => {
    const pending = deferred();
    const refresh = vi.fn().mockReturnValue(pending.promise);
    const open = vi.fn();
    const { container } = render(
      <ImportProgressStrip
        jobs={[job()]}
        sources={sources}
        backfilling
        sourceSnapshot={staleSnapshot}
        onRefresh={refresh}
        onOpenSources={open}
      />,
    );
    const trigger = screen.getByRole<HTMLButtonElement>("button", {
      name: "刷新核对",
    });
    expect(trigger.querySelector("svg")).toBeTruthy();
    expect(refresh).not.toHaveBeenCalled();
    expect(
      container.querySelector(".import-strip-headline")?.textContent,
    ).toContain("进度待更新");
    act(() => {
      fireEvent.click(trigger);
      fireEvent.click(trigger);
    });
    expect(refresh.mock.calls).toEqual([[]]);
    expect(trigger.disabled).toBe(true);
    expect(screen.getByRole("status").getAttribute("aria-busy")).toBe("true");
    expect(screen.getByRole("status").textContent).toContain("正在核对");
    expect(
      container.querySelector(".import-strip-headline")?.textContent,
    ).toContain("已保存内容保留，正在核对进度");
    expect(container.querySelector(".import-strip-pulse")).toBeNull();
    await act(async () =>
      pending.resolve({ arbitrary: "not a certified source snapshot" }),
    );
    expect(screen.getByRole("status").textContent).toContain("状态待核对");
    expect(screen.getByRole("status").textContent).not.toMatch(
      /正在导入|正在整理/,
    );
    expect(container.querySelector("time")?.dateTime).toBe(confirmedAt);
    expect(trigger.disabled).toBe(false);
    expect(
      container.querySelector(".import-strip-headline")?.textContent,
    ).toContain("进度待更新");
    expect(
      container.querySelector(".import-strip-headline")?.textContent,
    ).not.toContain("正在");
    expect(open).not.toHaveBeenCalled();
  });

  it("keeps the previous time and counts after refresh failure, and recovers only from fresh props", async () => {
    const refresh = vi.fn().mockRejectedValueOnce(new Error("本地服务不可用"));
    const props = {
      jobs: [job()],
      sources,
      backfilling: true,
      sourceSnapshot: staleSnapshot,
      onRefresh: refresh,
      onOpenSources: vi.fn(),
    };
    const { container, rerender } = render(<ImportProgressStrip {...props} />);
    await userEvent.click(screen.getByRole("button", { name: "刷新核对" }));
    expect(container.querySelector("details")?.textContent).toContain(
      "核对失败",
    );
    expect(container.querySelector("details")?.textContent).toContain(
      "本地服务不可用",
    );
    expect(screen.queryByRole("alert")).toBeNull();
    expect(container.querySelector("time")?.dateTime).toBe(confirmedAt);
    expect(screen.getByRole("status").textContent).toContain(
      "300 条原文已保存",
    );
    refresh.mockResolvedValueOnce(undefined);
    await userEvent.click(screen.getByRole("button", { name: "刷新核对" }));
    expect(refresh).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(container.querySelector(".import-strip-refresh-error")).toBeNull();
    expect(screen.getByRole("status").textContent).toContain("状态待核对");
    rerender(
      <ImportProgressStrip
        {...props}
        sourceSnapshot={{ confirmedAt: "2026-09-14T02:21:00Z", stale: false }}
      />,
    );
    expect(screen.getByRole("status").textContent).toContain("正在导入");
    expect(screen.getByRole("status").textContent).not.toContain("状态待核对");
    expect(container.querySelector(".import-strip-pulse")).toBeTruthy();
  });

  it("does not let an old refresh rejection overwrite a newer confirmed snapshot", async () => {
    const pending = deferred();
    const props = {
      jobs: [job()],
      sources,
      backfilling: false,
      onRefresh: vi.fn().mockReturnValue(pending.promise),
      onOpenSources: vi.fn(),
    };
    const { rerender } = render(
      <ImportProgressStrip {...props} sourceSnapshot={staleSnapshot} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "刷新核对" }));
    rerender(
      <ImportProgressStrip
        {...props}
        sourceSnapshot={{ confirmedAt: "2026-09-14T02:22:00Z", stale: false }}
      />,
    );
    await act(async () => pending.reject(new Error("old request failed")));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("status").textContent).toContain("正在导入");
  });

  it("handles unknown refresh errors honestly and does not refresh on unmount", async () => {
    const pending = deferred();
    const refresh = vi
      .fn()
      .mockRejectedValueOnce(null)
      .mockReturnValueOnce(pending.promise);
    const { container, unmount } = render(
      <ImportProgressStrip
        jobs={[]}
        sources={[]}
        backfilling={false}
        sourceSnapshot={{ confirmedAt: null, stale: true }}
        onRefresh={refresh}
        onOpenSources={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "刷新核对" }));
    expect(container.querySelector("details")?.textContent).toContain(
      "核对失败",
    );
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "刷新核对" }));
    unmount();
    await act(async () => pending.reject(new Error("unmounted")));
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it("shows completed jobs with read errors rather than hiding the strip", () => {
    const { container } = render(
      <ImportProgressStrip
        jobs={[job({ status: "completed", errors: 2, stored: 280 })]}
        sources={sources}
        backfilling={false}
        onOpenSources={vi.fn()}
      />,
    );
    const text = screen.getByRole("status").textContent;
    expect(text).toContain("Cursor");
    expect(text).toContain("280 条原文已保存");
    expect(text).toContain("2 条异常");
    expect(text).not.toContain("没能读完");
    expect(container.querySelector(".import-strip-pulse")).toBeNull();
    expect(screen.getByRole("button", { name: "查看来源" })).toBeTruthy();
  });

  it("selects the newest created job even when an older resumed job was updated later", () => {
    render(
      <ImportProgressStrip
        jobs={[
          job({
            job_id: "older-resumed",
            status: "failed",
            created_at: "2026-09-01T00:00:00Z",
            updated_at: "2026-09-14T00:00:00Z",
          }),
          job({
            job_id: "newer",
            status: "running",
            created_at: "2026-09-13T00:00:00Z",
            updated_at: "2026-09-13T00:00:01Z",
          }),
        ]}
        sources={sources}
        backfilling={false}
        onOpenSources={vi.fn()}
      />,
    );
    expect(screen.getByRole("status").textContent).toContain("正在导入 Cursor");
    expect(screen.getByRole("status").textContent).not.toContain("没能读完");
  });

  it("compares creation instants rather than ISO string order and never mutates job order", () => {
    const jobs = Object.freeze([
      job({
        job_id: "older",
        status: "failed",
        created_at: "2026-09-14T08:30:00+08:00",
        updated_at: "2026-09-14T03:00:00Z",
      }),
      job({
        job_id: "newer",
        status: "completed",
        created_at: "2026-09-14T01:00:00Z",
        updated_at: "2026-09-14T01:00:01Z",
      }),
    ]);
    render(
      <ImportProgressStrip
        jobs={jobs as ImportJob[]}
        sources={sources}
        backfilling={false}
        onOpenSources={vi.fn()}
      />,
    );
    expect(screen.queryByRole("status")).toBeNull();
    expect(jobs.map((item) => item.job_id)).toEqual(["older", "newer"]);
  });

  it("a newer clean completion keeps old errors in history, not the outer summary", () => {
    const { container } = render(
      <ImportProgressStrip
        jobs={[
          job({
            status: "completed",
            errors: 5,
            created_at: "2026-09-01T00:00:00Z",
            updated_at: "2026-09-14T00:00:00Z",
          }),
          job({
            job_id: "new",
            status: "completed",
            created_at: "2026-09-13T00:00:00Z",
          }),
          job({ job_id: "claude", source_id: "claude", status: "failed" }),
        ]}
        sources={sources}
        backfilling={false}
        onOpenSources={vi.fn()}
      />,
    );
    expect(
      container.querySelector(".import-strip-headline")?.textContent,
    ).not.toContain("Cursor");
    expect(container.querySelector("details")?.textContent).toContain("Cursor");
    expect(container.querySelector("details")?.textContent).toContain(
      "5 条异常",
    );
    expect(screen.getByRole("status").textContent).toContain(
      "Claude Code 没能读完",
    );
  });

  it("keeps the precipitation line and hides the source link and progress disclosure", () => {
    render(
      <ImportProgressStrip
        jobs={[job({ status: "completed", stored: 5171 })]}
        sources={sources}
        backfilling={false}
        onOpenSources={vi.fn()}
        showSourceLink={false}
        showProgressDetails={false}
        arrival={{
          importing: false,
          importedMessages: 5171,
          importedConversations: 511,
          memoryCount: 100,
          phase: "processing",
        }}
      />,
    );
    const status = screen.getByRole("status");
    expect(status.textContent).toContain("正在沉淀记忆");
    expect(status.textContent).toContain("先整理近期对话，更多记忆会陆续出现");
    expect(screen.queryByRole("button", { name: "查看来源" })).toBeNull();
    expect(screen.queryByText("查看进度与详情")).toBeNull();
  });
});

describe("pausing and continuing imports from the strip", () => {
  afterEach(cleanup);

  type Action = "pause" | "resume" | "cancel";
  const cursorRunning = job();
  const claudeRunning = job({
    job_id: "job-2",
    source_id: "claude",
    created_at: "2026-09-04T00:00:03Z",
  });
  const paused = (item: ImportJob) => ({
    ...item,
    status: "paused" as const,
    updated_at: "2026-09-04T00:01:00Z",
  });
  const running = (item: ImportJob) => ({
    ...item,
    status: "running" as const,
    updated_at: "2026-09-04T00:02:00Z",
  });
  const settle = (id: string, action: Action, from: ImportJob[]) => {
    const item = from.find((entry) => entry.job_id === id) as ImportJob;
    return action === "pause" ? paused(item) : running(item);
  };

  /** Stands in for the controller: a receipt replaces the job it names. */
  function Harness({
    initial,
    action,
    arrival,
    onConfigureModel,
  }: {
    initial: ImportJob[];
    action(jobId: string, action: Action): Promise<ImportJob>;
    arrival?: Parameters<typeof ImportProgressStrip>[0]["arrival"];
    onConfigureModel?(): void;
  }) {
    const [jobs, setJobs] = useState(initial);
    return (
      <ImportProgressStrip
        jobs={jobs}
        sources={sources}
        backfilling={false}
        onOpenSources={() => {}}
        arrival={arrival}
        onConfigureModel={onConfigureModel}
        onJobAction={async (jobId, next) => {
          const receipt = await action(jobId, next);
          if (receipt?.job_id)
            setJobs((all) =>
              all.map((item) =>
                item.job_id === receipt.job_id ? receipt : item,
              ),
            );
          return receipt;
        }}
      />
    );
  }

  function actionNames() {
    const strip = screen.getByRole("status");
    const actions = strip.querySelector(".import-strip-actions");
    return Array.from(actions?.querySelectorAll("button") ?? []).map(
      (button) => button.textContent,
    );
  }

  it("puts the pause button first, before setting up the model", () => {
    render(
      <Harness
        initial={[cursorRunning]}
        action={vi.fn()}
        arrival={{ phase: "model-required" } as never}
        onConfigureModel={() => {}}
      />,
    );
    const names = actionNames();
    expect(names[0]).toBe("全部暂停");
    expect(names).toContain("设置模型");
    const pause = screen.getByRole("button", { name: "全部暂停" });
    expect(pause.className).toContain("quiet");
    expect(pause.querySelector("svg")).toBeTruthy();
  });

  it("pauses every running import at once, without asking, and waits for each receipt", async () => {
    const user = userEvent.setup();
    const gate = deferred();
    const initial = [cursorRunning, claudeRunning];
    const action = vi.fn(async (id: string, next: Action) => {
      await gate.promise;
      return settle(id, next, initial);
    });
    render(<Harness initial={initial} action={action} />);
    await user.click(screen.getByRole("button", { name: "全部暂停" }));
    const busy = screen.getByRole("button", { name: "暂停中…" });
    expect((busy as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole("dialog")).toBeNull();
    await act(async () => gate.resolve());
    expect(
      await screen.findByRole("button", { name: "全部继续" }),
    ).toBeTruthy();
    expect(action.mock.calls).toEqual([
      ["job-1", "pause"],
      ["job-2", "pause"],
    ]);
    expect(screen.getByRole("status").textContent).toContain(
      "导入已暂停，已保存内容保留，随时可以继续。",
    );
  });

  it("with some paused and some running, pauses only the running ones", async () => {
    const user = userEvent.setup();
    const initial = [paused(cursorRunning), claudeRunning];
    const action = vi.fn(async (id: string, next: Action) =>
      settle(id, next, initial),
    );
    render(<Harness initial={initial} action={action} />);
    await user.click(screen.getByRole("button", { name: "全部暂停" }));
    await screen.findByRole("button", { name: "全部继续" });
    expect(action.mock.calls).toEqual([["job-2", "pause"]]);
  });

  it("continues every paused import", async () => {
    const user = userEvent.setup();
    const gate = deferred();
    const initial = [paused(cursorRunning), paused(claudeRunning)];
    const action = vi.fn(async (id: string, next: Action) => {
      await gate.promise;
      return settle(id, next, initial);
    });
    render(<Harness initial={initial} action={action} />);
    await user.click(screen.getByRole("button", { name: "全部继续" }));
    expect(
      (screen.getByRole("button", { name: "继续中…" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    await act(async () => gate.resolve());
    expect(
      await screen.findByRole("button", { name: "全部暂停" }),
    ).toBeTruthy();
    expect(action.mock.calls).toEqual([
      ["job-1", "resume"],
      ["job-2", "resume"],
    ]);
  });

  it("names a job the backend refused, and retries only that one", async () => {
    const user = userEvent.setup();
    const initial = [cursorRunning, claudeRunning];
    const action = vi
      .fn(async (id: string, next: Action) => settle(id, next, initial))
      .mockImplementationOnce(async (id, next) => settle(id, next, initial))
      .mockImplementationOnce(async () => {
        throw new Error("来源操作未完成，请核对当前状态后重试。");
      });
    render(<Harness initial={initial} action={action} />);
    await user.click(screen.getByRole("button", { name: "全部暂停" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Claude Code 未能暂停：来源操作未完成，请核对当前状态后重试。",
    );
    await user.click(screen.getByRole("button", { name: "重试暂停" }));
    await screen.findByRole("button", { name: "全部继续" });
    expect(action.mock.calls.slice(2)).toEqual([["job-2", "pause"]]);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("does not take a receipt that is still running as paused", async () => {
    const user = userEvent.setup();
    const action = vi.fn(async () => running(cursorRunning));
    render(<Harness initial={[cursorRunning]} action={action} />);
    await user.click(screen.getByRole("button", { name: "全部暂停" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Cursor 未能暂停：后台尚未确认此操作完成。",
    );
    expect(screen.getByRole("button", { name: "重试暂停" })).toBeTruthy();
  });

  it("does not take a receipt for another job", async () => {
    const user = userEvent.setup();
    const action = vi.fn(async () => paused(claudeRunning));
    render(<Harness initial={[cursorRunning]} action={action} />);
    await user.click(screen.getByRole("button", { name: "全部暂停" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Cursor 未能暂停：未收到此任务的有效回执。",
    );
  });

  it("offers nothing for new-conversation checks, or while the snapshot is stale", () => {
    const sync = job({ operation: "sync" } as Partial<ImportJob>);
    const { unmount } = render(<Harness initial={[sync]} action={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "全部暂停" })).toBeNull();
    unmount();
    render(
      <ImportProgressStrip
        jobs={[cursorRunning]}
        sources={sources}
        backfilling={false}
        onOpenSources={() => {}}
        sourceSnapshot={staleSnapshot}
        onJobAction={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "全部暂停" })).toBeNull();
  });
});
