#!/usr/bin/env python3
"""Generate SPDX SBOM and third-party notices from the installed dependencies.

Both artifacts are derived from what is actually installed rather than from a
hand-maintained list, so they cannot quietly drift away from the shipped app.
Run this after changing dependencies; `pnpm release:check` refuses to release
without the outputs.
"""

import sys

if sys.version_info < (3, 10):
    raise SystemExit(
        "generate_sbom.py requires Python 3.10+; run it with "
        "apps/core/.venv/bin/python"
    )

import json
import os
import re
import subprocess
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEMMY_LICENSE = (
    ROOT
    / "packages"
    / "host-bridge"
    / "src"
    / "memmy-adapter"
    / "claude-code"
    / "LICENSE.memmy"
)
MEMMY_PROVENANCE = MEMMY_LICENSE.with_name("PROVENANCE.md")
MEMMY_PIN = "25acd5f29fc7df73b943713abe61f1b2f9839887"
HY_MEMORY_NAME = "hy-memory"
HY_MEMORY_VERSION = "2.0.5"


class SbomError(RuntimeError):
    pass


def _python_dist_info(name: str, version: str) -> Path:
    environment = ROOT / "apps" / "core" / ".venv"
    matches = sorted(environment.glob(f"lib/*/site-packages/{name.replace('-', '_')}-{version}.dist-info"))
    if len(matches) != 1:
        raise SbomError(
            f"{name} {version} dist-info is missing or ambiguous; run uv sync before generating the SBOM"
        )
    return matches[0]


def hy_memory_notice(dist_info: Path | None = None) -> dict[str, str]:
    """Read the installed HY wheel notice instead of guessing its license."""
    directory = dist_info or _python_dist_info(HY_MEMORY_NAME, HY_MEMORY_VERSION)
    metadata_path = directory / "METADATA"
    license_path = directory / "licenses" / "LICENSE"
    if not directory.is_dir() or not metadata_path.is_file() or not license_path.is_file():
        raise SbomError(
            "hy-memory dist-info, METADATA or licenses/LICENSE is missing"
        )
    metadata = metadata_path.read_text(encoding="utf-8")
    name = re.search(r"^Name:\s*(.+)$", metadata, flags=re.MULTILINE)
    version = re.search(r"^Version:\s*(.+)$", metadata, flags=re.MULTILINE)
    license_name = re.search(
        r"^(?:License|License-Expression):\s*(.+)$",
        metadata,
        flags=re.MULTILINE,
    )
    text = license_path.read_text(encoding="utf-8").strip()
    copyright_match = re.search(r"^Copyright .+$", text, flags=re.MULTILINE)
    if (
        name is None
        or version is None
        or license_name is None
        or name.group(1).strip() != HY_MEMORY_NAME
        or version.group(1).strip() != HY_MEMORY_VERSION
        or license_name.group(1).strip() != "Apache-2.0"
        or copyright_match is None
    ):
        raise SbomError("hy-memory license metadata is incomplete")
    return {
        "name": HY_MEMORY_NAME,
        "version": HY_MEMORY_VERSION,
        "license": "Apache-2.0",
        "copyright": copyright_match.group(0).strip(),
        "text": text,
    }


