import { createRequire } from "node:module";
import { defineConfig } from "tsup";

const require = createRequire(import.meta.url);
const { assertSemver, resolveBuildVersion } =
  require("../../scripts/release-version.cjs") as {
    assertSemver(value: string, label?: string): string;
    resolveBuildVersion(environment?: NodeJS.ProcessEnv, root?: string): string;
  };
const releaseVersion = resolveBuildVersion(process.env);
const packageVersion = (require("./package.json") as { version: string })
  .version;
const cliVersion = assertSemver(
  process.env.MEMHUB_NPM_VERSION?.trim() || packageVersion,
  "MEMHUB_NPM_VERSION",
);
const runtimeReleaseVersion = assertSemver(
  process.env.MEMHUB_DEFAULT_RUNTIME_VERSION?.trim() || releaseVersion,
  "MEMHUB_DEFAULT_RUNTIME_VERSION",
);

export default defineConfig({
  entry: { "one-memory": "src/entry.ts" },
  format: ["cjs"],
  platform: "node",
  outDir: "dist",
  clean: true,
  define: {
    __ONE_MEMORY_VERSION__: JSON.stringify(cliVersion),
    __ONE_MEMORY_BUILD_NUMBER__: JSON.stringify(
      process.env.ONE_MEMORY_BUILD_NUMBER ?? "unknown",
    ),
    __ONE_MEMORY_COMMIT__: JSON.stringify(
      process.env.ONE_MEMORY_COMMIT ?? "unknown",
    ),
    __MEMHUB_DEFAULT_RELEASE_MANIFEST_URL_TEMPLATE__: JSON.stringify(
      process.env.MEMHUB_DEFAULT_RELEASE_MANIFEST_URL_TEMPLATE ?? "",
    ),
    __MEMHUB_DEFAULT_RUNTIME_VERSION__: JSON.stringify(runtimeReleaseVersion),
  },
  // The shared operations package ships TypeScript sources, so it must be
  // inlined into the CJS bundle the same way runtime-control is; otherwise the
  // emitted bundle tries to `require` a `.js` file that only exists as `.ts`.
  noExternal: [
    "@one-memory/operations",
    "@one-memory/runtime-control",
    "axios",
    "https-proxy-agent",
    "proxy-from-env",
  ],
});
