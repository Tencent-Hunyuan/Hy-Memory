// @vitest-environment jsdom
import { webcrypto } from "node:crypto";
import type {
  ConnectSourcesResponse,
  FileImportReceipt,
  SourceResponse,
} from "@one-memory/contracts";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HistoryImport } from "./HistoryImport";
import {
  clearPendingFileUpload,
  savePendingFileUpload,
} from "./pending-file-upload";
import { useImportSources } from "./use-import-sources";

const api = vi.hoisted(() => ({
  uploadHistoryFile: vi.fn(),
  reconcileHistoryUpload: vi.fn(),
  commitHistoryFile: vi.fn(),
  getHistoryFile: vi.fn(),
  discardHistoryFile: vi.fn(),
  apiErrorCode: vi.fn(),
  historyImportIssue: vi.fn(),
  getHealth: vi.fn(),
  getSources: vi.fn(),
  getImportJobs: vi.fn(),
  getSourceConnection: vi.fn(),
  connectSources: vi.fn(),
  changeImportJob: vi.fn(),
  createImportJob: vi.fn(),
  detectSource: vi.fn(),
  setSourceSync: vi.fn(),
}));
vi.mock("./api", () => api);

const staged: FileImportReceipt = {
  upload_id: "00000000-0000-4000-8000-000000000001",
  filename: "conversations.json",
  vendor: "chatgpt",
  content_kind: "raw_conversation",
  source_id: "chatgpt-export",
  messages: 6,
  conversations: 2,
  human_messages: 3,
  agent_messages: 3,
  unknown_author_messages: 0,
  skipped_records: 0,
  ignored_files: 1,
  checksum: "file-checksum",
  created_at: "2026-09-13T10:00:00Z",
  committed: false,
  preview: [
    {
      role: "user",
      written_by: "person",
      text: "原话不是结论",
      created_at: null,
      truncated: false,
    },
  ],
};
const committed = { ...staged, committed: true };
const candidate: SourceResponse = {
  source_id: staged.source_id,
  display_name: "ChatGPT 历史包",
  available: true,
  authorized: false,
  detected: null,
  root_label: null,
  candidate_path: "/isolated/import-files/ready/chatgpt-export",
  candidate_kind: "default",
  candidate_root_id: "root:v2:committed-directory",
  location_state: "found",
  grant_revision: 0,
  root_id: null,
  sync_enabled: false,
  storage_kinds: ["jsonl"],
  imported_messages: 10,
  imported_conversations: 3,
  sync_completeness: "one_time_snapshot",
  capabilities: {
    historical_import: "implemented",
    incremental_import: "implemented",
    live_sync: "not_implemented",
    recall: "not_implemented",
    installer: "not_implemented",
    verification: "fixture_only",
  },
};
const accepted: ConnectSourcesResponse = {
  request_id: "receipt-1",
  accepted_source_ids: [staged.source_id],
  failed_sources: {},
  jobs: [],
};
const onConnect = vi.fn();
const onRefresh = vi.fn();

it("does not steal initial page focus and restores trigger focus only after closing", async () => {
  render(
    <StrictMode>
      <HistoryImport onRefresh={onRefresh} onConnect={onConnect} />
    </StrictMode>,
  );
  const trigger = screen.getByRole("button", { name: "导入历史包" });
  expect(document.activeElement).not.toBe(trigger);
  fireEvent.click(trigger);
  await screen.findByRole("dialog", { name: "导入历史包" });
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  await waitFor(() => expect(document.activeElement).toBe(trigger));
});

beforeEach(() => {
  vi.stubGlobal("crypto", webcrypto);
  window.localStorage.clear();
  api.uploadHistoryFile.mockResolvedValue(staged);
  api.commitHistoryFile.mockResolvedValue(committed);
  api.getHistoryFile.mockResolvedValue(committed);
  api.discardHistoryFile.mockResolvedValue(undefined);
  api.getHealth.mockResolvedValue({ instance_id: "isolated-core" });
  api.getImportJobs.mockResolvedValue([]);
  api.getSources.mockResolvedValue([candidate]);
  onConnect.mockResolvedValue(accepted);
  onRefresh.mockResolvedValue({ sources: [candidate] });
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  vi.unstubAllGlobals();
});

function mount() {
  return render(<HistoryImport onConnect={onConnect} onRefresh={onRefresh} />);
}

it("reopens an uncertain upload and recovers preview without reupload, commit or authorization", async () => {
  const id = "11111111-1111-4111-8111-111111111111";
  savePendingFileUpload(id);
  api.reconcileHistoryUpload.mockImplementation(async () => {
    clearPendingFileUpload(id);
    return staged;
  });
  mount();
  await userEvent.click(screen.getByRole("button", { name: "导入历史包" }));
  expect(screen.getByLabelText("历史数据文件")).toHaveProperty(
    "disabled",
    true,
  );
  await userEvent.click(screen.getByRole("button", { name: "核对上传结果" }));
  await screen.findByText("6 条文本消息");
  expect(api.reconcileHistoryUpload).toHaveBeenCalledOnce();
  expect(api.uploadHistoryFile).not.toHaveBeenCalled();
  expect(api.commitHistoryFile).not.toHaveBeenCalled();
  expect(onConnect).not.toHaveBeenCalled();
});

