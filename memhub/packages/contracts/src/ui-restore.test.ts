import { describe, expectTypeOf, it } from "vitest";

import type {
  BackupEstimate,
  BackupRestoreResult,
  BackupResult,
  UISessionExchange,
  UISessionLogout,
} from "./index.js";

describe("Core UI session and restore contracts", () => {
  it("pins the one-time session exchange shape", () => {
    expectTypeOf<UISessionExchange["schema_version"]>().toEqualTypeOf<1>();
    expectTypeOf<UISessionExchange["exchange_code"]>().toEqualTypeOf<string>();
    expectTypeOf<
      UISessionExchange["expires_in_seconds"]
    >().toEqualTypeOf<number>();
    expectTypeOf<UISessionExchange["url"]>().toEqualTypeOf<string>();
    expectTypeOf<UISessionLogout["state"]>().toEqualTypeOf<"logged_out">();
  });

  it("pins the managed restore result shape", () => {
    expectTypeOf<BackupRestoreResult["state"]>().toEqualTypeOf<"restored">();
    expectTypeOf<
      BackupRestoreResult["restored_bytes"]
    >().toEqualTypeOf<number>();
    expectTypeOf<
      BackupRestoreResult["previous_backup_path"]
    >().toEqualTypeOf<string>();
    expectTypeOf<
      BackupRestoreResult["source_schema_version"]
    >().toEqualTypeOf<number>();
    expectTypeOf<BackupRestoreResult["backup_schema_version"]>().toEqualTypeOf<
      1 | 2 | undefined
    >();
    expectTypeOf<BackupRestoreResult["memories_included"]>().toEqualTypeOf<
      boolean | undefined
    >();
  });

  it("pins a v2 backup: memories included, size first", () => {
    expectTypeOf<BackupResult["schema_version"]>().toEqualTypeOf<2>();
    expectTypeOf<BackupResult["memories_included"]>().toEqualTypeOf<true>();
    expectTypeOf<BackupResult["pause_ms"]>().toEqualTypeOf<number>();
    expectTypeOf<BackupEstimate["bytes"]>().toEqualTypeOf<number>();
    expectTypeOf<BackupEstimate["free_bytes"]>().toEqualTypeOf<number | null>();
  });
});
