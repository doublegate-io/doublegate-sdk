# DoubleGate SDK roadmap

The SDK supports offline gate authors and explicit connections to gate services.
Gates own authorization, admission and publication; the SDK does not duplicate those
decisions. Organization Gate's proprietary boundary is unchanged.

Two lines of work met in `0.1.0.dev5`: the HTTP/MCP client over `POST /mcp` with its
starter apps, authoring composition and observability, and the two `dg.*` clients
(`KnowledgeClient`, `CurationClient`) over the Unix socket and `POST /rpc`
(ADR-0074). The table below carries both; a row names the door it was proved on.

## Release sequence

| Milestone | Scope | Acceptance | Status |
|---|---|---|---|
| SDK-1 Read access | Explicit transports; artifact status; recall; pending; paginated inventory; typed failures | Real gate read-back, unsupported-verb refusal, bounded exchanges and paging, no implicit connection | Implemented on both doors: `/mcp` verified against an isolated real gate; socket and `/rpc` source-tested against a real `AF_UNIX` listener and a loopback HTTP server; release checks pending |
| SDK-2 Memory lifecycle | Propose, learn (content plus provenance), operation status, recall, annotations; distinct pending/admitted results | Proposal/admission/recall plus uncertain-outcome, retry and scope negatives | `/mcp`: propose → status → recall verified on one isolated gate. `/rpc` and socket: `KnowledgeClient` adds `learn`, annotations and inventory; same-peer stub-reviewed lifecycle verified. Cross-principal, live-model and release checks pending |
| SDK-3 Authenticated remote client | HTTPS against a real deployment with an enforced credential | 401 without a credential and 200 with it against a real remote gate | `/mcp`: **blocked on the service** (keyless on the client tier, see below). `/rpc`: the door enforces the console token (client) or the admin key (org) and maps 401/403 to `auth`/`scope`; proved against scripted responders, not a real remote deployment |
| SDK-4 Adapter reference | MCP reference flow and one native adapter, then a second independent consumer | Same lifecycle contract in both hosts; native history remains untouched | The crawler gate and the client gate's tier-1 plugin are the two consumers on the shared `dg.*` client; the MCP starters are the reference flow for the tool catalog |
| SDK-5 Corrections and resilience | Authorized withdrawal/correction; cancellation reconciliation; bounded batches | Revocation/cache, partial result and uncertain-write outcomes tested | `CurationClient` carries the operator's corrections (veto, reject, relate, override) under a per-verb proof; a proposed supersession by an agent stays a note until the daemon carries the field (client-gate GAPS 20). Not exposed over `/mcp` |
| SDK-6 Broader integrations | Additional harnesses/languages or generated clients when justified | Contract parity, package/dependency/license checks | Deferred |

## The three doors

| door | module | who | what |
|---|---|---|---|
| Unix socket | `doublegate_sdk.transport.UnixSocketTransport` | a process on the gate's host | every `dg.*` verb within the transport's scope |
| `POST /rpc` | `doublegate_sdk.transport.HttpTransport` | a console token (client) or admin key (org) holder | the same verbs, over HTTP, one transport for consoles (ADR-0073) |
| `POST /mcp` | `doublegate_sdk.client.HttpMcpTransport` | an MCP host | the tool catalog the maintained client tier serves: `status`, `recall`, `pending`, `why`, opt-in `propose` |

Nothing connects on import or construction; no environment credential discovery;
no framework or database dependency. The caller selects endpoint or socket, token,
timeout and byte limits. Server-side identity and authorization remain authoritative
on every door.

## The HTTP/MCP packet: SDK-1/SDK-2 over `POST /mcp`

The transport is the product's public integration surface, `POST /mcp`
(ADR-0050), in the stateless JSON request/response subset the server actually
serves — no SSE, no session id, no `initialize` handshake. The exposed operations
are exactly the tools the maintained client tier serves: `status`, `recall`,
`pending`, `why` and (opt-in) `propose`. Anything else is refused locally as
unsupported rather than sent and blamed on the server.

### Deliberate absences on the MCP door

- **No inventory tool.** The client tier serves no inventory tool over MCP; the
  `dg.inventory` verb is reachable through the knowledge client on the socket or `/rpc`.
- **No whole-gate status.** `doublegate.status` requires an artifact id.
- **No base64 content.** `remember` takes text with no `encoding` parameter, so
  non-UTF-8 bytes are refused rather than stored as literal base64. `dg.ingest`
  through the knowledge client carries bytes base64, unchanged.

### Acceptance evidence

1. A real, isolated client gate answers `server/discover` and `tools/list`, and a
   propose → status → recall cycle completes over its own `POST /mcp` binding.
