import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const directory = path.dirname(fileURLToPath(import.meta.url));

describe("Linux runtime scripts", () => {
  it("requires and forwards the release build number", async () => {
    const script = await readFile(
      path.join(directory, "build-linux-runtime.mjs"),
      "utf8",
    );

    assert.match(script, /resolveBuildNumber/);
    assert.match(script, /resolveBuildVersion/);
    assert.match(script, /git", \["rev-parse", "HEAD"\]/);
    assert.match(script, /ONE_MEMORY_COMMIT: gitCommit/);
    assert.match(script, /version,\s*String\(buildNumber\)/);
    assert.match(script, /build_number: buildNumber/);
    assert.match(script, /git_commit: gitCommit/);
    assert.doesNotMatch(
      script,
      /path\.join\(output,\s*["']linux-build-receipt\.json["']\)/,
    );
    assert.match(script, /`\$\{output\}\.build-receipt\.json`/);
    assert.match(script, /ONE_MEMORY_CORE_LAYOUT/);
    assert.match(script, /pyinstaller/);
    assert.match(script, /run\("pnpm", \["run", "sbom"\]\)/);
    const workflow = await readFile(
      path.join(directory, "../../../../.github/workflows/linux-lifecycle.yml"),
      "utf8",
    );
    assert.doesNotMatch(
      workflow,
      /ONE_MEMORY_BUILD_NUMBER:.*github\.run_number/,
    );
    assert.doesNotMatch(workflow, /ONE_MEMORY_VERSION:\s*0\.0\.0/);
    assert.match(workflow, /runner:\s*ubuntu-24\.04-arm[\s\S]*arch:\s*arm64/);
    assert.match(
      workflow,
      /memhub-runtime-v\$\{VERSION\}-linux-\$\{ARCH\}\.tar\.gz/,
    );
    assert.match(workflow, /sha256sum "\$archive"/);
    assert.match(workflow, /actions\/upload-artifact@[a-f0-9]{40}/);
    assert.match(
      workflow,
      /name:\s*memhub-runtime-linux-\$\{\{ matrix\.arch \}\}/,
    );
  });

  it("executes the complete Linux lifecycle instead of reporting static blockers", async () => {
    const script = await readFile(
      path.join(directory, "verify-linux-lifecycle.mjs"),
      "utf8",
    );

    for (const command of [
      '"install"',
      '"start"',
      '"status"',
      '"restart"',
      '"doctor"',
      '"upgrade"',
      '"stop"',
      '"init"',
      '"scan"',
      '"uninstall"',
    ]) {
      assert.match(script, new RegExp(command));
    }
    assert.match(script, /instance_id_changed/);
    assert.match(script, /findKey\(installReceipt, "core_port"\)/);
    assert.match(script, /doctorCheck\(doctor, "core"\)/);
    assert.match(script, /doctorCheck\(doctor, "broker"\)/);
    assert.match(
      script,
      /coreCheck\.details\?\.version !== distributionManifest\.version/,
    );
    assert.match(
      script,
      /env\.ONE_MEMORY_CORE_PORT = String\(installedCorePort\)/,
    );
    assert.match(script, /waitForRunningStatus/);
    assert.match(
      script,
      /Exit 1 is a valid transient receipt[\s\S]{0,300}\[0, 1, 3\]/,
    );
    assert.match(script, /initial_queued/);
    assert.match(script, /append_queued/);
    assert.match(script, /FROM memory_write_jobs/);
    assert.match(script, /idempotent_upgrade_instance_id_changed/);
    assert.match(script, /post_upgrade_queued/);
    assert.match(script, /uninstall_preserved_data/);
    assert.match(script, /is-enabled/);
    assert.match(script, /is-active/);
    assert.match(script, /cleanup-uninstall/);
    assert.doesNotMatch(script, /install:\s*["']blocked["']/);
  });

  it("uploads successful tagged builds only to a draft release", async () => {
    const workflow = await readFile(
      path.join(directory, "../../../../.github/workflows/linux-lifecycle.yml"),
      "utf8",
    );

    assert.match(
      workflow,
      /publish-draft-release:[\s\S]*startsWith\(github\.ref, 'refs\/tags\/v'\)/,
    );
    assert.match(
      workflow,
      /publish-draft-release:[\s\S]*permissions:\s*\n\s*contents: write/,
    );
    assert.match(workflow, /actions\/download-artifact@[a-f0-9]{40}/);
    assert.match(
      workflow,
      /verify-release-version\.mjs[\s\S]*--channel stable/,
    );
    assert.match(workflow, /shopt -s globstar nullglob/);
    assert.match(workflow, /archives=\(release-assets\/\*\*\/"\$name"\)/);
    assert.match(
      workflow,
      /cp -- "\$\{archives\[0\]\}" "publish-assets\/\$name"/,
    );
    assert.match(workflow, /sha256sum -c/);
    assert.match(workflow, /--json isDraft[\s\S]*--jq \.isDraft/);
    assert.match(workflow, /gh release create[\s\S]*--draft/);
    assert.match(workflow, /gh release upload[\s\S]*--clobber/);
    assert.match(
      workflow,
      /publish-assets\/memhub-runtime-v\*-linux-\*\.tar\.gz/,
    );
  });
});
