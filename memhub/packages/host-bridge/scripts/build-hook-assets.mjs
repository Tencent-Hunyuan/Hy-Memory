import { createHash } from "node:crypto";
import {
  mkdir,
  readdir,
  readFile,
  rename,
  rm,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "tsup";

const root = fileURLToPath(new URL("../", import.meta.url));
const args = process.argv.slice(2);
if (
  args.length !== 0 &&
  (args.length !== 2 || args[0] !== "--outdir" || !path.isAbsolute(args[1]))
) {
  throw new Error(
    "Usage: build-hook-assets.mjs [--outdir /absolute/directory]",
  );
}
const outDir = args[1] ?? path.join(root, "dist/sync-hook");
await mkdir(outDir, { recursive: true });
const allowed = ["manifest.json", "notification.mjs", "runtime.mjs"];
if ((await readdir(outDir)).some((name) => !allowed.includes(name))) {
  throw new Error(
    "Hook asset directory contains unexpected files; no cleanup attempted",
  );
}
// No old manifest may attest a partial build. Only the dedicated asset directory is touched.
await rm(path.join(outDir, "manifest.json"), { force: true });
await build({
  entry: {
    runtime: path.join(root, "src/memmy-adapter/claude-code/runtime.ts"),
    notification: path.join(root, "src/notification-transport.ts"),
  },
  outDir,
  config: false,
  bundle: true,
  splitting: false,
  clean: false,
  format: ["esm"],
  platform: "node",
  target: "node24",
  noExternal: ["@one-memory/contracts", "axios"],
  banner: {
    js: 'import { createRequire as __hookCreateRequire } from "node:module"; const require = __hookCreateRequire(import.meta.url);',
  },
  outExtension: () => ({ js: ".mjs" }),
  shims: true,
  sourcemap: false,
  dts: false,
  silent: true,
});
const emitted = (await readdir(outDir)).sort();
if (
  JSON.stringify(emitted) !==
  JSON.stringify(["notification.mjs", "runtime.mjs"])
) {
  throw new Error("Expected only two self-contained Hook assets");
}
const files = {};
for (const name of ["runtime.mjs", "notification.mjs"]) {
  const bytes = await readFile(path.join(outDir, name));
  files[name] = {
    sha256: createHash("sha256").update(bytes).digest("hex"),
    bytes: bytes.byteLength,
  };
}
const manifest = {
  schemaVersion: 1,
  runtimeCapability: "mh-c01-runtime-v1",
  notificationCapability: "source-change-v1",
  runtimeTarget: "node24",
  files,
};
const temporary = path.join(outDir, "manifest.json.tmp");
await writeFile(temporary, `${JSON.stringify(manifest, null, 2)}\n`, {
  flag: "wx",
});
await rename(temporary, path.join(outDir, "manifest.json"));
console.log(JSON.stringify({ outDir, ...manifest }));