it("keeps an unknown upload fenced through closing and reopening", async () => {
  const emitted: Array<{ phase: string; status?: string }> = [];
  (
    window as unknown as {
      oneMemory: { logUiTrace: (input: Record<string, unknown>) => void };
    }
  ).oneMemory = {
    logUiTrace: (input) => {
      emitted.push(input as (typeof emitted)[number]);
    },
  };
  const id = "11111111-1111-4111-8111-111111111111";
  api.uploadHistoryFile.mockImplementation(async () => {
    savePendingFileUpload(id);
    throw Error("lost response");
  });
  mount();
  await userEvent.click(screen.getByRole("button", { name: "导入历史包" }));
  await userEvent.upload(
    screen.getByLabelText("历史数据文件"),
    new File(["[]"], "history.json"),
  );
  await screen.findByRole("button", { name: "核对上传结果" });
  const result = emitted.filter((item) => item.phase === "result").at(-1);
  expect(result?.status).toBe("unknown");
  expect(result?.status).not.toBe("failed");
  await userEvent.click(screen.getByRole("button", { name: "关闭历史包导入" }));
  await userEvent.click(screen.getByRole("button", { name: "导入历史包" }));
  expect(screen.getByLabelText("历史数据文件")).toHaveProperty(
    "disabled",
    true,
  );
  expect(api.uploadHistoryFile).toHaveBeenCalledOnce();
  expect(api.discardHistoryFile).not.toHaveBeenCalled();
  expect(onConnect).not.toHaveBeenCalled();
});

it("discards only the recovered uncommitted stage if recovery finishes after close", async () => {
  const id = "11111111-1111-4111-8111-111111111111";
  savePendingFileUpload(id);
  let resolve!: (receipt: FileImportReceipt) => void;
  api.reconcileHistoryUpload.mockImplementation(
    () =>
      new Promise<FileImportReceipt>((done) => {
        resolve = done;
      }),
  );
  mount();
  await userEvent.click(screen.getByRole("button", { name: "导入历史包" }));
  await userEvent.click(screen.getByRole("button", { name: "核对上传结果" }));
  await userEvent.click(screen.getByRole("button", { name: "关闭历史包导入" }));
  await act(async () => {
    clearPendingFileUpload(id);
    resolve(staged);
  });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(api.discardHistoryFile).toHaveBeenCalledExactlyOnceWith(
    staged.upload_id,
  );
  expect(api.uploadHistoryFile).not.toHaveBeenCalled();
  expect(api.commitHistoryFile).not.toHaveBeenCalled();
  expect(onConnect).not.toHaveBeenCalled();
});
async function upload() {
  await userEvent.click(screen.getByRole("button", { name: "导入历史包" }));
  await userEvent.upload(
    screen.getByLabelText("历史数据文件"),
    new File(["[]"], staged.filename, { type: "application/json" }),
  );
  await screen.findByText("6 条文本消息");
}
async function publish() {
  await userEvent.click(
    screen.getByRole("checkbox", { name: /确认保存这份文件到本机/ }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "保存文件并核对来源" }),
  );
}
async function authorize(label = "授权并开始导入") {
  await userEvent.click(
    screen.getByRole("checkbox", { name: /允许一次性读取上述来源目录/ }),
  );
  await userEvent.click(screen.getByRole("button", { name: label }));
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

it("requires preview, publication, actual directory review and independent source consent", async () => {
  const mutable = { ...candidate };
  onRefresh.mockResolvedValue({ sources: [mutable] });
  mount();
  await upload();
  expect(screen.getByText("原话不是结论")).toBeTruthy();
  expect(screen.getByText(staged.filename)).toBeTruthy();
  expect(api.commitHistoryFile).not.toHaveBeenCalled();
  expect(onRefresh).not.toHaveBeenCalled();
  expect(onConnect).not.toHaveBeenCalled();
  expect(
    screen.getByRole("button", { name: "保存文件并核对来源" }),
  ).toHaveProperty("disabled", true);
  await publish();
  expect(api.commitHistoryFile).toHaveBeenCalledWith(
    staged.upload_id,
    expect.any(String),
  );
  expect(onRefresh).toHaveBeenCalledTimes(1);
  expect(screen.getByText(candidate.candidate_path)).toBeTruthy();
  expect(screen.getByText(candidate.candidate_root_id as string)).toBeTruthy();
  expect(screen.getByText(/目录中的全部已提交历史包/)).toBeTruthy();
  expect(screen.getByText(/模型处理、持续同步、外部调用及安装/)).toBeTruthy();
  expect(
    screen.getByRole("checkbox", { name: /允许一次性读取上述来源目录/ }),
  ).toHaveProperty("checked", false);
  expect(onConnect).not.toHaveBeenCalled();
  mutable.candidate_root_id = "root:replaced-after-render";
  await authorize();
  expect(onConnect).toHaveBeenCalledWith([staged.source_id], [candidate], {
    importUploadId: staged.upload_id,
  });
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("accepts a directory the user picked themselves after a committed upload", async () => {
  const selected = {
    ...candidate,
    candidate_kind: "user_selected" as const,
    candidate_path: "/chosen/chatgpt-export",
  };
  onRefresh.mockResolvedValue({ sources: [selected] });
  mount();
  await upload();
  await publish();

  expect(await screen.findByText(selected.candidate_path)).toBeTruthy();
  await authorize();
  expect(onConnect).toHaveBeenCalledWith([staged.source_id], [selected], {
    importUploadId: staged.upload_id,
  });
});

it.each([
  { sources: [] },
  { sources: [{ ...candidate, candidate_root_id: undefined }] },
  { sources: [{ ...candidate, available: false }] },
  { sources: [{ ...candidate, location_state: "not_found" }] },
  { sources: [{ ...candidate, candidate_path: "" }] },
  { sources: [candidate, candidate] },
])(
  "fails closed for unavailable or ambiguous committed metadata %#",
  async ({ sources }) => {
    onRefresh.mockResolvedValue({ sources });
    mount();
    await upload();
    await publish();
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(
      screen.queryByRole("checkbox", { name: /允许一次性读取/ }),
    ).toBeNull();
    expect(onConnect).not.toHaveBeenCalled();
    expect(api.commitHistoryFile).toHaveBeenCalledTimes(1);
  },
);

it("retains a committed file through refresh failure and recovers receipt-first", async () => {
  onRefresh.mockRejectedValueOnce(new Error("offline"));
  mount();
  await upload();
  await publish();
  expect((await screen.findByRole("alert")).textContent).toContain(
    "文件已保存",
  );
  expect(screen.getByText("6 条文本消息")).toBeTruthy();
  expect(onConnect).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "重新核对来源" }));
  expect(api.getHistoryFile).toHaveBeenCalledWith(staged.upload_id);
  expect(api.getHistoryFile.mock.invocationCallOrder[0]).toBeLessThan(
    onRefresh.mock.invocationCallOrder[1],
  );
  expect(api.commitHistoryFile).toHaveBeenCalledTimes(1);
  expect(
    screen.getByRole("checkbox", { name: /允许一次性读取/ }),
  ).toHaveProperty("checked", false);
  await authorize();
  expect(onConnect).toHaveBeenCalledWith([staged.source_id], [candidate], {
    importUploadId: staged.upload_id,
  });
});

