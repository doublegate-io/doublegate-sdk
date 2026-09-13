# DoubleGate SDK roadmap

The SDK supports offline gate authors and explicit connections to gate services.
Gates own authorization, admission and publication; the SDK does not duplicate those
decisions. Organization Gate's proprietary boundary is unchanged.

## Release sequence

| Milestone | Scope | Acceptance | Status |
|---|---|---|---|
| SDK-1 Read access | Explicit transport; status; paginated inventory; typed failures | Real gate read-back, missing method/outage refusal, bounded paging, no implicit connection | Local socket slice implemented and source-tested; release checks pending |
| SDK-2 Memory lifecycle | Propose, operation status, recall; distinct pending/admitted results | Two-session proposal/admission/recall plus retry and scope negatives | Local proposal/recall implemented; same-peer stub-reviewed lifecycle verified; cross-principal, live-model and release checks pending |
| SDK-3 Adapter reference | MCP reference flow and one native adapter, then a second independent consumer | Same lifecycle contract in both hosts; native history remains untouched | Planned |
| SDK-4 Corrections and resilience | Authorized withdrawal/correction; cancellation reconciliation; bounded batches | Revocation/cache, partial result and uncertain-write outcomes tested | Planned |
| SDK-5 Broader integrations | Additional harnesses/languages or generated clients when justified | Contract parity, package/dependency/license checks | Deferred |

## Immediate packet: SDK-1

Start with the currently implemented local Unix-socket JSON-RPC surface through an
explicit transport. It is not the separately planned inter-deployment HTTP client.
Expose only status/inventory reads in the facade. Older gates lacking inventory
must return a typed unsupported-operation error, never an empty successful result.

No connection on import or construction; no environment credential discovery;
no framework or database dependency. Caller selects socket, timeout and response
limit. Server-side peer identity/authorization remains authoritative. Optional
transport injection allows contract tests and later HTTP support without pretending
all transports are already implemented.

Status: the service response remains authoritative. Inventory exposes retained
metadata separately from recall, preserves partial coverage and unknown lifecycle
states, and bounds pagination with a non-progress failure. Presence never implies
admission. Existing envelope and submission identities are not redefined.

## First acceptance cases

1. One real client gate returns its status through the SDK.
2. Inventory paging returns metadata, scope and coverage, not silently all data.
3. Absent inventory method returns unsupported; unavailable socket returns unavailable.
4. Unknown lifecycle states remain unknown, not admitted.
5. Oversized/malformed/incorrect-ID replies fail explicitly.
6. Pagination cannot loop forever or hide a partial page.
7. Offline SDK imports make no network connection and add no mandatory dependencies.

## Boundaries

### Developer workflow implemented

- Offline `describe-client` command follows executable client signatures and shared RPC mappings.
- One `scripts/check.py` entry point checks source identity, full tests/coverage and strict docs.
- A short root `llms.txt` links actual APIs, examples, tests and constraints.

These support AI-assisted development without introducing a harness framework or
requiring an agent to infer the public API from chat history.

### Client hardening completed in source

- Renamed the transport protocol to `GateTransport` to match its actual operation scope.
- Replaced reset-per-read timeouts with one transport I/O deadline.
- Added duplicate-JSON-key and contradictory inventory-response rejection.
- Real socket regressions cover slow-drip responses for reads and writes, including
  uncertain write outcomes. No automatic retries or authority shortcuts were added.
- Relative socket paths are pinned at construction; invalid path/timeout/text inputs
  fail before I/O with consistent diagnostics.
- The repository checker preserves failed-test counts and timeout output, and records
  snapshot failures without leaving misleading running-state evidence.

Remaining release work includes independent client review, HTTP transport contract,
cross-principal service tests and adapter compatibility. These are not implied by
local source-mode tests.

This roadmap does not authorize SDK installation, commits/pushes, restarting live
services or changing grants. Source-mode isolated verification is sufficient for
this implementation packet but must not be advertised as installed-release proof.
No async facade, write retry, model extraction engine or universal Store adapter is
added before a concrete consumer and its acceptance test require it.

Research basis: the pinned source investigation at
`../.tmp/memory-harness-research/investigation/` in the parent workspace selected
small typed clients, explicit outcomes/provenance and thin adapters. That path is
local research, not a dependency needed to use or build this SDK.
