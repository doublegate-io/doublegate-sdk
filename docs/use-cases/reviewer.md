# Prepare for a reviewer adapter

!!! warning "Status: blocked. This SDK publishes no reviewer stage."

    There is no reviewer adapter type, no reviewer protocol, no stage schema and
    no client-tier tool that carries a verdict. If you came here for "plug your
    model into the gate", that does not exist yet, and this page will not
    pretend otherwise by inventing a schema and calling it the contract.

    What you *can* do today is build and test the input/output contract **your
    application** owns, entirely offline, so the day a reviewer stage ships you
    are adapting working code instead of starting.

## Prove the gap yourself

Do not take this page's word for it. The starter probes the installed package:

```console
$ python examples/starters/reviewer.py --probe
{
  "supported": false,
  "candidate_detected": false,
  "python_symbols": [],
  "client_tier_tools": [],
  "probed_symbols": ["doublegate_sdk.reviewer", "doublegate_sdk.review", ...],
  "probed_tools": ["doublegate.review", "doublegate.judge", "doublegate.verdict", "doublegate.admit"],
  "available_client_tools": ["doublegate.ban", "doublegate.hide", "doublegate.pending",
                             "doublegate.rank", "doublegate.recall", "doublegate.remember",
                             "doublegate.status", "doublegate.tip", "doublegate.why"],
  "diagnostic": "reviewer_stage_unavailable: …"
}
```

The probe reports candidate names for inspection under `candidate_detected`.
They do not prove a compatible reviewer contract, and they do not change the
verdict: `supported` is a constant `false` in this build. It is not feature
detection — there is no reviewer code path here for a matching name to reach,
so any other value would claim an effect that cannot happen. Enabling a
reviewer stage takes an implemented, tested binding, never a newly matching
symbol.

`--probe` alone always exits `0`; it is a report, not an assertion. To make the
gap fail a build rather than merely report itself:

```console
$ python examples/starters/reviewer.py --require-stage   # exits 3 today
```

## What the starter does instead