2. The served catalog observed in that run contains no inventory tool.
3. A read-only client is refused a write at the transport, not only at the facade.
4. Unsupported tools, redirects, oversized requests/responses, malformed replies,
   mismatched ids and 401/403/405 all fail explicitly and distinctly.
5. Unknown lifecycle states remain unknown, never normalised to admitted.
6. An uncertain write outcome is flagged, and nothing is retried automatically.
7. Offline SDK imports make no network connection and add no mandatory dependencies.

### SDK-3 blocker: the MCP endpoint is keyless on the client tier

`console.py` dispatches `POST /mcp` **before** its `_authed()` check; only
`/api/*` and `/rpc` routes enforce the console bearer token. On that tier loopback
is the credential (INV-SEC-11 / FR-65), which is a coherent design for a local
daemon but means no run against it can demonstrate an authenticated MCP client.

What is proved today: the SDK sends `Authorization: Bearer <token>` when given a
token, withholds it from errors, refuses redirects that would carry it elsewhere,
and maps 401/403 to distinct error kinds — all against scripted responders. The
product's real token guard is exercised separately on `/api/status` (401 without,
200 with).

Closing SDK-3 requires a serving surface that enforces a credential on the MCP
endpoint itself — the org tier's keyed surface, or a client-tier change. That is
the service's decision; no server-side change was made here.

## The `dg.*` packet: SDK-1, SDK-2 and SDK-5 over the socket and `/rpc`

One operation table (`doublegate_sdk.operations`) lists every `dg.*` verb of both
catalogs with `mutates`, `proof`, `role` and `scope`; it is derived from the catalog
and asserted equal to it, so nothing in the SDK spells a verb the catalog does not.
A transport's scope (`read` | `knowledge` | `curation`) is the widest verb class it
emits, refused before I/O; the gate refuses again from the identity it derived off
the connection. The knowledge client never signs, promotes, demotes, rejects,
relates or bans. The curation client forwards an operator proof from a
`ProofProvider` and never loads a key.

### Acceptance evidence

1. Both transports drive a real `AF_UNIX` listener and a loopback `http.server`:
   allowlist before I/O, one shared deadline, byte caps, malformed and duplicate-key
   replies, lost-reply outcomes, the two code tables.
2. Every client method is asserted against a recording transport; every operator
   verb fetches a fresh nonce and sends the proof.
3. `examples/verify_knowledge_lifecycle.py` runs a real Client Gate in-process with
   the deterministic `StubBackend`: proposal → held → duplicate → admission →
   provisional recall → a learning with provenance. It is not live-model,
   cross-principal, Windows or organization-delivery evidence.
4. Inventory paging returns metadata, scope and coverage, never silently all data,
   and cannot loop forever or hide a partial page.
5. Absent inventory method returns `unsupported_operation`; unavailable socket
   returns `unavailable`. Presence never implies admission.

## Boundaries

### Developer workflow implemented

- Offline `describe-client` follows executable client signatures for all three
  doors: the `dg.*` clients' methods, the operation table, scopes and error tables,
  and under `mcp_client` the tool behind each MCP operation and what is explicitly
  unsupported there.
- One `scripts/check.py` entry point checks source identity, full tests/coverage
  and strict docs.
- A short root `llms.txt` links actual APIs, examples, tests and constraints.

### Client hardening completed in source

- One transport I/O deadline instead of reset-per-read timeouts.
- Duplicate-JSON-key and contradictory inventory-response rejection.
- Real socket regressions cover slow-drip responses for reads and writes, including
  uncertain write outcomes. No automatic retries or authority shortcuts were added.
- Relative socket paths are pinned at construction; invalid path/timeout/text inputs
  fail before I/O with consistent diagnostics.
- The repository checker preserves failed-test counts and timeout output, and records
  snapshot failures without leaving misleading running-state evidence.

### Remaining release work

Independent client review, an authenticated remote deployment test, HTTPS against
a real certificate chain, cross-principal service tests and adapter compatibility.
None of these are implied by source-mode loopback tests.

This roadmap does not authorize SDK installation, commits/pushes, restarting live
services or changing grants. Source-mode isolated verification is sufficient for
this implementation packet but must not be advertised as installed-release proof.
No async facade, write retry, model extraction engine, universal Store adapter or
general MCP framework is added before a concrete consumer and its acceptance test
require it.

Research basis: the pinned source investigation at
`../.tmp/memory-harness-research/investigation/` in the parent workspace selected
small typed clients, explicit outcomes/provenance and thin adapters. That path is
local research, not a dependency needed to use or build this SDK.
