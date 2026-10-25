import { expect, it } from "vitest";
import { requireCompletedStartupSdkMigration } from "./startup-sdk-migration";

it("refuses to open the app on a deferred migration, naming its code", () => {
  expect(() =>
    requireCompletedStartupSdkMigration({
      state: "deferred",
      code: "sdk_migration_disk_space_insufficient",
    }),
  ).toThrow("sdk_migration_disk_space_insufficient");
  expect(() =>
    requireCompletedStartupSdkMigration({ state: "deferred" }),
  ).toThrow("sdk_migration_incomplete");
  expect(() =>
    requireCompletedStartupSdkMigration({ state: "cutover" }),
  ).not.toThrow();
});