def run(command: list[str], *, cwd: Path) -> str:
    executable = shutil.which(command[0])
    if executable is None:
        raise SbomError(
            f"{command[0]} is not on PATH; install it or set PATH before generating the SBOM"
        )
    result = subprocess.run(
        [executable, *command[1:]],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SbomError(f"{' '.join(command)} failed: {result.stderr.strip()}")
    return result.stdout


def javascript_packages() -> list[dict[str, str]]:
    """Runtime JavaScript dependencies, excluding workspace-internal links."""
    raw = run(
        ["pnpm", "list", "--prod", "--depth", "0", "-r", "--json"],
        cwd=ROOT,
    )
    packages: dict[tuple[str, str], dict[str, str]] = {}
    for entry in json.loads(raw):
        for name, info in (entry.get("dependencies") or {}).items():
            version = info.get("version") if isinstance(info, dict) else str(info)
            if not version or version.startswith("link:"):
                continue
            packages[(name, version)] = {
                "name": name,
                "version": version,
                "ecosystem": "npm",
            }
    if not packages:
        raise SbomError("no JavaScript runtime dependencies were found")
    return [packages[key] for key in sorted(packages)]


def python_packages() -> list[dict[str, str]]:
    environment = ROOT / "apps" / "core" / ".venv"
    python = next(
        (
            candidate
            for candidate in (environment / "bin" / "python", environment / "Scripts" / "python.exe")
            if candidate.is_file()
        ),
        None,
    )
    if python is None:
        raise SbomError("apps/core/.venv is missing; run uv sync before generating the SBOM")
    raw = run(
        [os.environ.get("UV_BIN", "uv"), "pip", "list", "--python", str(python), "--format", "json"],
        cwd=ROOT / "apps" / "core",
    )
    packages = [
        {
            "name": entry["name"],
            "version": entry["version"],
            "ecosystem": "pypi",
        }
        for entry in json.loads(raw)
        if entry.get("name") not in {"one-memory", HY_MEMORY_NAME}
    ]
    if not packages:
        raise SbomError("no Python dependencies were found")
    return sorted(packages, key=lambda entry: entry["name"].lower())


def spdx_id(package: dict[str, str]) -> str:
    slug = re.sub(r"[^A-Za-z0-9.-]", "-", f"{package['ecosystem']}-{package['name']}")
    return f"SPDXRef-Package-{slug}"


def purl(package: dict[str, str]) -> str:
    name = package["name"]
    if package["ecosystem"] == "npm":
        return f"pkg:npm/{name}@{package['version']}"
    return f"pkg:pypi/{name.lower()}@{package['version']}"


def project_license() -> str:
    """The SPDX id for this project's own LICENSE, read from the file itself.

    Naming a license the repository does not actually carry is the one error
    an SBOM must never make, so this refuses to guess.
    """
    license_path = ROOT / "LICENSE"
    if not license_path.is_file():
        raise SbomError("LICENSE is missing; cannot state the project license")
    text = license_path.read_text(encoding="utf-8")
    if "Apache License" in text and "Version 2.0, January 2004" in text:
        return "Apache-2.0"
    if "MIT License" in text:
        return "MIT"
    raise SbomError("LICENSE text is not a recognized license")


def build_spdx(packages: list[dict[str, str]], *, created: str) -> dict:
    hy_notice = hy_memory_notice()
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "one-memory",
        "documentNamespace": f"https://one-memory.local/spdx/{created}",
        "creationInfo": {
            "created": created,
            "creators": ["Tool: one-memory-generate-sbom"],
        },
        "packages": [
            {
                "SPDXID": "SPDXRef-Package-one-memory",
                "name": "one-memory",
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": project_license(),
                "licenseDeclared": project_license(),
                "copyrightText": "NOASSERTION",
            },
        ]
        + [
            {
                "SPDXID": spdx_id(package),
                "name": package["name"],
                "versionInfo": package["version"],
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                # Resolving every upstream license text is a separate,
                # reviewed step; claiming one here would be a guess.
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": purl(package),
                    }
                ],
            }
            for package in packages
        ]
        + [
            {
                "SPDXID": "SPDXRef-Package-hy-memory",
                "name": hy_notice["name"],
                "versionInfo": hy_notice["version"],
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": hy_notice["license"],
                "licenseDeclared": hy_notice["license"],
                "copyrightText": hy_notice["copyright"],
            }
        ]
        + [
            {
                "SPDXID": "SPDXRef-Package-memmy-agent-derived",
                "name": "memmy-agent (adapted host-bridge module)",
                "versionInfo": MEMMY_PIN,
                "downloadLocation": f"https://github.com/MemTensor/memmy-agent/tree/{MEMMY_PIN}",
                "filesAnalyzed": True,
                "licenseConcluded": "MIT",
                "licenseDeclared": "MIT",
                "copyrightText": "Copyright (c) 2026-present MemTensor",
                "comment": (
                    "Source-level adaptation under packages/host-bridge/src/"
                    "memmy-adapter/claude-code; no Memmy brand or visual assets "
                    "are distributed."
                ),
            }
        ],
        "relationships": [
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": "SPDXRef-Package-one-memory",
            },
        ]
        + [
            {
                "spdxElementId": "SPDXRef-Package-one-memory",
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": spdx_id(package),
            }
            for package in packages
        ]
        + [
            {
                "spdxElementId": "SPDXRef-Package-one-memory",
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": "SPDXRef-Package-memmy-agent-derived",
            }
        ],
    }


