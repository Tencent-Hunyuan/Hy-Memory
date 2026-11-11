import path from "node:path";

const RESERVED_PORTS = new Set([8765, 18765]);
const PORT_KEYS = [
  "ONE_MEMORY_PORT",
  "ONE_MEMORY_CORE_PORT",
  "ONE_MEMORY_RUNTIME_PORT",
  "ONE_MEMORY_BROKER_PORT",
];

function fail(message) {
  process.stderr.write(`test isolation refused: ${message}\n`);
  process.exit(78);
}

if (process.env.ONE_MEMORY_TEST_ISOLATED !== "1") {
  fail("run tests through scripts/run-tests-isolated.sh");
}

const realHome = process.env.ONE_MEMORY_TEST_REAL_HOME;
const home = process.env.HOME;
const dataDir = process.env.ONE_MEMORY_DATA_DIR;
if (!realHome || !home || !dataDir)
  fail("required isolation paths are missing");
if (path.resolve(home) === path.resolve(realHome))
  fail("HOME is the real user directory");
if (path.resolve(dataDir).startsWith(`${path.resolve(realHome)}${path.sep}`)) {
  fail("ONE_MEMORY_DATA_DIR is inside the real user directory");
}

if (process.platform === "win32") {
  const userProfile = process.env.USERPROFILE;
  if (!userProfile) fail("USERPROFILE is unset");
  const real = path.resolve(realHome);
  const profile = path.resolve(userProfile);
  if (profile === real) fail("USERPROFILE is the real user directory");
  if (profile.startsWith(`${real}${path.sep}`)) {
    fail("USERPROFILE is inside the real user directory");
  }
}

for (const key of PORT_KEYS) {
  const value = Number(process.env[key]);
  if (!Number.isSafeInteger(value) || value < 1024 || value > 65535) {
    fail(`${key} must be an explicit non-privileged test port`);
  }
  if (RESERVED_PORTS.has(value)) fail(`${key} uses a reserved daily port`);
}
