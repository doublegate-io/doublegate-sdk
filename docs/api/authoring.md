# Check a file against a manifest

`evaluate_file` is the one call the offline half of the SDK is built around: give
it a check manifest and a file, get back evidence about that file. It is the same
operation the `evaluate` CLI subcommand runs, so a payload you print from Python
and a payload the CLI writes to stdout are the same object.

```python
from doublegate_sdk import evaluate_file

evaluation = evaluate_file('gate.json', 'runbook.md', artifact_type='memory')
if evaluation.flagged:
    for finding in evaluation.findings:
        print(finding.detail)
```

`artifact_type` is keyword-only and required. The SDK never infers it from a file
extension, and the manifest decides which types it covers: asking for one it does
not list is refused rather than silently accepted.

## What a result is, and is not

A `clean` outcome means every literal string the manifest requires was present in
the file. That is the whole claim. It is not admission, not publication authority
and not a completed review — `human_review` comes back on the result exactly as the
manifest declared it, and a clean check does not satisfy it. Nothing in this module
opens a connection or contacts a gate.

`manifest_digest` identifies the validated manifest data that produced this
evidence, so a stored payload says which gate version was applied. It does not
cover the evaluated file, and it is not a signature or a provenance record.

## Handling failures

Every failure below is a subclass of `DoublegateError`, so one `except` clause
covers the whole offline workflow while the specific types remain available when
you want to branch:

```python
from doublegate_sdk.errors import DoublegateError

try:
    evaluation = evaluate_file(manifest, path, artifact_type='memory')
except DoublegateError as error:
    print(f'check did not run: {error}')
```

| Raised by | Typical `str(error)` | Cause |
| --- | --- | --- |
| `ManifestError` | `invalid_manifest at /name: invalid_format` | The manifest failed schema validation. Carries `path` and `reason`. |
| `PackageError` | `invalid_json`, `unsafe_path`, `unreadable_file`, `file_too_large` | Unsafe/unreadable files or an oversized manifest. Oversized content is mapped to `EvaluationError('input_too_large')`. |
| `EvaluationError` | `unsupported_artifact_type`, `invalid_utf8`, `input_too_large` | The file was read but could not be evaluated under this manifest. |

Diagnostics are fixed strings. They never contain the file's contents, and
`PackageError` deliberately omits paths, so an evaluation failure is safe to log.

`DoublegateError` currently covers this offline workflow and the gate client's
`GateError`. Submission and observability helpers raise their own exception types
and are not yet under that base; catch those explicitly if you use them.

## When to use the low-level functions instead

`evaluate_file` composes three public functions that remain available and
unchanged. Use them directly when the composition does not fit:

- [`load_package`](package.md) once, then [`evaluate`](runtime.md) many times, to
  apply one manifest to many bodies without re-reading and re-hashing it.
- [`evaluate`](runtime.md) alone, for content that never came from a file.
- [`read_bounded`](package.md) alone, for the bounded, symlink-refusing read.

::: doublegate_sdk.authoring
