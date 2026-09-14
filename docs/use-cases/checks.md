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

The starter above is a CLI. The same work in Python is one call:

```python
from doublegate_sdk import evaluate_file

evaluation = evaluate_file("gates/release.gate.json", "docs/release/2026.09.md",
                           artifact_type="memory")
```

`evaluate_file` returns a frozen `FileEvaluation`. These are its fields, all of
them, with the values a real run against the bundled fixtures produces:

| Field | Type | Value for `release-note.flagged.md` |
|---|---|---|
| `outcome` | `str` | `'flagged'` (the other value is `'clean'`) |
| `findings` | `tuple[Finding, ...]` | one `required-text` finding |
| `gate_name` | `str` | `'release-note'` |
| `gate_version` | `str` | `'1.0.0'` |
| `artifact_type` | `str` | `'memory'` |
| `human_review` | `str` | `'required'` |
| `manifest_digest` | `str` | `'cfd60e994148aa5537f7203e49d9570cab83b6df142a8efa727028426156a7bf'` |

Plus `.flagged` (a property, `outcome == 'flagged'`) and `.to_payload()` (the
JSON-ready dict the starter prints). `manifest_digest` identifies the
**manifest**, so every file evaluated against one manifest carries the same
digest.

Each `Finding` carries `kind`, `severity`, `detail`, `start`, `end` and
`excerpt` — and `excerpt` is always `''` from the bundled evaluator, so findings
are safe to log:

```python
for finding in evaluation.findings:
    print(finding.kind, finding.severity, finding.detail)
# required-text warning required_text_missing: check 1
```

**What next.** Nothing, remotely — this is offline evidence. `human_review`
still says `required`, and that obligation is yours to route to a person. If you
want the content in a gate, that is a separate `propose` call; see
[Ingestion](ingestion.md).

### Handling the failures

Everything the SDK refuses arrives as a `DoublegateError` subclass with a fixed
`kind`. Catch the base class and read `kind`; do not parse the message.

```python
from doublegate_sdk.errors import DoublegateError

try:
    evaluation = evaluate_file(manifest_path, content_path, artifact_type="memory")
except DoublegateError as error:
    kind = error.kind        # 'unreadable_file', 'invalid_utf8', 'input_too_large', …
```

| `kind` | Raised by | Cause |
|---|---|---|
| `unsafe_path`, `not_regular_file`, `unreadable_file` | `PackageError` | the manifest or content path |
| `invalid_json`, `duplicate_json_key` | `PackageError` | the manifest document |
| `file_too_large` | `PackageError` | the **manifest** over 65,536 bytes |
| `input_too_large` | `EvaluationError` | the **content** over the manifest's `max_input_bytes` |
| `invalid_utf8` | `EvaluationError` | the content is not UTF-8 |
| `unsupported_artifact_type` | `EvaluationError` | the manifest does not declare that type |

`file_too_large` and `input_too_large` are different bounds on different files.
Mixing them up is the most common misreading of a check failure.

### The importable recipe

[`examples/recipes/offline_checks.py`](https://github.com/doublegate-io/doublegate-sdk/blob/main/examples/recipes/offline_checks.py)
is the same logic as small named functions that return values instead of
printing, so you can call them from your own code:

```python
from recipes.offline_checks import check_many_files, summarize_checks

results = check_many_files("gates/release.gate.json",
                           ["docs/release/2026.09.md", "docs/release/2026.10.md"],
                           artifact_type="memory")
summary = summarize_checks(results)
```

`check_many_files` never stops at the first bad file: a path the SDK refuses
comes back as a `CheckedFile` with `status='unevaluated'` and the SDK's `kind`
under `error_kind`. `summarize_checks` returns exactly this, for the bundled
fixtures:

```python
{'counts': {'clean': 1, 'flagged': 1, 'unevaluated': 0},
 'total': 2,
 'human_review': ['required'],
 'is_admission_decision': False,
 'human_review_satisfied': False}
```

The last two members are constants. They are emitted anyway so nothing
downstream has to infer admission from an outcome string.

`blocking_findings(evaluation, severities=...)` filters findings to the
severities *you* treat as blocking. The default is `{'error'}`, and the bundled
manifest emits `warning` — so the default returns `()` for it. Whether a warning
should fail your pipeline is a policy decision the SDK does not make for you.

To apply one already-loaded manifest to many bodies, skip `evaluate_file` and
use `load_package` + `evaluate` directly — the composition exists for
convenience, not as a gate around the lower-level calls.