[`examples/starters/reviewer.py`](https://github.com/doublegate-io/doublegate-sdk/blob/main/examples/starters/reviewer.py)
exercises one offline reviewer exchange using only machinery the SDK actually
publishes:

| Concern | SDK machinery used |
|---|---|
| Is the reviewer *input* well-formed? | [`schema.check_response`](../api/schema.md) against a caller-owned schema |
| Is the *evidence* in it real? | [`authoring.evaluate_file`](../api/authoring.md) over a fixture |
| Is the content identity derived correctly? | `submission.envelope_content_hash` → `content_digest_from_content_hash` |
| Is the *verdict* well-formed? | `check_response` plus the SDK's closed vocabularies |
| Are the verdict's enumerated values legal? | [`reasons.normalise_category`](../api/reasons.md), `vocabulary.normalise_blockers`, [`skipped.normalise_skipped_reason`](../api/skipped.md) |

```console
$ python examples/starters/reviewer.py
{
  "reviewer_stage": "unavailable",
  "contract_owner": "caller",
  "live_model_invoked": false,
  "gate_contacted": false,
  "admission_effect": "none",
  "derived_input": {
    "artifact_type": "memory",
    "content_digest": "sha256:17b6ea2b…",
    "deterministic_outcome": "flagged",
    "human_review": "required",
    "excerpt_policy": "withheld",
    "findings": [{"kind": "required-text", "severity": "warning",
                  "detail": "required_text_missing: check 1",
                  "start": 0, "end": 0, "excerpt": ""}]
  },
  "derived_input_violations": [],
  "fixture_input_violations": [],
  "fixture_output_violations": [],
  "vocabulary_violations": [],
  "valid": true
}
```

Those four flags are the point. No model ran, no gate was contacted, and nothing
was admitted.

## Read this before you reuse the schemas

`examples/starters/data/reviewer-input.schema.json` and
`reviewer-output.schema.json` are **caller-owned fixtures, not a product
contract**. Both say so in their own `description` field. They are shaped so
that the parts which *can* be anchored are anchored:

* `category` reuses the five values `doublegate_sdk.reasons` closes.
* `blockers` reuses the nine values `doublegate_sdk.vocabulary` closes.
* `skipped_reason` reuses the four values `doublegate_sdk.skipped` closes.
* `excerpt` is bounded to `maxLength: 0` — the reviewer input carries the
  deterministic evidence, never the body.

Everything else in those files — the member names, the nesting, the fact that an
exchange has an "input" and an "output" at all — is this starter's invention. It
is not what the product will publish, and building against it as if it were is
the mistake this page exists to prevent. When a reviewer stage ships, delete the
schemas and keep the plumbing.

## Why the closed vocabularies are checked twice

The schema validates shape; the normalisers validate meaning. They disagree
usefully: a schema `enum` will happily accept `null` for `skipped_reason`, while
`normalise_skipped_reason` refuses `None` outright, because a skip that names no
reason is the silent skip that vocabulary exists to end. Running both is how you
find out that a verdict which *validates* would still be rejected by the SDK's
own normaliser.

## Doing it in your own code

There is nothing to call that submits a verdict, so what follows is
**preparation only**: the validators the SDK does publish, applied to a contract
your application owns.

### Derive real evidence for a reviewer input

```python
from doublegate_sdk import evaluate_file
from doublegate_sdk.submission import (content_digest_from_content_hash,
                                       envelope_content_hash)

evaluation = evaluate_file("gates/release.gate.json", "docs/release/2026.09.md",
                           artifact_type="memory")

reviewer_input = {
    "artifact_type": evaluation.artifact_type,
    "content_digest": content_digest_from_content_hash(
        envelope_content_hash(open("docs/release/2026.09.md", "rb").read())),
    "deterministic_outcome": evaluation.outcome,     # 'clean' or 'flagged'
    "human_review": evaluation.human_review,         # still outstanding
    "excerpt_policy": "withheld",
    "findings": [finding.to_dict() for finding in evaluation.findings],
}
```

`content_digest` is derived by hashing the bytes **once** into the envelope
spelling and then prefixing. The SDK's submission profile forbids hashing
content twice to fill that member, which is why the two calls are chained rather
than `envelope_content_hash` being called again.

`excerpt_policy` is a constant, not a choice: findings from the bundled
evaluator carry fixed diagnostics and an empty `excerpt`, so the body never
travels with the input.

### Validate a verdict against the SDK's closed vocabularies

```python
from doublegate_sdk.reasons import CATEGORIES, normalise_category
from doublegate_sdk.schema import check_response
from doublegate_sdk.skipped import SKIPPED_REASONS, normalise_skipped_reason
from doublegate_sdk.vocabulary import BLOCKERS, normalise_blockers

violations = check_response(output_schema, verdict)   # () when the shape is valid

category = normalise_category(verdict["category"])            # one of CATEGORIES
blockers = normalise_blockers(verdict["blockers"])            # a tuple from BLOCKERS
skipped = normalise_skipped_reason(verdict["skipped_reason"]) # one of SKIPPED_REASONS
```

`check_response` returns a tuple of violation strings — empty means valid — and
never raises for a merely invalid payload. The three normalisers raise
`ValueError` or `TypeError` instead, and that difference is the point of running
both: a schema `enum` will happily accept `null` for `skipped_reason`, while
`normalise_skipped_reason` refuses `None` outright, because a skip that names no
reason is the silent skip the vocabulary exists to end.

`CATEGORIES`, `BLOCKERS` and `SKIPPED_REASONS` are frozensets you can read
directly — use them to build your own schema `enum`s rather than copying the
values by hand, so a future addition reaches your contract automatically.

**What next.** Nothing remote. There is no call that carries this verdict to a
gate, and no admission follows from it. Keep the exchange in your own tests so
that when a reviewer stage ships you are adapting working code.

### Confirm the gap against your installed package

```python
from doublegate_sdk.client import describe_client

description = describe_client()
sorted(description["operations"])   # ['pending', 'propose', 'recall', 'status', 'why']
description["mcp"]["tools"]         # nine tools; none carries a reviewer verdict
```

Those five operations are the entire Python client surface in this build. Read
them yourself instead of taking this page's word for it.

## Exit statuses

| Status | Meaning |
|---|---|
| `0` | the exchange satisfies the local contract |
| `1` | it does not; violations are listed in the report |
| `3` | `--require-stage` was given and no reviewer stage is published |

A missing or unusable fixture exits `1` with a one-line message naming the file;
argparse usage errors exit `2`.

## Completion status

App and tests are complete and pass, and every command on this page runs fully
offline against the installed SDK. Nothing here opens a socket, so there is no
real-service acceptance to record for this starter — the only thing a run can
establish is that the *caller-owned* contract in `data/` holds against the SDK's
published validators and closed vocabularies. It is not evidence that a reviewer
stage exists, and not evidence that any verdict was admitted.
