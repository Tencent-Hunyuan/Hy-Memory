# Host Context Transport v1

Status: legacy compatibility protocol used before SDK data-plane cutover.
Post-cutover hosts use SDK-native Context v2 at
`POST /api/v2/context/search`. A v2 response is never converted into v1 by
inventing revisions, Evidence IDs, paths, or adoption state.

## Transport

- `om_context` sends an authenticated Broker `/context` request to
  `POST /api/v1/context/search`.
- Request validation covers `query`, `kinds`, `raw_source_ids`, `limit`,
  `character_budget`, and `timeout_ms`. Caller-supplied identity, arbitrary
  scopes, and workspace fields are rejected.
- Existing `om_recall` and `om_conversations` rights are composed rather than
  replaced by a universal context grant. At least one requested lane must be
  authorized.
- Raw access requires `all-apps` plus source grants. Memory access requires an
  existing memory scope. Explicit unauthorized raw sources are rejected before
  Core is called.
- Broker captures installation identity and revision, then re-authenticates
  before releasing results. Revocation, credential rotation, or scope
  narrowing invalidates the release.

## Response and failure semantics

- Client and Broker validate response shape, item count, character usage, and
  requested lane selection before content becomes model-visible.
- Paths and partial zero-hit results remain intact; adoption is never inferred.
- Unknown payloads and unauthorized returned lanes fail closed.
- A v1 Core returning 404, 405, or 501 maps to
  `context_upgrade_required`; the Broker does not retry by combining separate
  legacy reads.
- Failure messages do not include provider bodies, credentials, private paths,
  or source content.
- MCP cancellation aborts only the matching active request. Deadlines include
  credential discovery and transport, and a late result is never released
  after cancellation.
- Raw anchors must refer to messages present in the item. Message IDs are
  unique, and grounded/empty/unavailable states must agree with item presence.

## CLI and managed host assets

- The recall executable accepts `--context`, repeated `--kind` and
  `--raw-source`, shared budgets, and JSON or provenance-preserving text.
- Unavailable or denied requests exit with code 4; runtime upgrade requirements
  exit with code 5. Empty and partial receipts remain machine-readable.
- Managed Skills prefer the context capability when exposed. Legacy invocation
  remains available for memory-only compatibility and self-checks, not as an
  automatic retry path.
- Generated commands retain the private credential-file path without shell
  evaluation. Journaled upgrade and user-modified asset protections remain in
  force.

## Verification requirements

Tests must cover:

- authenticated routing and strict request/response validation;
- lane and source authorization before Core invocation;
- revocation, credential rotation, and installation-revision races;
- character, item-count, metadata, and timeout budgets;
- client and Broker cancellation without late dispatch or release;
- generated command escaping and managed-Skill upgrades;
- fixed safe errors that do not expose upstream bodies or credentials.

Protocol tests use synthetic data and local test servers. Native-host adoption,
real model quality, and packaged-product acceptance require separate release
validation.