def build_notices(packages: list[dict[str, str]], *, created: str) -> str:
    if not MEMMY_LICENSE.is_file():
        raise SbomError("LICENSE.memmy is missing; cannot attribute Memmy")
    memmy_license = MEMMY_LICENSE.read_text(encoding="utf-8").strip()
    hy_notice = hy_memory_notice()
    lines = [
        "# Third-Party Notices",
        "",
        f"MemHub itself is licensed under {project_license()} (see `LICENSE`).",
        "It bundles the runtime dependencies listed below.",
        f"Generated from the installed dependency tree on {created}",
        "by `python3 scripts/generate_sbom.py`. Do not edit by hand.",
        "",
        "Per-package license texts are not reproduced here yet; the license",
        "column is filled in as part of release review, and the SBOM records",
        "`NOASSERTION` until then rather than guessing.",
        "",
    ]
    lines.extend(
        [
            f"## {hy_notice['name']} {hy_notice['version']}",
            "",
            f"License: {hy_notice['license']}",
            "",
            "```text",
            hy_notice["text"],
            "```",
            "",
            "## MemTensor memmy-agent (adapted)",
            "",
            f"Upstream commit: `{MEMMY_PIN}`",
            "License: MIT",
            "",
            "```text",
            memmy_license,
            "```",
            "",
            "Adaptation boundary:",
            "",
            (
                "`packages/host-bridge/src/memmy-adapter/claude-code/` is a "
                "source-level adaptation of Memmy's Claude integration. It is "
                "substantially rewritten for One Memory's authorization and "
                "storage contracts. The full upstream/adaptation boundary is "
                "recorded in the package's `PROVENANCE.md`."
            ),
            "",
            "No Memmy brand, icon, font or mascot assets are distributed.",
            "",
        ]
    )
    for ecosystem, heading in (("npm", "JavaScript (npm)"), ("pypi", "Python (PyPI)")):
        selected = [
            package for package in packages if package["ecosystem"] == ecosystem
        ]
        lines.append(f"## {heading} — {len(selected)} packages")
        lines.append("")
        for package in selected:
            lines.append(f"- {package['name']} {package['version']}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    source_date_epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if source_date_epoch:
        try:
            epoch = int(source_date_epoch)
            if epoch < 0:
                raise ValueError
        except ValueError as error:
            print("sbom generation failed: SOURCE_DATE_EPOCH must be a non-negative integer")
            raise SystemExit(1) from error
        created = datetime.fromtimestamp(epoch, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    else:
        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        packages = javascript_packages() + python_packages()
        spdx = build_spdx(packages, created=created)
        notices = build_notices(packages, created=created)
    except SbomError as error:
        print(f"sbom generation failed: {error}")
        raise SystemExit(1) from error

    # Written only once both artifacts are known-good, so a failure never
    # leaves a half-updated SBOM behind for the release gate to accept.
    (ROOT / "SBOM.spdx.json").write_text(
        f"{json.dumps(spdx, indent=2)}\n",
        encoding="utf-8",
    )
    (ROOT / "THIRD_PARTY_NOTICES.md").write_text(notices, encoding="utf-8")
    print(f"wrote SBOM and notices for {len(packages)} packages")


if __name__ == "__main__":
    sys.exit(main())
