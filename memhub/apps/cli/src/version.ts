declare const __ONE_MEMORY_VERSION__: string;
declare const __ONE_MEMORY_BUILD_NUMBER__: string;
declare const __MEMHUB_DEFAULT_RUNTIME_VERSION__: string;

const SEMVER =
  /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/;

const embeddedVersion =
  typeof __ONE_MEMORY_VERSION__ === "string"
    ? __ONE_MEMORY_VERSION__.trim()
    : "";
const embeddedBuildNumber =
  typeof __ONE_MEMORY_BUILD_NUMBER__ === "string"
    ? __ONE_MEMORY_BUILD_NUMBER__.trim()
    : "";
const embeddedRuntimeVersion =
  typeof __MEMHUB_DEFAULT_RUNTIME_VERSION__ === "string"
    ? __MEMHUB_DEFAULT_RUNTIME_VERSION__.trim()
    : "";

export const CLI_VERSION = SEMVER.test(embeddedVersion)
  ? embeddedVersion
  : "development";

export const RUNTIME_RELEASE_VERSION = SEMVER.test(embeddedRuntimeVersion)
  ? embeddedRuntimeVersion
  : CLI_VERSION;

export const CLI_BUILD_NUMBER = /^\d+$/.test(embeddedBuildNumber)
  ? Number(embeddedBuildNumber)
  : null;

export const PRODUCT = "memhub";
