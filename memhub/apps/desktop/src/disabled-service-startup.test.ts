import { readFile } from "node:fs/promises";
import path from "node:path";
import { expect, it } from "vitest";

/** The degraded startup branch lives in Electron's startup path, which this
 * repo covers by asserting the source shape (see `noncanonical-startup.test.ts`).
 *
 * Without it, deleting the branch would leave every test green while a durable
 * `launchctl disable` override quietly made the app unopenable again.
 */
it("degrades to a child Runtime instead of failing startup when the service is disabled", async () => {
  const source = await readFile(path.join(__dirname, "main.ts"), "utf8");
  const classify = source.indexOf(
    "if (!isDisabledServiceInstallError(error)) throw error;",
  );
  expect(classify).toBeGreaterThan(-1);
  const degrade = source.indexOf("await ensureRuntimeProcess(", classify);
  expect(degrade).toBeGreaterThan(classify);
  expect(source).toContain('event: "stable_runtime.service_disabled"');
  // Reported, never undone: the override is the user's or the system's
  // decision, and refusing to re-enable it is the installer's contract.
  expect(source).not.toMatch(/launchctl\W+enable/);
});
