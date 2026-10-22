import { copyFile, lstat, mkdir, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const sourceRoot = path.resolve(
  packageRoot,
  "../../..",
  "plugins/hermes/hy_memory",
);
const targetRoot = path.join(packageRoot, "dist/hermes-provider/hy_memory");
const files = ["__init__.py", "plugin.yaml", "provider.py"];

await rm(path.dirname(targetRoot), { recursive: true, force: true });
await mkdir(targetRoot, { recursive: true, mode: 0o700 });
for (const relative of files) {
  const source = path.join(sourceRoot, relative);
  const info = await lstat(source);
  if (!info.isFile() || info.isSymbolicLink()) {
    throw new Error(`invalid Hermes provider asset: ${relative}`);
  }
  await copyFile(source, path.join(targetRoot, relative));
}
