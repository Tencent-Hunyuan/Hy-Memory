import { stopRuntimeProcess } from "./runtime-launcher.js";
import { readSdkMigrationBlock } from "./sdk-migration-log.js";
import {
  disableRuntimeService,
  enableRuntimeService,
  runtimeServiceEnabled,
} from "./service-ownership.js";

export interface RuntimeMaintenanceDependencies {
  serviceEnabled(dataDir: string): Promise<boolean>;
  disableService(dataDir: string): Promise<void>;
  enableService(dataDir: string): Promise<void>;
  stopRuntime(options: { dataDir: string }): Promise<"stopped" | "not-running">;
  /** True when the maintenance left the home frozen after a failed migration. */
  homeFrozen?(dataDir: string): Promise<boolean>;
}

async function homeFrozen(dataDir: string): Promise<boolean> {
  return (await readSdkMigrationBlock(dataDir))?.frozen !== undefined;
}

const DEFAULT_DEPENDENCIES: RuntimeMaintenanceDependencies = {
  serviceEnabled: runtimeServiceEnabled,
  disableService: disableRuntimeService,
  enableService: enableRuntimeService,
  stopRuntime: stopRuntimeProcess,
  homeFrozen,
};

/**
 * Run offline maintenance while preventing a managed service from restarting
 * Runtime between stop and completion. The prior enable-marker state is
 * restored even when maintenance fails, unless the maintenance froze the home
 * (a failed SDK migration): then the service stays off until the next
 * migration succeeds, which turns it back on itself.
 */
export async function withRuntimeMaintenanceFence<T>(
  dataDir: string,
  maintain: () => Promise<T>,
  dependencies: RuntimeMaintenanceDependencies = DEFAULT_DEPENDENCIES,
): Promise<T> {
  const wasEnabled = await dependencies.serviceEnabled(dataDir);
  if (wasEnabled) await dependencies.disableService(dataDir);
  try {
    await dependencies.stopRuntime({ dataDir });
    return await maintain();
  } finally {
    if (
      wasEnabled &&
      !(await (dependencies.homeFrozen ?? homeFrozen)(dataDir).catch(
        () => false,
      ))
    )
      await dependencies.enableService(dataDir);
  }
}