it("reads an uncertain commit receipt before retry without duplicating a committed upload", async () => {
  const emitted: Array<{ phase: string; status?: string }> = [];
  (
    window as unknown as {
      oneMemory: { logUiTrace: (input: Record<string, unknown>) => void };
    }
  ).oneMemory = {
    logUiTrace: (input) => {
      emitted.push(input as (typeof emitted)[number]);
    },
  };
  api.commitHistoryFile.mockRejectedValueOnce(new Error("response lost"));
  mount();
  await upload();
  await publish();
  await screen.findByRole("alert");
  const result = emitted.filter((item) => item.phase === "result").at(-1);
  expect(result?.status).toBe("unknown");
  expect(result?.status).not.toBe("failed");
  expect(onRefresh).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: "核对文件保存结果" }),
  );
  expect(api.getHistoryFile).toHaveBeenCalledWith(staged.upload_id);
  expect(api.commitHistoryFile).toHaveBeenCalledTimes(1);
  expect(onConnect).not.toHaveBeenCalled();
  await authorize();
  expect(onConnect).toHaveBeenCalledTimes(1);
});

it("never reposts after failed receipt recovery and reconfirms a known uncommitted save", async () => {
  api.commitHistoryFile.mockRejectedValueOnce(new Error("response lost"));
  api.getHistoryFile
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(staged);
  mount();
  await upload();
  await publish();
  await userEvent.click(
    screen.getByRole("button", { name: "核对文件保存结果" }),
  );
  expect(api.commitHistoryFile).toHaveBeenCalledTimes(1);
  expect(onRefresh).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: "核对文件保存结果" }),
  );
  expect(screen.getByRole("checkbox", { name: /确认保存/ })).toHaveProperty(
    "checked",
    false,
  );
  await publish();
  expect(api.getHistoryFile).toHaveBeenCalledTimes(2);
  expect(api.commitHistoryFile).toHaveBeenCalledTimes(2);
});

it.each(["upload_id", "source_id", "checksum"])(
  "rejects a recovered receipt with changed %s",
  async (key) => {
    api.commitHistoryFile.mockRejectedValueOnce(new Error("response lost"));
    api.getHistoryFile.mockResolvedValue({ ...committed, [key]: "other" });
    mount();
    await upload();
    await publish();
    await userEvent.click(
      screen.getByRole("button", { name: "核对文件保存结果" }),
    );
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(api.commitHistoryFile).toHaveBeenCalledTimes(1);
    expect(onRefresh).not.toHaveBeenCalled();
    expect(onConnect).not.toHaveBeenCalled();
  },
);

