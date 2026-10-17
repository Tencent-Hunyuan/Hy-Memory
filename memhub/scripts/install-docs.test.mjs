import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("installation docs disclose publication and lifecycle boundaries", async () => {
  const readme = await readFile(
    new URL("../README.md", import.meta.url),
    "utf8",
  );
  const installation = await readFile(
    new URL("../docs/INSTALLATION.md", import.meta.url),
    "utf8",
  );

  assert.match(readme, /公共 Python SDK 为 `hy-memory==2\.0\.3`/);
  assert.match(readme, /`v1\.0\.3` 的发行目标是 Linux x64\/arm64 Runtime/);
  assert.match(readme, /Linux 制品由 GitHub Actions/);
  assert.match(readme, /macOS.*Developer ID 签名、公证/s);
  assert.match(readme, /release-manifest\.json/);
  assert.match(readme, /memhub uninstall/);
  assert.match(readme, /memhub delete-data/);
  assert.match(readme, /docs\/INSTALLATION\.md/);
  assert.doesNotMatch(readme, /私有 RC|内部候选/);

  assert.match(installation, /Linux x64.*GitHub Actions/s);
  assert.match(installation, /Linux arm64.*GitHub Actions/s);
  assert.match(installation, /macOS arm64.*Developer ID/s);
  assert.match(installation, /macOS x64.*Developer ID/s);
  assert.match(installation, /release-manifest\.json/);
  assert.match(installation, /fake Release/);
  assert.match(installation, /--source/);
  assert.match(installation, /完全离线/);
  assert.match(installation, /HTTPS_PROXY/);
  assert.match(installation, /NO_PROXY/);
  assert.match(installation, /npm `postinstall` 不下载 Runtime/);
  assert.match(installation, /Python Core.*系统 Python/s);
  assert.match(installation, /默认数据目录是 `~\/\.one-memory`/);
  assert.match(installation, /Windows.*不支持/s);
  assert.match(installation, /最低支持 macOS 13/);
  assert.match(installation, /手动下载新版本/);
  assert.match(installation, /auto-updater/);
  assert.doesNotMatch(installation, /私有 RC|内部候选/);
  assert.doesNotMatch(installation, /npm install -g @tencent\/memhub_internal/);
});
