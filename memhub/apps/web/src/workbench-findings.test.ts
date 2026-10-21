import type {
  GovernedMemory,
  MemoryProcessingSummary,
  SourceResponse,
} from "@one-memory/contracts";
import { describe, expect, it } from "vitest";

import { findingsOf, mattersOf, memoriesInMatter } from "./workbench-findings";

const NOW = new Date("2026-09-10T00:00:00Z");

it("localiza hallazgos conservando contenidos y nombres de ámbitos opacos", () => {
  const findings = findingsOf({
    now: NOW,
    processing: null,
    locale: "es",
    memories: [
      memory("a", { content: "原文甲", evidence_ids: ["shared"] }),
      memory("b", {
        content: "原文乙",
        evidence_ids: ["shared"],
        scopes: ["custom:范围原文"],
      }),
    ],
  });
  expect(findings[0].label).toBe("Proceden del mismo texto");
  expect(findings[0].text).toContain("原文甲");
  expect(findings[0].text).toContain("原文乙");
  expect(findings[0].text).toContain("范围原文");
  expect(findings[0].text).toContain("Perfil personal");
});

function memory(
  id: string,
  overrides: Partial<GovernedMemory> = {},
): GovernedMemory {
  return {
    memory_id: id,
    user_id: "local",
    kind: "chat",
    status: "confirmed",
    content: `记忆 ${id}`,
    scopes: ["all-apps"],
    evidence_ids: [`ev-${id}`],
    origin: "extracted",
    tags: [],
    revision: 1,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    deleted_at: null,
    ...overrides,
  };
}

