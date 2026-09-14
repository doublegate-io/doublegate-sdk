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
