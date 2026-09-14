# Correct or withdraw something already admitted

!!! warning "Status: blocked. This SDK cannot withdraw an admitted artifact."

    The client tier serves nine tools. None of them removes an artifact,
    replaces its content, or retracts it. There is no delete RPC, and this page
    will not invent one.

    Three of the nine *write* — `doublegate.rank`, `doublegate.hide` and
    `doublegate.ban` — and it is tempting to reach for `hide`. Don't. Hiding
    changes what a reader is served; the artifact, its content and its ledger
    stay exactly where they were. Reporting that as a withdrawal tells an
    operator something was removed when nothing was, which is worse than
    reporting failure.

## What is honestly available

| Want | Available? | What you get |
|---|---|---|
| Delete an admitted artifact | **No** | no tool on this surface |
| Retract / withdraw it | **No** | no tool on this surface |
| Replace its content in place | **No** | no tool on this surface |
| Record an explicit correction request locally | **Yes** | a canonical, id-bound work item |
| See where the artifact currently stands | **Yes** | `status` and `why`, read-only |
| Submit a *new* observation | Yes, separately | `propose` — a new artifact, not a correction of the old one |

[`examples/starters/correction.py`](https://github.com/doublegate-io/doublegate-sdk/blob/main/examples/starters/correction.py)
does the two available things and blocks loudly on the third.

## Prepare the correction request

```console
$ python examples/starters/correction.py --help
$ python examples/starters/correction.py --artifact-id sha256:<64 hex> --reason-category quality
```

```json
{
  "withdrawal": "unavailable",
  "remote_mutation_attempted": false,
  "correction_request": {
    "request_id": "sha256:9d04b36a…",
    "canonical_bytes_length": 904,
    "document": {
      "kind": "local-correction-request",
      "target_artifact_id": "sha256:0000…",
      "proposed_body": "# Release note 2026.09.1 (corrected)\n…",
      "provenance": {"observed_state": "held", "observed_by": "operations-oncall",
                     "observed_at": "2026-09-12T09:15:00Z", "basis": "…"},
      "reason_category": "quality",
      "carried_by": "human",
      "remote_effect": "none"
    }
  },
  "next_step": "Carry the prepared request to whoever holds correction authority…"
}
```

Abridged: the real run also prints a `probe` object and an `inspection` object
(`{"skipped": "no --endpoint given; run stayed offline"}` when offline).

The request is canonicalized with `doublegate_sdk.submission.canonical_bytes`,
so it has exactly one byte string and a derived `request_id`. That module has no
API reference page in this build — read it in
[`src/doublegate_sdk/submission.py`](https://github.com/doublegate-io/doublegate-sdk/blob/main/src/doublegate_sdk/submission.py).
That id identifies **this local document**.
It is not gate-issued and confers nothing — its value is that two operators who
prepare the same correction get the same id, and a request that was edited gets
a different one.

`reason_category` is optional and, when given, is passed through
`doublegate_sdk.reasons.normalise_category`, so it can only hold one of the five
values that contract closes.

## Inspect where the artifact stands

Optional, and off by default — without `--endpoint` the run is fully offline.

```console
$ export DOUBLEGATE_TOKEN=…    # read from the environment; there is no token flag
$ python examples/starters/correction.py --artifact-id sha256:… \
      --endpoint https://gate.example.org/mcp
```

The client is built with `allow_writes=False`. That is load-bearing rather than
decorative: the transport refuses a write-annotated tool itself, so this code
path cannot be talked into a mutation even by a caller that reaches past the
starter's own logic. Only `status` and `why` are called, and a failed inspection
reports the SDK's fixed error `kind` and exits `2` — it never degrades into a
write.

The token is read from `DOUBLEGATE_TOKEN` only. No token argument exists (a
credential on a command line lands in shell history and process listings), and
no token is printed in any output.

## Asking for withdrawal

```console
$ python examples/starters/correction.py --artifact-id sha256:… --withdraw
```

Exits `3`, writes the report to stderr, and changes nothing:

```
withdrawal_unavailable: this SDK exposes no tool that withdraws, retracts,
deletes or replaces an admitted artifact. doublegate.hide is visibility, not
removal, and this starter refuses to call it as if it were. Candidate names
alone do not establish compatibility: a matching tool or operation name is
reported for inspection under candidate_detected and does not flip this
result. The prepared request below has had no remote effect.
```

You still get the prepared correction request. A blocked integration should hand
back the work it *could* do, not an empty error.

Like the reviewer starter, the starter reads `describe_client()` and reports any
withdrawal-shaped operation or tool name it finds under `candidate_detected`.

!!! warning "A matching name will not enable withdrawal"
    `supported` is a constant `false`. It does not flip when a candidate name
    appears, and the probe is a *diagnostic hint*, not feature detection. That
    is deliberate: there is no code path in this starter that withdraws
    anything, so reporting `supported: true` on the strength of a name would
    claim an effect that still could not happen. When the product publishes a
    real correction path, this starter needs a new binding and a test — not a
    newly matching string.

## Exit statuses

| Status | Meaning |
|---|---|
| `0` | the correction request was prepared |
| `1` | a local refusal: an unreadable fixture, or an invalid artifact id or reason category |
| `2` | `--endpoint` was given and the inspection failed |
| `3` | `--withdraw` was given and withdrawal is not supported |

Exit `2` is shared with argparse's own usage errors, so distinguish them by the
output: a failed inspection prints the JSON report, a usage error prints usage
text.

## If you need this today

Until the product publishes a correction path, the workable route is procedural,
not programmatic:

1. Prepare the request with this starter and keep the `request_id`.
2. Inspect `why` to capture the artifact's ledger as it stands.
3. Hand both to whoever holds correction authority for that register.
4. If a *new* observation is appropriate, `propose` it as its own artifact and
   name the original in `derives_from`. That is a new record with declared
   lineage — it does not retract the original, and this starter does not do it
   for you, because "correction" and "new submission" must not be conflated by
   an automated step.