it("requires explicit failed-source retryFailed consent", async () => {
  onConnect.mockResolvedValueOnce({
    ...accepted,
    accepted_source_ids: [],
    failed_sources: { [staged.source_id]: "source_unavailable" },
  });
  mount();
  await upload();
  await publish();
  await authorize();
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(
    screen.getByRole("checkbox", { name: /允许一次性读取/ }),
  ).toHaveProperty("checked", false);
  expect(onConnect).toHaveBeenCalledTimes(1);
  await authorize("重试此来源");
  expect(onConnect).toHaveBeenLastCalledWith([staged.source_id], [candidate], {
    importUploadId: staged.upload_id,
    retryFailed: true,
  });
  expect(api.commitHistoryFile).toHaveBeenCalledTimes(1);
});

it("keeps a rejected-source retry explicit after rechecking the same directory", async () => {
  onConnect.mockResolvedValueOnce({
    ...accepted,
    accepted_source_ids: [],
    failed_sources: { [staged.source_id]: "source_unavailable" },
  });
  mount();
  await upload();
  await publish();
  await authorize();
  await userEvent.click(screen.getByRole("button", { name: "重新核对来源" }));
  expect(
    screen.getByRole("checkbox", { name: /允许一次性读取/ }),
  ).toHaveProperty("checked", false);
  expect(onConnect).toHaveBeenCalledTimes(1);
  await authorize("重试此来源");
  expect(onConnect).toHaveBeenLastCalledWith([staged.source_id], [candidate], {
    importUploadId: staged.upload_id,
    retryFailed: true,
  });
});

it("uses ordinary receipt recovery after an uncertain connect, never retryFailed", async () => {
  onConnect.mockRejectedValueOnce(new Error("response lost"));
  mount();
  await upload();
  await publish();
  await authorize();
  await screen.findByRole("alert");
  await authorize("核对连接结果");
  expect(onConnect).toHaveBeenLastCalledWith([staged.source_id], [candidate], {
    importUploadId: staged.upload_id,
  });
  expect(api.commitHistoryFile).toHaveBeenCalledTimes(1);
});

it("shows a replacement root only after rechecking and requires fresh consent", async () => {
  onConnect.mockRejectedValueOnce(new Error("source_root_changed"));
  const replacement = {
    ...candidate,
    candidate_root_id: "root:new",
    candidate_path: "/isolated/new",
  };
  onRefresh
    .mockResolvedValueOnce({ sources: [candidate] })
    .mockResolvedValueOnce({ sources: [replacement] });
  mount();
  await upload();
  await publish();
  await authorize();
  expect(screen.getByText(candidate.candidate_path)).toBeTruthy();
  expect(onRefresh).toHaveBeenCalledTimes(1);
  await userEvent.click(screen.getByRole("button", { name: "重新核对来源" }));
  expect(screen.getByText(replacement.candidate_path)).toBeTruthy();
  expect(
    screen.getByRole("checkbox", { name: /允许一次性读取/ }),
  ).toHaveProperty("checked", false);
  expect(onConnect).toHaveBeenCalledTimes(1);
  await authorize();
  expect(onConnect).toHaveBeenLastCalledWith(
    [staged.source_id],
    [replacement],
    { importUploadId: staged.upload_id },
  );
});

it("locks double clicks through commit, refresh and connect", async () => {
  const save = deferred<FileImportReceipt>();
  const refresh = deferred<{ sources: SourceResponse[] }>();
  const connect = deferred<ConnectSourcesResponse>();
  api.commitHistoryFile.mockReturnValue(save.promise);
  onRefresh.mockReturnValue(refresh.promise);
  onConnect.mockReturnValue(connect.promise);
  mount();
  await upload();
  await userEvent.click(screen.getByRole("checkbox", { name: /确认保存/ }));
  const submit = screen.getByRole("button", { name: "保存文件并核对来源" });
  act(() => {
    fireEvent.click(submit);
    fireEvent.click(submit);
  });
  expect(api.commitHistoryFile).toHaveBeenCalledTimes(1);
  expect(
    screen.getByRole("radio", { name: "已有记忆" }).matches(":disabled"),
  ).toBe(true);
  await act(async () => save.resolve(committed));
  expect(onRefresh).toHaveBeenCalledTimes(1);
  expect(onConnect).not.toHaveBeenCalled();
  await act(async () => refresh.resolve({ sources: [candidate] }));
  await userEvent.click(
    screen.getByRole("checkbox", { name: /允许一次性读取/ }),
  );
  const confirm = screen.getByRole("button", { name: "授权并开始导入" });
  act(() => {
    fireEvent.click(confirm);
    fireEvent.click(confirm);
  });
  expect(onConnect).toHaveBeenCalledTimes(1);
  await act(async () => connect.resolve(accepted));
});

it.each(["close", "unmount"])(
  "discards late staged upload after %s without reviving the panel",
  async (action) => {
    const pending = deferred<FileImportReceipt>();
    api.uploadHistoryFile.mockReturnValue(pending.promise);
    const view = mount();
    await userEvent.click(screen.getByRole("button", { name: "导入历史包" }));
    await userEvent.upload(
      screen.getByLabelText("历史数据文件"),
      new File(["[]"], "pending.json"),
    );
    if (action === "unmount") view.unmount();
    else {
      await userEvent.click(
        screen.getByRole("button", { name: "关闭历史包导入" }),
      );
      await userEvent.click(screen.getByRole("button", { name: "导入历史包" }));
    }
    await act(async () => pending.resolve(staged));
    expect(api.discardHistoryFile).toHaveBeenCalledWith(staged.upload_id);
    expect(screen.queryByText("6 条文本消息")).toBeNull();
    expect(api.commitHistoryFile).not.toHaveBeenCalled();
    expect(onConnect).not.toHaveBeenCalled();
  },
);

