# Monorepo layout

The repository currently contains two independently buildable projects:

```text
hy-memory/
├── .github/           # Monorepo CI, native lifecycle and Release workflows
├── hy_memory/          # Hy Memory SDK implementation
├── tests/              # SDK tests
├── plugins/            # SDK integrations
├── pyproject.toml      # SDK package definition
├── docs/               # Shared and SDK documentation
└── memhub/             # End-user application workspace
    ├── apps/core/      # Python Core / Runtime data plane
    ├── apps/cli/       # CLI
    ├── apps/desktop/   # macOS Electron application
    ├── apps/web/       # Web UI
    ├── packages/       # Contracts, host bridge, operations, runtime control
    └── scripts/        # Build, packaging, release and verification tooling
```

## Repository policy

The SDK remains at the repository root so its Python package layout and release
flow stay stable. MemHub remains a complete subproject under `memhub/`. This
public monorepo is the source of truth for both components.

Release workflows may generate reviewed source or binary artifacts, but those
artifacts do not replace this repository. Internal coordination notes,
credentials, private acceptance evidence, generated reports, and build outputs
do not belong in the public source tree.

## Current boundaries

- SDK commands run from the repository root.
- MemHub commands run from `memhub/`.
- Root GitHub workflows run SDK and MemHub checks from their respective working
  directories. Native lifecycle workflows produce reviewable Actions artifacts;
  tag releases remain fail-closed on public identity, signing and notarization.
- Hy Memory is published as `hy-memory==2.0.4`. MemHub pins that public
  distribution; its selected npm package is `memhub-cli` 1.0.5.
- Hy-Memory source uses Apache-2.0 at the repository root. Third-party
  components retain their own licenses and attribution notices.
