# DoubleGate SDK roadmap

The SDK supports offline gate authors and explicit connections to gate services.
Gates own authorization, admission and publication; the SDK does not duplicate those
decisions. Organization Gate's proprietary boundary is unchanged.

## Release sequence

| Milestone | Scope | Acceptance | Status |
|---|---|---|---|
| SDK-1 Read access | Explicit HTTP/MCP transport; artifact status; recall; pending; typed failures | Real gate read-back over `POST /mcp`, unsupported-tool refusal, bounded exchanges, no implicit connection | HTTP/MCP slice implemented and verified against an isolated real gate; release checks pending |
| SDK-2 Memory lifecycle | Propose, operation status, recall; distinct pending/admitted results | Proposal/admission/recall plus uncertain-outcome and scope negatives | Implemented over HTTP/MCP; stub-reviewed lifecycle verified on one isolated gate; cross-principal, live-model and release checks pending |
| SDK-3 Authenticated remote client | HTTPS against a real deployment with an enforced credential | 401 without a credential and 200 with it, **on the MCP endpoint itself**, against a real remote gate | **Blocked on the service** — see below |
| SDK-4 Adapter reference | MCP reference flow and one native adapter, then a second independent consumer | Same lifecycle contract in both hosts; native history remains untouched | Planned |
| SDK-5 Corrections and resilience | Authorized withdrawal/correction; cancellation reconciliation; bounded batches | Revocation/cache, partial result and uncertain-write outcomes tested | Planned |
| SDK-6 Broader integrations | Additional harnesses/languages or generated clients when justified | Contract parity, package/dependency/license checks | Deferred |

## Implemented packet: SDK-1/SDK-2 over HTTP/MCP

The transport is the product's public integration surface, `POST /mcp`
(ADR-0050), in the stateless JSON request/response subset the server actually
serves — no SSE, no session id, no `initialize` handshake. The previous public
`UnixSocketTransport` has been **removed**: an internal local IPC channel is not
the SDK's public API, and this is pre-release, so no compatibility shim is kept.

The exposed operations are exactly the tools the maintained client tier serves:
`status`, `recall`, `pending`, `why` and (opt-in) `propose`. Anything else is
refused locally as unsupported rather than sent and blamed on the server.

### Deliberate absences

- **No inventory.** The client tier serves no inventory tool. `inventory`,
  `inventory_pages` and `InventoryPage` are gone rather than left to fail.
- **No whole-gate status.** `doublegate.status` requires an artifact id.
- **No base64 content.** `remember` takes text with no `encoding` parameter, so
  non-UTF-8 bytes are refused rather than stored as literal base64.

No connection on import or construction; no environment credential discovery; no
framework or database dependency. The caller selects endpoint, token, timeout and
byte limits. Server-side identity and authorization remain authoritative.

## Acceptance evidence

1. A real, isolated client gate answers `server/discover` and `tools/list`, and a
   propose → status → recall cycle completes over its own `POST /mcp` binding.
2. The served catalog observed in that run contains no inventory tool.
3. A read-only client is refused a write at the transport, not only at the facade.
4. Unsupported tools, redirects, oversized requests/responses, malformed replies,
   mismatched ids and 401/403/405 all fail explicitly and distinctly.
5. Unknown lifecycle states remain unknown, never normalised to admitted.
6. An uncertain write outcome is flagged, and nothing is retried automatically.
7. Offline SDK imports make no network connection and add no mandatory dependencies.

## SDK-3 blocker: the MCP endpoint is keyless on the client tier

`console.py` dispatches `POST /mcp` **before** its `_authed()` check; only
`/api/*` routes enforce the console bearer token. On that tier loopback is the
credential (INV-SEC-11 / FR-65), which is a coherent design for a local daemon
but means no run against it can demonstrate an authenticated MCP client.

What is proved today: the SDK sends `Authorization: Bearer …` when given a token,
withholds it from errors, refuses redirects that would carry it elsewhere, and
maps 401/403 to distinct error kinds — all against scripted responders. The
product's real token guard is exercised separately on `/api/status` (401 without,
200 with).

Closing SDK-3 requires a serving surface that enforces a credential on the MCP
endpoint itself — the org tier's keyed surface, or a client-tier change. That is
the service's decision; no server-side change was made here.

## Boundaries

### Developer workflow implemented

- Offline `describe-client` follows executable client signatures, names the tool
  behind each operation, and lists what is explicitly unsupported.
- One `scripts/check.py` entry point checks source identity, full tests/coverage
  and strict docs.
- A short root `llms.txt` links actual APIs, examples, tests and constraints.

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
