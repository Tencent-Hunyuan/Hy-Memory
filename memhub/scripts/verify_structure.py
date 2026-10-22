#!/usr/bin/env python3

from pathlib import Path


REQUIRED_PATHS = (
    "NOTICE",
    "docs/ARCHITECTURE.md",
    "docs/DEVELOPMENT_LOOP.md",
    "docs/LOCALIZATION.md",
    "apps/core/pyproject.toml",
    "apps/cli/package.json",
    "apps/web/package.json",
    "apps/desktop/package.json",
    "packages/contracts/package.json",
    "packages/i18n/package.json",
    "scripts/audit_core_test_design.py",
    "scripts/generate_core_test_index.py",
    "scripts/summarize_core_test_report.py",
    "pnpm-workspace.yaml",
    "SECURITY.md",
    "PRIVACY.md",
    "CONTRIBUTING.md",
)

#: Paths that must stay gone, with the reason they were removed.
#:
#: A required-paths list cannot express "this was deleted on purpose", so a
#: deleted entry point reads as an oversight and comes back. `deploy/docker`
#: could not build for three independent reasons — pip does not read
#: `[tool.uv.sources]` so the private HY dependency never installed, the web
#: UI was never copied into the image, and the compose port bound loopback
#: only, so a remote deploy was unreachable even if it had built.
FORBIDDEN_PATHS = {
    "deploy/docker": "无法构建的部署入口，2026-09-09 RL-31 移除；桌面壳走 pyinstaller + extraResources",
    "AGENTS.md": "内部 Agent 协作说明不属于公开源码",
    "HANDSOFF.md": "内部交接和验收状态不属于公开源码",
    "docs/mvp/PREREQUISITES.md": "内部发布前置与审批跟踪不属于公开源码",
    "docs/mvp/SCOPE.md": "历史开源规划快照不属于公开源码",
    "docs/mvp/packaging-smoke-checklist.md": "内部实包验收记录不属于公开源码",
}

FORBIDDEN_ROOT_GLOBS = {
    "_tmp_*": "临时文件不得进入仓库根目录",
}


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    missing = [relative for relative in REQUIRED_PATHS if not (root / relative).exists()]
    if missing:
        for relative in missing:
            print(f"missing: {relative}")
        raise SystemExit(1)

    returned = [
        (relative, reason)
        for relative, reason in FORBIDDEN_PATHS.items()
        if (root / relative).exists()
    ]
    if returned:
        for relative, reason in returned:
            print(f"should not exist: {relative} — {reason}")
        raise SystemExit(1)

    matched_globs = [
        (path.relative_to(root), reason)
        for pattern, reason in FORBIDDEN_ROOT_GLOBS.items()
        for path in root.glob(pattern)
    ]
    if matched_globs:
        for relative, reason in matched_globs:
            print(f"should not exist: {relative} — {reason}")
        raise SystemExit(1)

    print(
        f"structure gate passed: {len(REQUIRED_PATHS)} required paths exist, "
        f"{len(FORBIDDEN_PATHS)} removed paths stayed removed, "
        f"{len(FORBIDDEN_ROOT_GLOBS)} forbidden root patterns stayed empty"
    )


if __name__ == "__main__":
    main()
