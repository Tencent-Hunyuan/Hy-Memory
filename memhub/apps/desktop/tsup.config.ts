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
const releaseCommit =
  process.env.ONE_MEMORY_COMMIT?.trim() || readSourceCommit();

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
