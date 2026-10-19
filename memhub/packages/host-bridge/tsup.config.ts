import { defineConfig } from "tsup";

export default defineConfig({
  entry: ["src/mcp.ts", "src/installer-cli.ts", "src/recall-cli.ts"],
  format: ["cjs"],
  platform: "node",
  outDir: "dist",
  // Preserve the separately built sync-hook asset directory.
  clean: false,
  // `@one-memory/contracts` is a private, source-only workspace package
  // (`exports` points at `src/index.ts`). Left external, the emitted CJS asks
  // Node for a `.js` file that only exists as `.ts`, and the MCP entry fails at
  // startup; inline it the way runtime-control already inlines it.
  noExternal: ["@one-memory/contracts", "axios"],
});