it.each(["close", "unmount"])(
  "keeps a late committed file after %s without refreshing or granting",
  async (action) => {
    const pending = deferred<FileImportReceipt>();
    api.commitHistoryFile.mockReturnValue(pending.promise);
    const view = mount();
    await upload();
    await publish();
    if (action === "unmount") view.unmount();
    else
      await userEvent.click(
        screen.getByRole("button", { name: "关闭历史包导入" }),
      );
    await act(async () => pending.resolve(committed));
    expect(onRefresh).not.toHaveBeenCalled();
    expect(onConnect).not.toHaveBeenCalled();
    expect(api.discardHistoryFile).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  },
);

it("ignores late source metadata after cancellation", async () => {
  const pending = deferred<{ sources: SourceResponse[] }>();
  onRefresh.mockReturnValue(pending.promise);
  mount();
  await upload();
  await publish();
  await userEvent.click(screen.getByRole("button", { name: "关闭历史包导入" }));
  await userEvent.click(screen.getByRole("button", { name: "导入历史包" }));
  await act(async () => pending.resolve({ sources: [candidate] }));
  expect(screen.queryByText(candidate.candidate_path)).toBeNull();
  expect(onConnect).not.toHaveBeenCalled();
  expect(api.discardHistoryFile).not.toHaveBeenCalled();
});

it("ignores late connection success without closing a reopened dialog", async () => {
  const pending = deferred<ConnectSourcesResponse>();
  onConnect.mockReturnValue(pending.promise);
  mount();
  await upload();
  await publish();
  await authorize();
  await userEvent.click(screen.getByRole("button", { name: "关闭历史包导入" }));
  await userEvent.click(screen.getByRole("button", { name: "导入历史包" }));
  await act(async () => pending.resolve(accepted));
  expect(screen.getByRole("dialog")).toBeTruthy();
  expect(screen.queryByText("6 条文本消息")).toBeNull();
  expect(onConnect).toHaveBeenCalledTimes(1);
});

it("cancels before final consent without granting or discarding a committed file", async () => {
  mount();
  await upload();
  await publish();
  await userEvent.click(screen.getByRole("button", { name: "暂不授权" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(onConnect).not.toHaveBeenCalled();
  expect(api.discardHistoryFile).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "导入历史包" })).toBe(
    document.activeElement,
  );
});

it("clears committed scope and consent on kind changes without deleting the file", async () => {
  mount();
  await upload();
  await publish();
  await userEvent.click(
    screen.getByRole("checkbox", { name: /允许一次性读取/ }),
  );
  await userEvent.click(screen.getByRole("radio", { name: "已有记忆" }));
  expect(screen.queryByText(candidate.candidate_path)).toBeNull();
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(api.discardHistoryFile).not.toHaveBeenCalled();
  expect(onConnect).not.toHaveBeenCalled();
});

it("previews memory without inventing authors and invalidates consent on editing", async () => {
  api.uploadHistoryFile.mockResolvedValue({
    ...staged,
    vendor: "memory-content",
    content_kind: "memory_content",
    source_id: "memory-content-export",
    messages: 1,
    conversations: 1,
    human_messages: 0,
    agent_messages: 0,
    unknown_author_messages: 1,
    preview: [
      {
        role: "memory",
        written_by: "unknown",
        text: "可能适合当前项目",
        created_at: null,
        truncated: false,
      },
    ],
  });
  mount();
  await userEvent.click(screen.getByRole("button", { name: "导入历史包" }));
  await userEvent.click(screen.getByRole("radio", { name: "已有记忆" }));
  await userEvent.click(screen.getByRole("button", { name: "粘贴内容" }));
  await userEvent.type(
    screen.getByLabelText("已有记忆原文"),
    "可能适合当前项目",
  );
  await userEvent.click(screen.getByRole("button", { name: "预览内容" }));
  expect(await screen.findByText("1 条已有记忆记录")).toBeTruthy();
  expect(screen.getByText("作者未知")).toBeTruthy();
  expect(screen.getByText(/原始对话未提供/)).toBeTruthy();
  expect(api.uploadHistoryFile.mock.calls[0][1]).toBe("memory_content");
  await userEvent.click(screen.getByRole("checkbox", { name: /确认保存/ }));
  await userEvent.click(screen.getByRole("button", { name: "编辑原文" }));
  expect(screen.queryByRole("checkbox")).toBeNull();
  await userEvent.type(screen.getByLabelText("已有记忆原文"), "变更");
  await userEvent.click(screen.getByRole("button", { name: "关闭历史包导入" }));
  expect(api.discardHistoryFile).toHaveBeenCalledWith(staged.upload_id);
});

