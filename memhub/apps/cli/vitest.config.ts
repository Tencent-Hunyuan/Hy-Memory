import { readFileSync } from "node:fs";
import { defineConfig } from "vitest/config";

const version = JSON.parse(
  readFileSync(new URL("package.json", import.meta.url), "utf8"),
).version as string;

export default defineConfig({
  define: {
    __ONE_MEMORY_VERSION__: JSON.stringify(version),
    __ONE_MEMORY_BUILD_NUMBER__: JSON.stringify("unknown"),
    __ONE_MEMORY_COMMIT__: JSON.stringify("unknown"),
    __MEMHUB_DEFAULT_RELEASE_MANIFEST_URL_TEMPLATE__: JSON.stringify(""),
    __MEMHUB_DEFAULT_RUNTIME_VERSION__: JSON.stringify(version),
  },
  test: {
    testTimeout: 120_000,
    hookTimeout: 30_000,
    setupFiles: ["./src/daily-port-guard.test-setup.ts"],
  },
});