function source(
  sourceId: string,
  overrides: Partial<SourceResponse> = {},
): SourceResponse {
  return {
    source_id: sourceId,
    display_name: sourceId,
    storage_kinds: ["jsonl"],
    available: true,
    authorized: true,
    detected: true,
    root_label: null,
    candidate_path: `~/.${sourceId}`,
    candidate_kind: "default",
    imported_messages: 10,
    imported_conversations: 2,
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

const processing: MemoryProcessingSummary = {
  pending: 0,
  running: 0,
  fresh_pending: 0,
  backlog_pending: 0,
  recent_completions: [],
  waiting_for_model: 0,
  completed: 0,
  failed: 0,
  blocked_stages: [],
  outside_window_pending: 0,
  extraction_window_preset: "3m",
  manual_extract_batches_planned: 0,
  manual_extract_batches_current: 0,
  active_queue: 0,
  in_flight_count: 0,
  seconds_per_completion: null,
} as MemoryProcessingSummary;

describe("mattersOf", () => {
  // 原来这里有三条测试守着「按 scope 分组」。那条轴已经被判定为错的：
  // scope 是授权范围，真实库里是 workspace:v1:<sha256>，当事项列出来就是
  // 一列 hash。删掉它们不是为了求绿，是它们守的行为本身不该存在。
  // 授权范围仍然要能被说清，只是说在重复发现的句子里，不再是左栏的轴 ——
  // 由下面这条和「重复」那组测试一起守。
  it("授权范围仍然说得出来，只是不再当事项轴", () => {
    const matters = mattersOf(
      [memory("a", { scopes: ["all-apps"], tags: [] })],
      [source("cursor")],
    );

    // 左栏不再因为 scope 立行。
    expect(matters.map((item) => item.label)).toEqual(["全部"]);
    // 但按 scope 取记忆仍然有效：存过的选择不能因为换轴就查不到。
    expect(
      memoriesInMatter(
        [memory("a", { scopes: ["all-apps"], tags: [] })],
        "all-apps",
      ),
    ).toHaveLength(1);
  });

  it("重复发现里也不许出现整串 hash", () => {
    // scopesText 会把每条记忆的授权范围念出来。真实库里那是 64 位十六进制，
    // 整串念出来占满一行且什么都没说。这里只要求短号可辨、长串不出现。
    const said = findingsOf({
      memories: [
        memory("a", {
          scopes: ["workspace:v1:0027abad1745330f1b57671635e0e9b3bd0cc3d2"],
          evidence_ids: ["ev-1"],
        }),
        memory("b", {
          scopes: ["workspace:v1:daf248ff48ea3e6baf35cc7f1fb46d74d5dc5360"],
          evidence_ids: ["ev-1"],
        }),
      ],
      processing: null,
      now: new Date("2026-09-10T00:00:00Z"),
    })
      .map((item) => item.text)
      .join(" ");

    expect(said).not.toContain("0027abad1745330f");
    // 但两个项目要能被分辨，否则这句话等于没说。
    expect(said).toContain("0027abad");
    expect(said).toContain("daf248ff");
  });

  it("一条记忆都没有时来源数是 0，不拿导入过的来源充数", () => {
    const matters = mattersOf([], [source("cursor")]);

    expect(matters[0]).toMatchObject({ memoryCount: 0, sourceCount: 0 });
  });
});

describe("findingsOf", () => {
  it("从同一句原话抽出来的两条会被指出来，并给出各自的归属", () => {
    const findings = findingsOf({
      memories: [
        memory("a", {
          content: "统一用 axios 封装，不要用 fetch 裸调",
          scopes: ["project:om"],
          evidence_ids: ["ev-1"],
        }),
        memory("b", {
          content: "统一用 axios 封装，不用 fetch 裸调",
          scopes: ["all-apps"],
          evidence_ids: ["ev-1", "ev-2"],
        }),
      ],
      processing,
      now: NOW,
    });

    const duplicate = findings.find((item) => item.kind === "duplicate");
    expect(duplicate?.memoryIds).toEqual(["a", "b"]);
    expect(duplicate?.text).toContain("om");
    expect(duplicate?.text).toContain("个人画像");
  });

  it("出处不同的两条不会被当成重复，哪怕措辞很像", () => {
    // 实测过：字面相似度分不开。「PersonaMem 卡在提取层」和「检索层先放着」
    // 的三元组相似度 0.346，比几对真重复还高。所以判据只看出处。
    const findings = findingsOf({
      memories: [
        memory("a", {
          content: "统一用 axios 封装，不要用 fetch 裸调",
          evidence_ids: ["ev-1"],
        }),
        memory("b", {
          content: "统一用 axios 封装，不用 fetch 裸调",
          evidence_ids: ["ev-2"],
        }),
      ],
      processing,
      now: NOW,
    });

    expect(findings.find((item) => item.kind === "duplicate")).toBeUndefined();
  });

  it("已删除和已被取代的记忆不参与重复判断", () => {
    const findings = findingsOf({
      memories: [
        memory("a", { content: "统一用 axios 封装，不要用 fetch 裸调" }),
        memory("b", {
          content: "统一用 axios 封装，不用 fetch 裸调",
          status: "deleted",
        }),
      ],
      processing,
      now: NOW,
    });

    expect(findings.find((item) => item.kind === "duplicate")).toBeUndefined();
  });

  it("已经删掉或被取代的记忆不参与配对", () => {
    // 正向对照：同样两条、同一出处，都活着的时候是能报出来的。
    const same = { evidence_ids: ["ev-1"] };
    const live = findingsOf({
      memories: [memory("a", same), memory("b", same)],
      processing,
      now: NOW,
    });
    expect(live.find((item) => item.kind === "duplicate")).toBeDefined();

    // 翻成负向：其中一条已经被处理掉，就不该再拿它去烦人。
    const gone = findingsOf({
      memories: [
        memory("a", same),
        memory("b", { ...same, status: "deleted" }),
      ],
      processing,
      now: NOW,
    });
    expect(gone.find((item) => item.kind === "duplicate")).toBeUndefined();

    const refused = findingsOf({
      memories: [
        memory("a", same),
        memory("b", { ...same, status: "superseded" }),
      ],
      processing,
      now: NOW,
    });
    expect(refused.find((item) => item.kind === "duplicate")).toBeUndefined();
  });

  it("重复对最多报两条，不淹掉别的发现", () => {
    const same = { evidence_ids: ["ev-1"] };
    const findings = findingsOf({
      memories: [
        memory("a", same),
        memory("b", same),
        memory("c", same),
        memory("d", same),
      ],
      processing,
      now: NOW,
    });

    expect(findings.filter((item) => item.kind === "duplicate")).toHaveLength(
      2,
    );
  });

  it("三条以上两个月没动过才报沉寂，两条不报", () => {
    const stale = { updated_at: "2026-06-01T00:00:00Z" };
    const two = findingsOf({
      memories: [
        memory("a", { ...stale, content: "甲" }),
        memory("b", { ...stale, content: "乙" }),
      ],
      processing,
      now: NOW,
    });
    expect(two.find((item) => item.kind === "dormant")).toBeUndefined();

    const three = findingsOf({
      memories: [
        memory("a", { ...stale, content: "甲" }),
        memory("b", { ...stale, content: "乙" }),
        memory("c", { ...stale, content: "丙" }),
      ],
      processing,
      now: NOW,
    });
    expect(three.find((item) => item.kind === "dormant")?.text).toContain(
      "3 条",
    );
  });

  it("流水线卡住时报出真实条数，而不是含糊说一句在处理", () => {
    const findings = findingsOf({
      memories: [],
      processing: {
        ...processing,
        blocked_stages: [
          { kind: "extract", reason: "model_profile_not_active", count: 7 },
        ],
      } as MemoryProcessingSummary,
      now: NOW,
    });

    expect(findings.find((item) => item.kind === "blocked")?.text).toContain(
      "7 段",
    );
  });

  it("什么都没有的时候不硬凑发现", () => {
    expect(findingsOf({ memories: [], processing, now: NOW })).toEqual([]);
  });
});

describe("左栏的事项轴", () => {
  it("列的是主题，不是那串授权范围的 hash", () => {
    // 真实库里 scope 长这样：workspace:v1:<sha256>。原来 mattersOf 按 scope
    // 分组、标签取冒号后面，于是「事项」列出的是 9 串 v1:0027abad… ——
    // 那是权限，不是事项，人也看不懂。
    const matters = mattersOf(
      [
        memory("a", {
          scopes: ["workspace:v1:0027abad1745330f1b57671635e0e9b3"],
          tags: ["axios", "frontend"],
        }),
        memory("b", {
          scopes: ["workspace:v1:daf248ff48ea3e6baf35cc7f1fb46d74"],
          tags: ["axios", "testing"],
        }),
      ],
      [source("cursor")],
    );

    const labels = matters.map((item) => item.label);
    expect(labels).toContain("全部");
    expect(labels).toContain("axios");
    // 一条 hash 都不许出现在人眼前。
    expect(labels.filter((label) => label.includes("v1:"))).toEqual([]);
    expect(labels.filter((label) => label.startsWith("workspace"))).toEqual([]);
  });

  it("只被一条记忆用过的主题不单独立一行", () => {
    // 正向对照：没有这条，上一条可以被「每个标签都立一行」骗过 ——
    // axios 照样在，只是旁边多出 frontend、testing 两个各挂一条的行，
    // 左栏又变成一条记忆一行，等于没分类。
    const matters = mattersOf(
      [
        memory("a", { scopes: [], tags: ["axios", "frontend"] }),
        memory("b", { scopes: [], tags: ["axios", "testing"] }),
      ],
      [],
    );

    const labels = matters.map((item) => item.label);
    expect(labels).toContain("axios");
    expect(labels).not.toContain("frontend");
    expect(labels).not.toContain("testing");
  });

  it("点一个主题能筛出带这个标签的记忆", () => {
    const memories = [
      memory("a", { scopes: [], tags: ["axios"] }),
      memory("b", { scopes: [], tags: ["testing"] }),
    ];

    const picked = memoriesInMatter(memories, "topic:axios");

    expect(picked.map((item) => item.memory_id)).toEqual(["a"]);
  });

  it("条数多的主题排前面，全部永远第一", () => {
    const matters = mattersOf(
      [
        memory("a", { scopes: [], tags: ["少", "多"] }),
        memory("b", { scopes: [], tags: ["多"] }),
        memory("c", { scopes: [], tags: ["多"] }),
        memory("d", { scopes: [], tags: ["少"] }),
      ],
      [],
    );

    expect(matters.map((item) => item.label)).toEqual(["全部", "多", "少"]);
  });

  it("一条带标签的记忆都没有时，左栏只剩全部，不是一片空", () => {
    const matters = mattersOf([memory("a", { scopes: [], tags: [] })], []);

    expect(matters.map((item) => item.label)).toEqual(["全部"]);
    expect(matters[0].memoryCount).toBe(1);
  });
});