it("shows structured manual parsing errors without private exception text", async () => {
  api.uploadHistoryFile.mockRejectedValue(
    new Error("unrelated private exception"),
  );
  api.apiErrorCode.mockReturnValue("generic_import_invalid");
  api.historyImportIssue.mockReturnValue({
    code: "unknown_role",
    location: "line:2",
    excerpt: "Tool: 原文片段",
  });
  mount();
  await userEvent.click(screen.getByRole("button", { name: "导入历史包" }));
  await userEvent.upload(
    screen.getByLabelText("历史数据文件"),
    new File(["User: 内容\nTool: 原文片段"], "chat.txt"),
  );
  expect(await screen.findByText("第 2 行：无法确认说话人")).toBeTruthy();
  expect(screen.getByText("Tool: 原文片段")).toBeTruthy();
  expect(screen.queryByText("unrelated private exception")).toBeNull();
});

it("invalidates the old preview when a replacement is oversized", async () => {
  mount();
  await upload();
  await userEvent.click(screen.getByRole("checkbox", { name: /确认保存/ }));
  const oversized = new File(["[]"], "too-large.json");
  Object.defineProperty(oversized, "size", { value: 129 * 1024 * 1024 });
  await userEvent.upload(screen.getByLabelText("历史数据文件"), oversized);
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.queryByText("6 条文本消息")).toBeNull();
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(
    screen.getByRole("button", { name: "保存文件并核对来源" }),
  ).toHaveProperty("disabled", true);
  expect(api.discardHistoryFile).toHaveBeenCalledWith(staged.upload_id);
  expect(onConnect).not.toHaveBeenCalled();
});

it.each([
  [
    "duplicate_export_identity",
    "文件里有重复的会话或消息身份，未导入任何内容。",
  ],
  [
    "export_record_invalid",
    "文件中有超长或无法读取的对话记录，未导入任何内容。",
  ],
])("explains %s without exposing parser input", async (code, label) => {
  api.apiErrorCode.mockReturnValue(code);
  api.uploadHistoryFile.mockRejectedValue(
    new Error("private source must not appear"),
  );
  mount();
  await userEvent.click(screen.getByRole("button", { name: "导入历史包" }));
  await userEvent.upload(
    screen.getByLabelText("历史数据文件"),
    new File(["[]"], "export.json"),
  );
  expect((await screen.findByRole("alert")).textContent).toBe(label);
  expect(screen.queryByText(/private source/)).toBeNull();
  expect(api.commitHistoryFile).not.toHaveBeenCalled();
});

it("hides the previous preview when replacement cleanup fails", async () => {
  api.discardHistoryFile.mockRejectedValue(new Error("local filesystem busy"));
  mount();
  await upload();
  await userEvent.upload(
    screen.getByLabelText("历史数据文件"),
    new File(["[]"], "second.json"),
  );
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.queryByText("6 条文本消息")).toBeNull();
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(api.uploadHistoryFile).toHaveBeenCalledTimes(1);
  expect(onConnect).not.toHaveBeenCalled();
});

it("previews pasted turns and invalidates consent immediately after editing", async () => {
  api.uploadHistoryFile.mockResolvedValue({
    ...staged,
    vendor: "generic",
    source_id: "generic-export",
    messages: 2,
    conversations: 1,
    human_messages: 1,
    agent_messages: 1,
  });
  mount();
  await userEvent.click(screen.getByRole("button", { name: "导入历史包" }));
  await userEvent.click(screen.getByRole("button", { name: "粘贴对话" }));
  await userEvent.type(
    screen.getByLabelText("对话原文"),
    "User: 原话不是结论\nAssistant: 这是一项建议",
  );
  await userEvent.click(screen.getByRole("button", { name: "预览内容" }));
  expect(await screen.findByText("手工提供的对话")).toBeTruthy();
  expect(screen.getByText("原话不是结论")).toBeTruthy();
  expect(screen.getByText("时间未知")).toBeTruthy();
  await userEvent.click(screen.getByRole("checkbox", { name: /确认保存/ }));
  await userEvent.click(screen.getByRole("button", { name: "编辑原文" }));
  await userEvent.type(screen.getByLabelText("对话原文"), "变更");
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(
    screen.getByRole("button", { name: "保存文件并核对来源" }),
  ).toHaveProperty("disabled", true);
  await userEvent.click(screen.getByRole("button", { name: "关闭历史包导入" }));
  expect(api.discardHistoryFile).toHaveBeenCalledWith(staged.upload_id);
});

it("discards an uncommitted memory preview when switching to historical conversations", async () => {
  api.uploadHistoryFile.mockResolvedValue({
    ...staged,
    vendor: "memory-content",
    source_id: "memory-content-export",
    content_kind: "memory_content",
  });
  mount();
  await userEvent.click(screen.getByRole("button", { name: "导入历史包" }));
  await userEvent.click(screen.getByRole("radio", { name: "已有记忆" }));
  await userEvent.upload(
    screen.getByLabelText("历史数据文件"),
    new File(["内容"], "memory.txt"),
  );
  await userEvent.click(screen.getByRole("checkbox", { name: /确认保存/ }));
  await userEvent.click(screen.getByRole("radio", { name: "历史对话" }));
  expect(api.discardHistoryFile).toHaveBeenCalledWith(staged.upload_id);
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(
    screen.getByRole("button", { name: "保存文件并核对来源" }),
  ).toHaveProperty("disabled", true);
  expect(onConnect).not.toHaveBeenCalled();
});

