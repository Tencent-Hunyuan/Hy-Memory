# HC-06/10/11 Host Context Transport v1

Implementation scope: Host Bridge, Broker, MCP, CLI and managed Skill v10 only.
Core aggregation/authorization adapters/routes remain owned by the main integration
task. This slice consumes its frozen `context_models.py` contract and the identical
`packages/contracts/src/context.ts` snapshot. No second context backend, event
store, model processing policy or cross-session monitor is introduced.

## Implemented behavior

- `om_context` sends one request through authenticated Broker `/context` to
  `POST /api/v1/context/search`. Legacy tools preserve their endpoints and shapes.
- Request validation matches query/kinds/raw_source_ids/limit/character_budget/
  timeout_ms. Identity, arbitrary scopes and workspace fields are rejected.
- Existing om_recall/om_conversations rights are composed, not replaced by a
  universal om_context grant. At least one requested lane must be authorized.
  Raw requires all-apps plus source grants; memory requires an existing memory
  scope. Explicit unauthorized raw sources reject before calling Core.
- Broker captures installation identity/revision and re-authenticates before
  releasing results. Revoke, rotation and narrowing invalidate the whole release.
  A missing grant store fails closed for this new endpoint.
- Client and Broker validate bounded response shape, counts, text usage and
  requested selection. Paths and partial zero-hit results remain intact; no
  inferred adoption. Unrecognized payloads never become model-visible content.
- Old Broker/Core 404/405/501 becomes context_upgrade_required. No fallback to
  two legacy reads. Failure messages exclude provider error bodies/credentials.
- MCP notifications/cancelled abort the matching active context HTTP call only.
  Client cancellation/deadline covers credential/discovery waits and transport;
  late credential resolution cannot dispatch a new request. Client disconnection
  aborts Broker's Core transport; Core owns cancellation of its actual read work.
- Existing recall executable adds `--context` as its first option. It accepts
  repeated --kind/--raw-source plus shared budgets, supports JSON or provenance-
  preserving text, and reads the installed private credential file.
- CLI unavailable/denied is exit 4, runtime upgrade is exit 5; ordinary empty or
  partial receipts remain machine-readable with their actual lane states.
- Skill v10 prefers the new capability when exposed; CLI documents explicit
  context mode. Legacy invocation remains for memory-only queries/self-checks,
  not as an automatic retry path. Loading a Skill is not retrieval.
- Generated CLI commands retain the credential-file path, and self-check parses
  generated escaped paths without shell evaluation. Existing journaled upgrade
  and modified-Skill protection stay in place; v9 upgrade coverage is added.

## Verification and scope

Tests use temporary POSIX installation stores and real local HTTP servers with
controlled Core responses. Native Node child processes execute the built
`dist/mcp.cjs` and `dist/recall-cli.cjs` bundles, using real temporary credential
files. This is protocol integration, not a real user host/model acceptance.

Initial new integration tests: 35 failures before implementation. Additional
cancel/response-bound tests exposed 5 failures; a generated-path test independently
failed before its fix. MCP cancellation was strengthened to distinguish explicit
cancellation from a later timeout rather than accepting either as evidence.

Run from packages/host-bridge after building:

```sh
node node_modules/tsup/dist/cli-default.js
node node_modules/typescript/lib/tsc.js --noEmit --pretty false
node node_modules/vitest/vitest.mjs run --exclude src/host-guidance.test.ts --reporter=dot
```

The exclusion is an earlier uncommitted diagnostic-feature red-test draft outside
this slice. It is not staged or delivered in this commit. Legacy expected-failure
and skipped tests remain reported separately. Final counts belong in STATUS and
the integration handoff after the final run, not inferred from intermediate runs.

Final 2026-09-19 verification: 548 passed, 1 expected failure, 1 skipped across
44 passed files and 1 skipped file (8.83s). This includes 47 context integration
tests and v9 managed-Skill upgrade coverage. TypeScript, tsup, 18-file Biome and
diff whitespace checks passed. The Core implementation is not part of this
test run. No native host conversation, private data or real model was exercised.

## Integration notes

### Defensive follow-up, 2026-09-19

Main review identified a release-boundary gap: request kinds default to both
lanes, so validating only request selection was insufficient defense against
a faulty Core returning raw content to a memory-only installation. Broker now
checks each returned item's lane and raw source against its captured trusted
identity, in addition to its existing installation-revision release recheck.
Memory content-level scope filtering remains Core's responsibility; the wire
memory item does not carry authority and cannot establish its own scope.

Raw anchors must occur in the item's messages, message IDs must be unique, and
grounded/empty/unavailable states must agree with whether items exist. Partial
zero-hit responses remain valid. Invalid upstream responses release only the
fixed context_unavailable error, not the unauthorized body.

CLI workspace guidance is now explicitly legacy-only; context rejects that
field. Skill version is 11, with an added v10 journaled upgrade regression.
New tests first produced 10 failures and 3 positive controls. Final follow-up
regression: 565 passed, 1 expected failure, 1 skipped in 9.64s; context integration
contains 60 cases. TypeScript, tsup, seven-file Biome and diff checks passed.
The same earlier uncommitted host-guidance draft is excluded. No real host or
Core/model acceptance is claimed by this defensive transport test suite.

- Main already owns the same context.ts type file; retain one copy and the
  `.js` type export when merging. Main's Core route is a runtime dependency.
- No Core files, source permissions, model settings or private user data changed.
- No active host configuration or daily installed App was replaced. A source
  commit does not update previously loaded tools/Skills: rebuild the product,
  apply the managed upgrade and validate a new host session separately.
- Cross-session CS research, earlier recall diagnostics and host-version health
  drafts are deliberately excluded; this is not completion of all HC requirements.
- No real Core service/model or native WorkBuddy/Codex conversation was started by
  these tests. Native host adoption, real provenance/quality and full product
  packaging remain main-integration acceptance tasks.
