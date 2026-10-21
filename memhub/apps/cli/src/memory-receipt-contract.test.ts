import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { CoreReader } from "./core-client.js";
import {
  planExistingMemoryMutation,
  planMemoryAdd,
  readMemoryMutationReceipt,
} from "./memory-flows.js";

// Written by a real Core and checked against it on the Core side, so these are
// the receipts the CLI meets in use rather than ones shaped to fit the parser.
const FIXTURE = JSON.parse(
  readFileSync(
    new URL(
      "../../../packages/contracts/fixtures/memory-mutation-receipts.v2.json",
      import.meta.url,
    ),
    "utf8",
  ),
) as { receipts: Record<"add" | "correct" | "delete", Receipt> };

interface Receipt {
  schema_version: number;
  request_id: string;
  memory_id: string;
  result_revision: number;
  result_status: string;
  operation: string;
}

function reader(data: unknown): CoreReader {
  return async <T>() => ({ state: "ok", data: data as T });
}

function detail(sample: Receipt) {
  const revision = sample.result_revision - 1;
  return {
    memory: {
      memory_id: sample.memory_id,
      user_id: "local",
      kind: "chat",
      status: "confirmed",
      content: "Synthetic memory.",
      scopes: ["all-apps"],
      evidence_ids: ["evidence-1"],
      origin: "manual",
      tags: [],
      revision,
      created_at: "2026-09-19T00:00:00Z",
      updated_at: "2026-09-19T00:00:01Z",
      deleted_at: null,
    },
    evidence: [
      {
        evidence_id: "evidence-1",
        available: true,
        source_id: "fixture",
        written_by: "person",
        created_at: "2026-09-19T00:00:00Z",
        verbatim: "Synthetic source text.",
      },
    ],
    history: [
      {
        revision,
        action: "create",
        status: "confirmed",
        content: "Synthetic memory.",
        scopes: ["all-apps"],
        created_at: "2026-09-19T00:00:01Z",
      },
    ],
    index_sync: null,
  };
}

describe("memory mutation receipt v2 from Core", () => {
  it("recovers an add from the receipt Core returns for a create", async () => {
    const sample = FIXTURE.receipts.add;
    expect(sample.schema_version).toBe(2);
    const preview = planMemoryAdd({
      content: "Synthetic memory.",
      memoryKind: "chat",
      scopes: ["all-apps"],
      evidenceIds: ["evidence-1"],
    });
    if (preview.state !== "preview") throw new Error("preview expected");
    // The request id is the one binding a receipt to its plan; the plan picks it.
    const result = await readMemoryMutationReceipt(
      reader({ ...sample, request_id: preview.plan.request_id }),
      preview.plan,
    );
    expect(result).toMatchObject({
      state: "recovered",
      receipt: {
        schema_version: 2,
        operation: "create",
        memory_id: sample.memory_id,
        result_revision: 1,
        result_status: "confirmed",
        memory_state: "present",
      },
    });
  });

  it.each<["correct" | "delete", { content?: string }]>([
    ["correct", { content: "Corrected synthetic memory." }],
    ["delete", {}],
  ])(
    "recovers %s from the receipt Core returns for it",
    async (operation, extra) => {
      const sample = FIXTURE.receipts[operation];
      const preview = await planExistingMemoryMutation(reader(detail(sample)), {
        operation,
        memoryId: sample.memory_id,
        ...extra,
      });
      if (preview.state !== "preview") {
        throw new Error(`preview expected: ${JSON.stringify(preview)}`);
      }
      const result = await readMemoryMutationReceipt(
        reader({ ...sample, request_id: preview.plan.request_id }),
        preview.plan,
      );
      expect(result).toMatchObject({
        state: "recovered",
        receipt: {
          schema_version: 2,
          operation: sample.operation,
          memory_id: sample.memory_id,
          result_revision: sample.result_revision,
          result_status: sample.result_status,
        },
      });
    },
  );

  it("does not take a v1 receipt for a v2 one", async () => {
    const sample = FIXTURE.receipts.delete;
    const preview = await planExistingMemoryMutation(reader(detail(sample)), {
      operation: "delete",
      memoryId: sample.memory_id,
    });
    if (preview.state !== "preview") throw new Error("preview expected");
    const { migrated_from: _dropped, ...v1 } = sample as Receipt & {
      migrated_from: null;
    };
    // As v1 was, and with v2's fields but v1's number: either alone is refused.
    for (const receipt of [
      { ...v1, schema_version: 1 },
      { ...sample, schema_version: 1 },
    ]) {
      const result = await readMemoryMutationReceipt(
        reader({ ...receipt, request_id: preview.plan.request_id }),
        preview.plan,
      );
      expect(result).toMatchObject({
        state: "unknown",
        code: "memory_mutation_receipt_invalid",
      });
    }
  });

  it("does not take a not-found receipt that names a migrated operation", async () => {
    const sample = FIXTURE.receipts.delete;
    const preview = await planExistingMemoryMutation(reader(detail(sample)), {
      operation: "delete",
      memoryId: sample.memory_id,
    });
    if (preview.state !== "preview") throw new Error("preview expected");
    const notFound = {
      schema_version: 2,
      request_id: preview.plan.request_id,
      state: "not_found",
      operation: null,
      memory_id: null,
      result_revision: null,
      result_status: null,
      memory_state: null,
    };
    expect(
      await readMemoryMutationReceipt(
        reader({ ...notFound, migrated_from: null }),
        preview.plan,
      ),
    ).toMatchObject({
      code: "memory_mutation_receipt_not_found_after_attempt",
    });
    expect(
      await readMemoryMutationReceipt(
        reader({ ...notFound, migrated_from: "reject" }),
        preview.plan,
      ),
    ).toMatchObject({ code: "memory_mutation_receipt_invalid" });
  });

  it("plans nothing on a memory that is not in effect", async () => {
    const sample = FIXTURE.receipts.delete;
    const superseded = detail(sample);
    superseded.memory.status = "superseded";
    superseded.history[0].status = "superseded";
    const result = await planExistingMemoryMutation(reader(superseded), {
      operation: "delete",
      memoryId: sample.memory_id,
    });
    expect(result).toMatchObject({
      state: "invalid",
      code: "memory_operation_not_allowed",
    });
  });
});
