import { afterEach, expect, it, vi } from "vitest";
import { queryClient, resetClient } from "./query-client";
import {
  commitSedimentation,
  refreshSedimentation,
} from "./sedimentation-state";

const { getSedimentation } = vi.hoisted(() => ({
  getSedimentation: vi.fn(),
}));

vi.mock("./api", () => ({ getSedimentation }));

const enabled = {
  enabled: true,
  pending_retained: true,
  imports_unaffected: true,
};
const paused = { ...enabled, enabled: false };

afterEach(() => {
  resetClient();
  vi.clearAllMocks();
});

it("keeps a newer switch receipt when an older read lands later", async () => {
  let finishRead: ((value: typeof enabled) => void) | undefined;
  getSedimentation.mockImplementationOnce(
    () =>
      new Promise<typeof enabled>((resolve) => {
        finishRead = resolve;
      }),
  );

  const olderRead = refreshSedimentation();
  commitSedimentation(paused);
  finishRead?.(enabled);

  expect(await olderRead).toEqual(paused);
  expect(queryClient.getQueryData(["sedimentation"])).toEqual(paused);
});

it("keeps the newest read when concurrent reads finish out of order", async () => {
  let finishFirst: ((value: typeof enabled) => void) | undefined;
  getSedimentation
    .mockImplementationOnce(
      () =>
        new Promise<typeof enabled>((resolve) => {
          finishFirst = resolve;
        }),
    )
    .mockResolvedValueOnce(paused);

  const first = refreshSedimentation();
  const second = refreshSedimentation();
  expect(await second).toEqual(paused);

  finishFirst?.(enabled);
  expect(await first).toEqual(paused);
  expect(queryClient.getQueryData(["sedimentation"])).toEqual(paused);
});