function ControllerHarness() {
  const imports = useImportSources({ automaticSync: false });
  return (
    <HistoryImport
      onConnect={imports.connectSelected}
      onRefresh={imports.refresh}
    />
  );
}

it("uses the real controller with a newly visible root and explicit failed-only retry", async () => {
  let published = false;
  api.getSources.mockImplementation(async () =>
    published
      ? [candidate]
      : [
          {
            ...candidate,
            available: false,
            candidate_root_id: null,
            location_state: "not_found",
          },
        ],
  );
  api.commitHistoryFile.mockImplementation(async () => {
    published = true;
    return committed;
  });
  const receipts = new Map<string, ConnectSourcesResponse>();
  api.getSourceConnection.mockImplementation(async (id: string) =>
    receipts.get(id),
  );
  api.connectSources.mockImplementation(async (_ids, _confirmed, options) => {
    const result = {
      ...accepted,
      request_id: options.requestId,
      ...(receipts.size
        ? {}
        : {
            accepted_source_ids: [],
            failed_sources: { [staged.source_id]: "source_unavailable" },
          }),
    };
    receipts.set(options.requestId, result);
    return result;
  });
  render(<ControllerHarness />);
  await upload();
  await publish();
  expect(api.connectSources).not.toHaveBeenCalled();
  await authorize();
  await screen.findByRole("alert");
  expect(api.connectSources).toHaveBeenCalledTimes(1);
  const first = api.connectSources.mock.calls[0][2];
  expect(first.sourceRoots).toEqual({
    [staged.source_id]: candidate.candidate_root_id,
  });
  await authorize("重试此来源");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(api.getSourceConnection).toHaveBeenCalledWith(first.requestId);
  expect(api.connectSources).toHaveBeenCalledTimes(2);
  expect(api.connectSources.mock.calls[1][2].requestId).not.toBe(
    first.requestId,
  );
  expect(api.commitHistoryFile).toHaveBeenCalledTimes(1);
  expect(api.setSourceSync).not.toHaveBeenCalled();
  expect(api.createImportJob).not.toHaveBeenCalled();
});

it("creates a separately bound intent for a second committed file in the same source directory", async () => {
  const second = {
    ...staged,
    upload_id: "00000000-0000-4000-8000-000000000002",
    checksum: "second-file-checksum",
  };
  api.uploadHistoryFile
    .mockResolvedValueOnce(staged)
    .mockResolvedValueOnce(second);
  api.commitHistoryFile.mockImplementation(async (id: string) => ({
    ...(id === staged.upload_id ? staged : second),
    committed: true,
  }));
  const receipts = new Map<string, ConnectSourcesResponse>();
  api.connectSources.mockImplementation(async (_ids, _confirmed, options) => {
    const result = { ...accepted, request_id: options.requestId };
    receipts.set(options.requestId, result);
    return result;
  });
  api.getSourceConnection.mockImplementation(async (id: string) =>
    receipts.get(id),
  );
  render(<ControllerHarness />);
  await upload();
  await publish();
  await authorize();
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  const first = api.connectSources.mock.calls[0][2];
  await upload();
  await publish();
  expect(api.connectSources).toHaveBeenCalledTimes(1);
  expect(
    screen.getByRole("checkbox", { name: /允许一次性读取/ }),
  ).toHaveProperty("checked", false);
  await authorize();
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(api.connectSources).toHaveBeenCalledTimes(2);
  const next = api.connectSources.mock.calls[1][2];
  expect(first.importUploadId).toBe(staged.upload_id);
  expect(next.importUploadId).toBe(second.upload_id);
  expect(next.requestId).not.toBe(first.requestId);
  expect(next.sourceRoots).toEqual(first.sourceRoots);
  expect(api.commitHistoryFile).toHaveBeenCalledTimes(2);
});

