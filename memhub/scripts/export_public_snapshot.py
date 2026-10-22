#!/usr/bin/env python3
"""Export the reviewed public source tree without internal process artifacts."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


ROOT_FILES = frozenset(
    {
        ".gitattributes",
        ".gitignore",
        ".node-version",
        ".npmrc",
        ".python-version",
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "MEMORY.md",
        "NOTICE",
        "PRIVACY.md",
        "README.md",
        "SBOM.spdx.json",
        "SECURITY.md",
        "THIRD_PARTY_NOTICES.md",
        "biome.json",
        "package.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
    }
)

PUBLIC_DOCS = frozenset(
    {
        "docs/ARCHITECTURE.md",
        "docs/DEVELOPMENT_LOOP.md",
        "docs/INSTALLATION.md",
        "docs/LOCALIZATION.md",
        "docs/cli/EXPERIENCE.md",
        "docs/contracts/diagnostic-logging.md",
        "docs/contracts/host-context-transport-v1.md",
        "docs/contracts/one-memory-recall-install-v1.md",
        "docs/contracts/runtime-unification.md",
        "docs/contracts/source-adapter-v1.md",
        "docs/contracts/unified-context-read-v1.md",
    }
)

PUBLIC_SCRIPTS = frozenset(
    {
        "scripts/assert-test-isolation.mjs",
        "scripts/audit_core_test_design.py",
        "scripts/build-release-assets.mjs",
        "scripts/build-release-assets.test.mjs",
        "scripts/build_app_icon.cjs",
        "scripts/build_runtime_distribution.cjs",
        "scripts/build_runtime_distribution.test.cjs",
        "scripts/build_runtime_native.cjs",
        "scripts/build_service_observer.cjs",
        "scripts/build_service_observer.test.cjs",
        "scripts/check.sh",
        "scripts/export_public_snapshot.py",
        "scripts/fetch_embedding_model.py",
        "scripts/generate_core_test_index.py",
        "scripts/generate_sbom.py",
        "scripts/install-docs.test.mjs",
        "scripts/platform/macos/build-macos-release.mjs",
        "scripts/platform/macos/macos-release-scripts.test.mjs",
        "scripts/platform/macos/notarize-macos-release.mjs",
        "scripts/packaged_resource_manifest.cjs",
        "scripts/packaged_resource_manifest.test.cjs",
        "scripts/probe_hy_dependency_surface.py",
        "scripts/publish-cli-after-release.mjs",
        "scripts/publish-cli-after-release.test.mjs",
        "scripts/release-contract.cjs",
        "scripts/release-contract.test.cjs",
        "scripts/release-version.cjs",
        "scripts/release-workflow.test.mjs",
        "scripts/run-desktop-package-mac-dir.sh",
        "scripts/run-tests-isolated.sh",
        "scripts/run-tests-isolated.test.mjs",
        "scripts/test_export_public_snapshot.py",
        "scripts/test_generate_sbom.py",
        "scripts/test_verify_public_release_ready.py",
        "scripts/test_verify_public_snapshot.py",
        "scripts/test_zero_collection_service_status.py",
        "scripts/verify_login_autorun.cjs",
        "scripts/verify_login_autorun.test.cjs",
        "scripts/verify_release_ready.py",
        "scripts/verify_public_release_ready.py",
        "scripts/verify_public_snapshot.py",
        "scripts/verify-published-release.mjs",
        "scripts/verify-published-release.test.mjs",
        "scripts/set-release-version.mjs",
        "scripts/set-release-version.test.mjs",
        "scripts/summarize_core_test_report.py",
        "scripts/verify-release-version.mjs",
        "scripts/verify-release-version.test.mjs",
        "scripts/verify_structure.py",
        "scripts/verify_zero_collection.py",
    }
)

PUBLIC_PREFIXES = (
    "apps/",
    "docs/harness-support/",
    "packages/",
    "scripts/platform/linux/",
    "scripts/platform/macos/service-lifecycle",
    "scripts/platform/macos/custom-root-port-isolation.test.mjs",
    "scripts/platform/windows/verify-windows-support",
)

EXCLUDED_PREFIXES = (
    "apps/cli/docs/",
)

# These files are useful only with private acceptance receipts, design exports,
# real-machine paths, or packaging worktrees that the public snapshot excludes.
EXCLUDED_FILES = frozenset(
    {
        "apps/cli/src/bundle-receipt.test.ts",
        "apps/core/tests/test_bulk_import_experience.py",
        "apps/core/tests/test_concurrent_import_processing.py",
        "apps/core/tests/test_context_auto_recall.py",
        "apps/core/tests/test_context_expand.py",
        "apps/core/tests/test_context_feedback.py",
        "apps/core/tests/test_context_host_artifact.py",
        "apps/core/tests/test_context_memory_scale_benchmark.py",
        "apps/core/tests/test_context_mutations.py",
        "apps/core/tests/test_context_query_planner.py",
        "apps/core/tests/test_context_resume.py",
        "apps/core/tests/test_context_v2_coordinator.py",
        "apps/core/tests/test_product_contract_coverage.py",
        "apps/core/tests/test_real_model_prereq_guards.py",
        "apps/core/tests/test_retrieval_models.py",
        "apps/core/tests/test_training_extraction_and_baseline.py",
        "apps/desktop/scripts/capture-onboarding.cjs",
        "apps/desktop/scripts/capture-review.cjs",
        "packages/host-bridge/scripts/c5-containment-replay.mjs",
    }
)

FORBIDDEN_TEXT = (
    "git" + "." + "woa" + ".com",
    "." + "woa" + ".com",
    "." + "oa" + ".com",
    "/Users/" + "kristinhe",
    "产品" + "OM",
    "file" + "726913703804825",
    "翔" + "哥",
    "codex/" + "c6-cli-h01",
)

TEXT_SUFFIXES = frozenset(
    {
        ".cjs",
        ".css",
        ".html",
        ".js",
        ".json",
        ".md",
        ".mjs",
        ".py",
        ".sh",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }
)


def _repository_files(root: Path) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if result.returncode == 0:
        return tuple(
            value.decode("utf-8") for value in result.stdout.split(b"\0") if value
        )
    return tuple(
        path.relative_to(root).as_posix()
        for path in sorted(root.rglob("*"))
        if (path.is_file() or path.is_symlink()) and ".git" not in path.relative_to(root).parts
    )


def _is_public(relative: str) -> bool:
    if relative in EXCLUDED_FILES:
        return False
    if relative.startswith(EXCLUDED_PREFIXES):
        return False
    if "/node_modules/" in f"/{relative}/" or "/.venv/" in f"/{relative}/":
        return False
    if "/__pycache__/" in f"/{relative}/":
        return False
    if "/dist/" in f"/{relative}/" or "/build/" in f"/{relative}/":
        return False
    if relative in ROOT_FILES or relative in PUBLIC_DOCS or relative in PUBLIC_SCRIPTS:
        return True
    return relative.startswith(PUBLIC_PREFIXES)


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        target.symlink_to(source.readlink())
    else:
        shutil.copy2(source, target)


def _scan_text(target: Path) -> list[str]:
    failures: list[str] = []
    for path in sorted(target.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for marker in FORBIDDEN_TEXT:
            if marker in content:
                failures.append(f"forbidden public marker {marker!r}: {path.relative_to(target)}")
    return failures


def export(root: Path, target: Path) -> tuple[str, ...]:
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"target must be empty: {target}")
    target.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for relative in _repository_files(root):
        source = root / relative
        if not source.exists() or not _is_public(relative):
            continue
        _copy_file(source, target / relative)
        copied.append(relative)
    missing = sorted(ROOT_FILES - set(copied))
    if missing:
        raise ValueError(f"public root files missing from export: {', '.join(missing)}")
    unexpected_root = sorted(
        path.name
        for path in target.iterdir()
        if path.is_file() and path.name not in ROOT_FILES
    )
    if unexpected_root:
        raise ValueError(f"unclassified public root files: {', '.join(unexpected_root)}")
    failures = _scan_text(target)
    if failures:
        raise ValueError("\n".join(failures))
    return tuple(copied)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    copied = export(root, args.target.resolve())
    print(f"public snapshot exported: {len(copied)} files -> {args.target.resolve()}")


if __name__ == "__main__":
    main()
