# Gate memory client

`doublegate_sdk.client` is an explicit, optional import using only the Python
standard library. It adds no connection or credential discovery on import or
construction. Offline authoring and evaluation remain unchanged.

## Discover the client API

`python -m doublegate_sdk describe-client` emits the SDK's operations, RPC names,
read/write classification, parameters, defaults and return annotations as JSON.
The parameter list comes from actual Python signatures; operation mappings are
shared with the client and transport. Tests require every public client method
to be described. This is a Python-call description, not JSON Schema, MCP tool
declaration or a claim that a remote gate supports every operation.

## Local client-gate connection

```python
from doublegate_sdk.client import GateClient, UnixSocketTransport

client = GateClient(UnixSocketTransport('/explicit/gate/daemon.sock', timeout=5))
status = client.status()
page = client.inventory(limit=25)
for row in page.records:
    print(row['artifact_id'], row['state'])

for page in client.inventory_pages(limit=25, max_pages=4):
    if not page.complete:
        print('Partial coverage:', page.coverage_errors)
    # Process each page; presence does not imply admission.
```

The transport speaks the existing local Unix-socket JSON-RPC methods `dg.status`
and `dg.inventory`, plus `dg.recall` and explicitly enabled `dg.ingest`. It is not an HTTP client, SSO adapter or new authorization
layer. The receiving gate identifies the socket peer and enforces its permissions.
The caller must already be authorized to access that socket. Builds that lack
`dg.inventory` raise `GateError(kind='unsupported_operation', code=-32601)`.

A relative socket path is converted to an absolute path at construction, so a
later working-directory change cannot redirect the client to a different gate.
This pins path resolution against cwd changes, not the socket inode or symlink
target. Empty, byte-valued and NUL-containing paths are refused. Invalid timeout
types and invalid UTF-8 proposal text receive explicit validation errors before I/O.

Status responses preserve their service-defined shape. `InventoryPage` exposes
records, scope, counts, paging and coverage without converting unknown states to
approved states. Filters are the existing service parameters: state, space,
content_type, trust_class, retrievability, source, q, sort and descending. Unknown
parameter names are refused before transport. Filter values remain service-validated.

## Bounds and errors

The default timeout is one 10-second deadline shared by connection, sending and
all response reads. A peer cannot extend it by slowly sending additional bytes.
It bounds transport I/O, not arbitrary computation or OS scheduling latency.
Responses are capped at 1 MiB by default, configurable through
`max_response_bytes`; requests are capped at 1 MiB. No automatic retries occur.

`GateError.kind` distinguishes `unavailable`, `timeout`, `unsupported_transport`,
`unsupported_operation`, `remote_error`, `invalid_response`, `request_too_large`,
`response_too_large`, `writes_disabled` and `page_limit`. Remote errors retain their numeric `code`,
not their possibly sensitive message. Applications should map server-specific
codes explicitly; this version does not claim a universal authorization taxonomy.

`inventory_pages` raises `page_limit` if more data remains after the declared page
budget. A repeated/non-progressing offset is invalid rather than an infinite loop.
Inventory may change between requests: this API does not promise a snapshot across
pages. Consumers requiring a snapshot must use a future service-supported revision
contract, not assume the iterator creates one.

## Explicit transport injection

`GateClient` also accepts a `GateTransport` implementing
`call(method, params) -> dict`. This lets an application supply a supported service
transport without importing an agent framework into the SDK. The provided socket
transport itself refuses every method except the three read methods and an
explicitly enabled proposal write. A custom injected transport owns its own
write-enablement contract; none of these client-side flags grant server authority.

`GateTransport` replaces the misleading pre-release `ReadTransport` name now that
the interface also carries explicitly enabled proposals. Only the annotation's
name changed; `call(method, params)` is unchanged.

Duplicate JSON object keys and internally contradictory inventory paging/coverage
are rejected as `invalid_response`, not silently normalized into success.

## Propose and recall

```python
client = GateClient(UnixSocketTransport('/explicit/gate/daemon.sock', allow_proposals=True))
proposal = client.propose(
    'The laboratory labels basalt specimens by collection date.',
    content_type='memory', trust_class='T-4', source_uri='application://observation/123',
)
current = client.status(proposal['artifact_id'])
knowledge = client.recall('basalt specimens', limit=5)
```

Proposal accepts text or bytes, encoded without changing the content. Required
content type, trust classification and source URI are explicit; the receiving
gate still validates them. The SDK never supplies writer/deployment identity.
The returned state is the service's actual state, not an SDK claim of approval.

Recall always excludes the caller's unreviewed pending echoes. It also excludes
provisional knowledge by default. Set `include_provisional=True` explicitly when
the application's policy permits Solo-provisional results; each hit retains its
provisional flag, provenance and score. No score is relabelled as trust/confidence.

Writes are disabled in `UnixSocketTransport` unless `allow_proposals=True` was
provided. No proposal is automatically retried. If sending may have begun and the
response is lost, malformed or oversized, `GateError.outcome_unknown` is true:
the server may have retained the observation. Reconcile before resubmission;
content duplicate detection is not an exactly-once or idempotency-key guarantee.
The SDK does not clear findings, approve records or perform organization submission.

## Verified scope

Unit tests exercise real socket response parsing, response bounds, invalid input,
unavailable service, partial results and paging errors. A source-mode isolated
Client Gate run returned an empty inventory, then three product-ingested fixture
records across three pages; each status remained `L1_SCANNED`, not admitted.

This is local source-mode verification on Linux/WSL. It is not installed-wheel,
Windows, HTTP, cross-tenant, live-deployment or organization-delivery acceptance.
The consolidated gate source used for that run contains Inventory; the maintained
gate source may not yet expose that operation. No SDK installation was performed.

`examples/verify_memory_lifecycle.py` is an optional source-mode integration probe
requiring Client Gate in addition to the SDK. It verified proposal → pending
exclusion → duplicate recognition → product admission → recall of the same record,
with provisional results explicitly enabled. The reviewer is `StubBackend`:
this proves SDK/service lifecycle plumbing, not real model judgment quality.
Two SDK clients use the same local peer; this is not a cross-principal permission test.
