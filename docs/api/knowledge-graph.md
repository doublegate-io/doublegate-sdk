# Organization graph diagnostic (draft)

`doublegate_sdk.knowledge_graph.OrgGraphDiagnosticReader` is an **in-process,
organization-wide admin diagnostic**, not a tenant API, scoped reader, or
current review-authority eligibility decision. Never mount it behind a tenant,
member, or reader route. A space is not an authorization boundary.

```python
from doublegate_sdk.knowledge_graph import GraphQuery, OrgGraphDiagnosticReader
result = OrgGraphDiagnosticReader(daemon).read(
    GraphQuery(roots=(artifact_id,)), api_key=current_admin_key)
```

Only outgoing edges from explicit roots and their immediate targets are returned.
This is one-hop traversal, **not a resource-bound guarantee**: the complete
memory and graph exports and their fan-out are unbounded. There is no pagination,
node/edge budget, timeout, multi-hop traversal, or refinement-filter API.

Every read checks the current admin credential under the daemon lock, intersects
legacy memory rows with the global `serving.hidden_ids` projection, and removes
edges with ineligible endpoints before selection. Held, demoted, globally hidden
and dangling endpoints are excluded. Audience-specific hides are not enforced as
tenant isolation. Review-authority withdrawal/revocation eligibility is not
verified: do not use results to authorize content use. No cache or writes.

Nodes identify **legacy artifacts**, not submission IDs or claim UUIDs. Provenance
is copied from the actual export into a read-only mapping; nodes/edges are frozen.
Original relation direction, identity, timestamp and event ID are preserved;
an empty derives-from event ID is not an invented retractable assertion ID.
There is no evidence-manifest, assertion-withdrawal, or replacement-frontier API.

## Supported experimental duck-type contract

The constructor deliberately imports no private gate package and adds no SDK
runtime dependencies. It does depend on private OrgDaemon behavior, not a stable
public protocol. The separately supplied daemon must provide:

- `_lock`: a reentrant context-manager lock shared by export/projection mutations.
- `apikeys.authorize(raw, 'admin')`: `(status, record)` with status 200 only for a
  current admin credential; denial becomes `PermissionError` before export.
- `_memories_for_api(None)`: organization-wide legacy rows with `artifact_id`,
  `space`, `content_type`, `body`, and a `provenance` mapping.
- `serving.hidden_ids(artifact_ids, None)`: the globally hidden ID set.
- `graph_payload()['edges']`: dictionaries with `from`, `kind`, `to`, `event_id`,
  `identity`, and `ts`.
- `key.deployment_id`: the connection identity.

Compatibility evidence is restricted to archived client-gate commit
`b164e21b9e311a4f6d3925ce5736e75958e88d91` and organization-gate commit
`8988b0e63b4976f3f8aaff22da31fe1eeb1336a0`. New gate snapshots need fresh
integration acceptance; arbitrary duck types are not certified as safe.

## Runnable acceptance

Install this SDK wheel and separately built wheels from the two gate commits in
a disposable environment, then run `python examples/knowledge_graph.py`.
The example creates a real disposable OrgDaemon, ingests two artifacts, signs and
promotes them, queries the adapter and asserts the ledger did not change. It
prints live provenance and one derives-from edge, not fixture JSON.
Run `python -m pytest integration/test_knowledge_graph.py` in that environment.
Missing gate dependencies are errors, not skipped acceptance.
Run the standalone SDK `python -m pytest tests` separately without client-gate:
the SDK's namespace-isolation checks intentionally require that namespace absent.

## Baseline dependency hold

This draft starts at SDK `da35fa33d4e9009289ee4de46ee234b01ed6c44b`.
Fetched `origin/main` was `efc8e74`, nine commits ahead (the old base is its
ancestor). Thus the 36-test standalone suite is the complete **old-base** suite,
not the newer main's 210/247-test acceptance. Newer submission, schema, reason,
skipped and vocabulary contracts and packaging changes are absent from this
branch, not removed by this adapter. No schemas or prerequisites were deleted to
make tests pass. Hold merge/release until main is reconciled by its owner and
full current-main acceptance is rerun. No PyPI publication is authorized.