it("coalesces repeated closes during staged-file cleanup", async () => {
  const pending = deferred<void>();
  api.discardHistoryFile.mockReturnValue(pending.promise);
  mount();
  await upload();
  const dialog = screen.getByRole("dialog");
  act(() => {
    fireEvent.keyDown(dialog, { key: "Escape" });
    fireEvent.keyDown(dialog, { key: "Escape" });
  });
  expect(api.discardHistoryFile).toHaveBeenCalledTimes(1);
  await act(async () => pending.resolve());
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("never starts a replacement upload or second cleanup when closed during old-file discard", async () => {
  const pending = deferred<void>();
  api.discardHistoryFile.mockReturnValue(pending.promise);
  mount();
  await upload();
  await userEvent.upload(
    screen.getByLabelText("历史数据文件"),
    new File(["[]"], "replacement.json"),
  );
  await userEvent.click(screen.getByRole("button", { name: "关闭历史包导入" }));
  expect(api.discardHistoryFile).toHaveBeenCalledTimes(1);
  await act(async () => pending.resolve());
  expect(api.uploadHistoryFile).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("locks file and kind replacement until an uncertain save has been checked", async () => {
  api.commitHistoryFile.mockRejectedValueOnce(new Error("response lost"));
  mount();
  await upload();
  await publish();
  expect(screen.getByLabelText("历史数据文件")).toHaveProperty(
    "disabled",
    true,
  );
  expect(
    screen.getByRole("radio", { name: "已有记忆" }).matches(":disabled"),
  ).toBe(true);
  expect(screen.getByRole("button", { name: "粘贴对话" })).toHaveProperty(
    "disabled",
    true,
  );
  expect(
    screen.getByRole("button", { name: "核对文件保存结果" }),
  ).toHaveProperty("disabled", false);
  await userEvent.click(
    screen.getByRole("button", { name: "核对文件保存结果" }),
  );
  expect(screen.getByLabelText("历史数据文件")).toHaveProperty(
    "disabled",
    false,
  );
  expect(onConnect).not.toHaveBeenCalled();
});

it("does not downgrade a known committed receipt or discard it after a regressed read", async () => {
  onRefresh.mockRejectedValueOnce(new Error("offline"));
  api.getHistoryFile.mockResolvedValue(staged);
  mount();
  await upload();
  await publish();
  await userEvent.click(screen.getByRole("button", { name: "重新核对来源" }));
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(
    screen.queryByRole("button", { name: "保存文件并核对来源" }),
  ).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "关闭历史包导入" }));
  expect(api.discardHistoryFile).not.toHaveBeenCalled();
  expect(api.commitHistoryFile).toHaveBeenCalledTimes(1);
});

it("keeps a rejected cleanup retryable without restoring stale consent", async () => {
  api.discardHistoryFile.mockRejectedValueOnce(new Error("busy"));
  mount();
  await upload();
  await userEvent.click(screen.getByRole("checkbox", { name: /确认保存/ }));
  await userEvent.click(screen.getByRole("button", { name: "关闭历史包导入" }));
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.queryByRole("checkbox")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "关闭历史包导入" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(api.discardHistoryFile).toHaveBeenCalledTimes(2);
  expect(api.commitHistoryFile).not.toHaveBeenCalled();
});

it("wraps keyboard focus inside the dialog and Escape cancels staged content", async () => {
  mount();
  await upload();
  await userEvent.click(screen.getByRole("checkbox", { name: /确认保存/ }));
  const submit = screen.getByRole("button", { name: "保存文件并核对来源" });
  submit.focus();
  await userEvent.tab();
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: "关闭历史包导入" }),
  );
  await userEvent.tab({ shift: true });
  expect(document.activeElement).toBe(submit);
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(api.discardHistoryFile).toHaveBeenCalledTimes(1);
  expect(api.commitHistoryFile).not.toHaveBeenCalled();
});

it("keeps a successful file commit and source receipt when the controller's status refresh fails", async () => {
  let connected = false;
  api.getSources.mockImplementation(async () => {
    if (connected) throw new Error("status offline");
    return [candidate];
  });
  api.connectSources.mockImplementation(async (_ids, _confirmed, options) => {
    connected = true;
    return { ...accepted, request_id: options.requestId };
  });
  render(<ControllerHarness />);
  await upload();
  await publish();
  await authorize();
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.getByRole("status").textContent).toContain(
    "已取得来源连接回执",
  );
  expect(api.commitHistoryFile).toHaveBeenCalledTimes(1);
  expect(api.connectSources).toHaveBeenCalledTimes(1);
  expect(api.discardHistoryFile).not.toHaveBeenCalled();
});

it("retains a committed Claude ZIP through connection failure and retries its exact reviewed source", async () => {
  const preview: FileImportReceipt = {
    ...staged,
    upload_id: "00000000-0000-4000-8000-000000000003",
    vendor: "claude",
    source_id: "claude-export",
    filename: "export.zip",
    messages: 2,
    conversations: 1,
    human_messages: 1,
    agent_messages: 1,
    ignored_files: 0,
  };
  const source = {
    ...candidate,
    source_id: preview.source_id,
    display_name: "Claude 历史包",
    candidate_path: "/isolated/import-files/ready/claude-export",
    candidate_root_id: "root:v2:claude-directory",
  };
  api.uploadHistoryFile.mockResolvedValue(preview);
  api.commitHistoryFile.mockResolvedValue({ ...preview, committed: true });
  onRefresh.mockResolvedValue({ sources: [source] });
  onConnect.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({
    ...accepted,
    accepted_source_ids: [preview.source_id],
  });
  mount();
  await userEvent.click(screen.getByRole("button", { name: "导入历史包" }));
  await userEvent.upload(
    screen.getByLabelText("历史数据文件"),
    new File(["zip"], "export.zip"),
  );
  expect(await screen.findByText("2 条文本消息")).toBeTruthy();
  await publish();
  await authorize();
  expect(await screen.findByRole("alert")).toBeTruthy();
  await authorize("核对连接结果");
  expect(onConnect).toHaveBeenCalledTimes(2);
  expect(onConnect).toHaveBeenLastCalledWith([preview.source_id], [source], {
    importUploadId: preview.upload_id,
  });
  expect(api.commitHistoryFile).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("dialog")).toBeNull();
});
