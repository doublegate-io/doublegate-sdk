# Check a file before anyone reviews it

**Status: fully working.** Everything on this page runs offline against the SDK
you have installed. No gate is contacted.

You have content — a runbook, a release note, a skill file — and a rule about
what it must contain. You want that rule enforced mechanically, in CI or a
pre-commit hook, before it reaches a human. That is what a check manifest and
[`evaluate_file`][doublegate_sdk.authoring.evaluate_file] do.

## What a check result is, and is not

A `clean` outcome means one thing: the manifest's literal required strings were
present in the file, matched case-sensitively, with no regex and no execution.

It is **not** an admission decision, and it does not discharge review. The
manifest's `human_review` value travels into every result for exactly that
reason — a clean check that dropped it would read like an approval.

## The starter

[`examples/starters/checks.py`](https://github.com/doublegate-io/doublegate-sdk/blob/main/examples/starters/checks.py)
evaluates any number of files against one manifest and prints a machine result.

```console
$ python examples/starters/checks.py --help
$ python examples/starters/checks.py
$ python examples/starters/checks.py --format text
$ python examples/starters/checks.py --manifest my.gate.json --artifact-type memory notes/*.md
```

Run with no arguments it uses the bundled fixtures in
`examples/starters/data/`: one release note that satisfies the manifest and one
that omits the `Rollback:` line.

```json
{
  "artifact_type": "memory",
  "counts": {"clean": 1, "flagged": 1, "unevaluated": 0},
  "files": [
    {
      "path": ".../release-note.clean.md",
      "status": "clean",
      "artifact_type": "memory",
      "outcome": "clean",
      "name": "release-note",
      "version": "1.0.0",
      "digest": "cfd60e994148aa55…",
      "human_review": "required",
      "findings": []
    },
    {
      "path": ".../release-note.flagged.md",
      "status": "flagged",
      "artifact_type": "memory",
      "outcome": "flagged",
      "name": "release-note",
      "version": "1.0.0",
      "digest": "cfd60e994148aa55…",
      "human_review": "required",
      "findings": [
        {"kind": "required-text", "severity": "warning",
         "detail": "required_text_missing: check 1",
         "start": 0, "end": 0, "excerpt": ""}
      ]
    }
  ],
  "human_review_satisfied": false,
  "is_admission_decision": false,
  "manifest": ".../release-note.gate.json"
}
```

Paths are abridged above; the real run prints them in full. Both file entries
carry the same `digest` because both were evaluated against the same manifest —
the digest identifies the manifest, never the file it was applied to.

Note the empty `excerpt`. The bundled evaluator emits fixed diagnostics and
never quotes your content back at you, so a check result is safe to log.

## Exit statuses

| Status | Meaning                                              |
|--------|------------------------------------------------------|
| `0`    | every file was clean                                 |
| `1`    | at least one file was flagged                        |
| `2`    | at least one file or the manifest could not be read  |

That is the whole CI integration:

```yaml
- run: python examples/starters/checks.py --manifest gates/release.gate.json docs/release/*.md
```

A file that cannot be evaluated does not abort the run. It becomes an
`unevaluated` entry carrying the SDK's fixed diagnostic kind
(`unreadable_file`, `not_regular_file`, `unsafe_path`, `invalid_utf8`,
`input_too_large`, …), so one bad file does not hide the verdict on the rest.
A content file over the manifest's `max_input_bytes` reports `input_too_large`,
not `file_too_large` — the latter is the manifest's own 65,536-byte bound.

## The manifest

`examples/starters/data/release-note.gate.json`:

```json
{
  "schema_version": "0.1",
  "sdk_version": "0.1",
  "name": "release-note",
  "version": "1.0.0",
  "artifact_types": ["memory"],
  "checks": [
    {"capability": "required-text", "text": "Owner:"},
    {"capability": "required-text", "text": "Rollback:"}
  ],
  "max_input_bytes": 4096,
  "human_review": "required",
  "egress": [],
  "secret_refs": []
}
```

`egress` and `secret_refs` are bounded to zero items by the schema. A manifest
is declarative data: nothing in it can open a connection or name a credential.

For the manifest schema in full, see
[Manifest](../api/manifest.md); for the underlying calls, see
[Load a check manifest](../api/package.md) and [Evaluation](../api/runtime.md).

## Doing it in your own code

```python
from doublegate_sdk import evaluate_file

evaluation = evaluate_file("gates/release.gate.json", "docs/release/2026.09.md",
                           artifact_type="memory")
if evaluation.flagged:
    for finding in evaluation.findings:
        print(finding.kind, finding.detail)
print("review still required:", evaluation.human_review)
```

To apply one already-loaded manifest to many bodies, skip `evaluate_file` and
use `load_package` + `evaluate` directly — the composition exists for
convenience, not as a gate around the lower-level calls.
