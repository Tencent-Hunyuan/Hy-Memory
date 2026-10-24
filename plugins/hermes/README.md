# Native Hermes memory provider

`hy_memory` implements the Hermes MemoryProvider contract pinned by this
repository to Hermes v0.21.6. Its primary product mode is a MemHub-managed,
read-only recall provider:

```text
Hermes queue_prefetch / prefetch
  → MemHub loopback Broker /context
  → authorized Core Context v1 or SDK-native Context v2
```

The provider uses Python's standard library only. It does not embed the Hy
Memory SDK, own a database, start a server, or receive a Core bearer token.

## MemHub-managed installation

MemHub's Hermes “Connect” action installs:

```text
~/.hermes/plugins/hy_memory/
  __init__.py
  plugin.yaml
  provider.py
  connection.json

~/.hermes/config.yaml
  memory.provider: hy_memory
  mcp_servers.one-memory: ...

~/.hermes/skills/memhub-recall/SKILL.md
```

`connection.json` contains only the Broker discovery path and the private host
credential file path. The credential remains in MemHub's owner-only permission
store. Each request reloads discovery and credential state, validates loopback
Broker identity, refuses redirects/proxies, and uses the host grant.

Clicking Connect is approval to replace the current Hermes
`memory.provider`. The install journal records the previous value. Turning the
integration off through MemHub restores it if the setting and provider files
have not been modified since installation.

Deleting the MemHub application directly may not provide a cleanup opportunity.
An orphaned provider therefore fails safely: Hermes continues normally without
recalled memory when the Broker or credential is unavailable.

Hermes must be fully restarted after installation or removal.

## Recall lifecycle

Automatic recall is enabled by default:

1. At the end of turn N, Hermes calls `queue_prefetch(query_N)`.
2. The provider performs authorized background recall.
3. At the start of turn N+1, `prefetch(query_N+1)` consumes the warmed result
   without blocking. The two query strings do not need to match.

Hermes retains previously injected context in later history. To prevent repeated
context growth, the provider maintains the set of injected opaque `memory_id`
values for each Hermes `session_id`:

- only IDs not previously injected in that session are returned;
- duplicate IDs within one response collapse to the first item;
- identical text with different IDs remains distinct;
- switching sessions uses that session's own set;
- reset/rewind clears the target session's set;
- results searched but never injected do not enter the set.

Only opaque IDs are persisted under
`$HERMES_HOME/.one-memory/hy-memory-seen.json`, so deduplication survives a
Hermes restart without storing recalled content.

`recall_status()` reports only newly injected items.

Recalled content is external, untrusted data and never instructions.
Injected blocks use the same escaped `memhub_memory_context` boundary that
MemHub source adapters strip if a host persists injected context.

## Write and anti-recursion boundary

The MemHub-managed provider is read-only:

- it exposes `hy_memory_search` as an explicit fallback;
- it does not expose `hy_memory_add`;
- automatic capture is disabled;
- Hermes conversation ingestion remains owned by MemHub's source scanner.

This prevents recalled memory injected into Hermes history—or an assistant
paraphrase of it—from being written back as a new memory.

The legacy direct-service mode described below can opt into automatic capture,
but captures user-authored text only. It ignores assistant text and the
`messages` payload containing injected memory context. Explicit add remains a
deliberate user/tool action in that mode.

## Optional operator-managed direct mode

If no managed `connection.json` exists, the provider retains the contribution's
operator-managed direct mode. Configure it explicitly:

```sh
export HY_MEMORY_URL=http://127.0.0.1:19527
export HY_MEMORY_USER_ID=your-explicit-owner-id
export HY_MEMORY_AGENT_ID=your-explicit-agent-id
```

| Environment variable | Default | Accepted values |
|---|---|---|
| `HY_MEMORY_CONNECTION_FILE` | sibling `connection.json` if present | absolute managed connection file |
| `HY_MEMORY_USER_ID` | required in direct mode | nonblank, at most 256 characters |
| `HY_MEMORY_AGENT_ID` | required in direct mode | nonblank, at most 256 characters |
| `HY_MEMORY_URL` | `http://127.0.0.1:19527` | origin only |
| `HY_MEMORY_CA_FILE` | OS trust | explicit PEM CA for direct HTTPS |
| `HY_MEMORY_API_KEY` | unset | reverse-proxy Bearer credential |
| `HY_MEMORY_TIMEOUT` | `5` | finite seconds, 0.1–30 |
| `HY_MEMORY_WRITE_TIMEOUT` | read timeout | finite seconds, 0.1–120 |
| `HY_MEMORY_LIMIT` | `5` | integer, 1–20 |
| `HY_MEMORY_MAX_CHARS` | `4000` | integer, 256–16000 |
| `HY_MEMORY_MAX_BODY_BYTES` | `262144` | integer, 1024–1048576 |
| `HY_MEMORY_AUTO_RECALL` | `true` | `true` or `false` |
| `HY_MEMORY_AUTO_CAPTURE` | `false` | `true` or `false`; direct mode only |

The bare Hy Memory HTTP server does not authenticate or authorize clients.
Never expose it to untrusted clients. Remote direct mode requires HTTPS through
an authenticating gateway and network policy blocking direct backend access.

## Offline tests

```sh
PYTHONPATH="$PWD" python -m pytest plugins/hermes/tests -o addopts='' -q
python -m ruff check plugins/hermes
```

When `HERMES_MEMORY_PROVIDER_SNAPSHOT` is set, tests load that pinned Hermes
contract snapshot. Otherwise they use the installed contract or the local
fixture. Tests cover protocol, isolation, session deduplication, recursion
prevention, transport deadlines, and installation behavior; they do not claim
live recall quality.
