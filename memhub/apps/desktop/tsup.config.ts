import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { defineConfig } from "tsup";

const require = createRequire(import.meta.url);
const { resolveBuildNumber, resolveBuildVersion } =
  require("../../scripts/release-version.cjs") as {
    resolveBuildNumber(environment?: NodeJS.ProcessEnv, root?: string): number;
    resolveBuildVersion(environment?: NodeJS.ProcessEnv, root?: string): string;
  };
const releaseVersion = resolveBuildVersion(process.env);
const releaseBuildNumber = resolveBuildNumber(process.env);
const releaseCommit = resolveReleaseCommit(process.env.ONE_MEMORY_COMMIT);

// The runtime accepts only a full 40-character commit, so an explicitly
// supplied malformed value fails the build instead of being baked and skipped.
function resolveReleaseCommit(explicit: string | undefined): string {
  const trimmed = explicit?.trim();
  if (!trimmed) return readSourceCommit();
  if (!/^[0-9a-f]{40}$/i.test(trimmed)) {
    throw new Error(
      `ONE_MEMORY_COMMIT must be a full 40-character hex commit, got ${JSON.stringify(trimmed)}`,
    );
  }
  return trimmed.toLowerCase();
}

function readSourceCommit(): string {
  try {
    return execFileSync("git", ["rev-parse", "HEAD"], {
      cwd: fileURLToPath(new URL("../..", import.meta.url)),
      encoding: "utf8",
    }).trim();
  } catch {
    return "unknown";
  }
}

export default defineConfig({
  entry: ["src/main.ts", "src/preload.ts"],
  format: ["cjs"],
  platform: "node",
  outDir: "dist",
  external: ["electron"],
  noExternal: [
    "@one-memory/host-bridge",
    "@one-memory/contracts",
    "@one-memory/i18n",
    "@one-memory/runtime-control",
    "axios",
    "i18next",
    "https-proxy-agent",
    "proxy-from-env",
  ],
  define: {
    __ONE_MEMORY_VERSION__: JSON.stringify(releaseVersion),
    __ONE_MEMORY_TRIAL_PROFILE__: JSON.stringify(
      process.env.ONE_MEMORY_BUILD_TRIAL_PROFILE ?? null,
    ),
    __ONE_MEMORY_BUILD_NUMBER__: JSON.stringify(String(releaseBuildNumber)),
    __ONE_MEMORY_COMMIT__: JSON.stringify(releaseCommit),
  },
});
