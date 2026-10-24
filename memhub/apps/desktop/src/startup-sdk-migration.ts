import {
  runSdkMigration,
  type SdkMigrationResult,
} from "@one-memory/runtime-control";

export type StartupMigrationResult = SdkMigrationResult;
export const runStartupSdkMigration = runSdkMigration;

export function requireCompletedStartupSdkMigration(
  result: StartupMigrationResult,
): void {
  if (result.state === "deferred") {
    throw new Error(result.code ?? "sdk_migration_incomplete");
  }
}
