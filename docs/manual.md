# Doublegate offline SDK manual

The experimental SDK 0.1 loads a declarative JSON manifest, checks text locally,
and returns evidence. It does not install a gate, run plugins, contact a daemon,
or admit or publish an artifact. A `clean` result means only that the configured
literal strings were found.

## Run from the standalone checkout

Install this local repository with `python -m pip install .` in a virtual
environment. Run all commands below from the standalone repository root, using
that environment's Python. The console command `doublegate-sdk` is equivalent to
`python -m doublegate_sdk`. The package version is `0.1.0.dev3`; manifest and SDK
contract versions are both `"0.1"`. Source is available on GitHub; no PyPI release is claimed.

Python 3.11+ is required. The loader uses POSIX descriptor-relative operations and
`O_NOFOLLOW`; Linux/WSL is verified, native Windows loading is unsupported.
Examples are checkout fixtures, not wheel contents.

```sh
python -m doublegate_sdk --help
python -m doublegate_sdk schema --help
python -m doublegate_sdk validate --help
python -m doublegate_sdk evaluate --help
```

All four help commands exit `0`. The top-level command lists four subcommands:
`schema`, `describe-client`, `validate` and `evaluate`. The first two are pure
emitters that take no arguments and cannot fail on input; `describe-client`
describes *this SDK's* Python client offline and is documented on the
[client reference](api/client.md), not here. Evaluation takes a manifest, a
regular UTF-8 input file, and the required `--artifact-type` option. It does not
accept stdin or fetch URLs; `-` is not a stdin shortcut.

## Quick start: the included runbook gate

The complete manifest at `examples/gates/runbook/gate.json` is:

```json
{
  "schema_version": "0.1",
  "sdk_version": "0.1",
  "name": "runbook",
  "version": "1.0.0",
  "artifact_types": ["memory"],
  "checks": [{"capability": "required-text", "text": "Owner:"}],
  "max_input_bytes": 4096,
  "human_review": "required",
  "egress": [],
  "secret_refs": []
}
```

The included `examples/gates/runbook/pass.txt` contains:

```text
Owner: Operations
Restart the worker only after draining the queue.
```

### Export the schema

```sh
python -m doublegate_sdk schema
```

Exit `0`; stdout is the full JSON Schema, with `$schema` set to
`https://json-schema.org/draft/2020-12/schema` and title
`Doublegate declarative gate manifest 0.1`. Stderr is empty. Use the emitted
schema for editor tooling; the CLI validator supports this specific schema, not
arbitrary user-supplied JSON Schemas.

### Validate the manifest

```sh
python -m doublegate_sdk validate examples/gates/runbook/gate.json
```

Exit `0`, empty stderr, actual stdout:

```json
{"digest": "c0b4b8bf5d9c07147b99892547421127205e7a8815050c3bd0e5248a43a80745", "human_review": "required", "name": "runbook", "valid": true, "version": "1.0.0"}
```

Validation reads and validates the manifest only. It does not evaluate content,
verify authorship, grant permissions, or register the package anywhere.

### Evaluate matching content

```sh
python -m doublegate_sdk evaluate examples/gates/runbook/gate.json examples/gates/runbook/pass.txt --artifact-type memory
```

Exit `0`, empty stderr, actual stdout:

```json
{"artifact_type": "memory", "digest": "c0b4b8bf5d9c07147b99892547421127205e7a8815050c3bd0e5248a43a80745", "findings": [], "human_review": "required", "name": "runbook", "outcome": "clean", "version": "1.0.0"}
```

Notice that `human_review` is still `required`. The check has not performed or
satisfied human review.

### Observe a missing-text finding

This source-checkout demonstration uses `pyproject.toml` as arbitrary text: it
fits the example's byte limit and does not contain `Owner:`. The SDK does not
infer an artifact type from a file extension. For your own gate, supply your own
regular UTF-8 file rather than depending on this repository fixture.

```sh
python -m doublegate_sdk evaluate examples/gates/runbook/gate.json pyproject.toml --artifact-type memory
```

Exit `1`, empty stderr, actual stdout:

```json
{"artifact_type": "memory", "digest": "c0b4b8bf5d9c07147b99892547421127205e7a8815050c3bd0e5248a43a80745", "findings": [{"detail": "required_text_missing: check 0", "end": 0, "excerpt": "", "kind": "required-text", "severity": "warning", "start": 0}], "human_review": "required", "name": "runbook", "outcome": "flagged", "version": "1.0.0"}
```

Check indexes are zero-based and follow manifest order. Missing-text findings
have severity `warning`, empty excerpts, and zero start/end offsets: these are
not locations in the input. Multiple missing checks produce multiple findings.
The CLI returns neither the input body nor the required literal text in findings.
It does return manifest `name`, `version`, and review metadata; do not put secrets
in those fields. A flagged result is evidence, not a recorded admission refusal.

### Observe handled errors

The runbook manifest allows only `memory`, even though the CLI recognizes four
artifact types:

```sh
python -m doublegate_sdk evaluate examples/gates/runbook/gate.json examples/gates/runbook/pass.txt --artifact-type script
```

Exit `2`, empty stdout, actual stderr:

```json
{"error": "unsupported_artifact_type"}
```

Passing the example's text file as a manifest is not valid JSON:

```sh
python -m doublegate_sdk validate examples/gates/runbook/pass.txt
```

Exit `2`, empty stdout, actual stderr:

```json
{"error": "invalid_json"}
```

## The same check from Python

The CLI is a thin wrapper. `evaluate_file` is the operation itself, and it
produces the payload the CLI prints:

```python
from doublegate_sdk import evaluate_file

evaluation = evaluate_file('examples/gates/runbook/gate.json',
                           'examples/gates/runbook/pass.txt',
                           artifact_type='memory')
print(evaluation.outcome, evaluation.human_review)   # clean required
```

`evaluation.flagged` is the boolean behind exit code `1`, `evaluation.findings`
carries the same findings, and `evaluation.to_payload()` is the stdout object.
Failures the CLI reports as `{"error": ...}` with exit `2` are raised here
instead, all under `doublegate_sdk.errors.DoublegateError`.

`examples/quickstart.py` is this workflow as a runnable program. See
[the API page](api/authoring.md) for the result fields, the error table, and when
to drop to `load_package`/`evaluate` directly instead.

## Authoring `gate.json`

A package is **one JSON file**, not a directory bundle, archive, Python module,
or executable. `gate.json` is a convention; the CLI takes an explicit file path.
Use UTF-8 JSON, not general YAML. Manifest files are limited to **65,536 bytes**.
All fields below are required; there are no implicit defaults. Unknown fields
are rejected at the top level and inside each check. Duplicate JSON keys are
rejected rather than silently taking the last value.

| Field | Accepted value |
| --- | --- |
| `schema_version` | String `"0.1"` only. |
| `sdk_version` | String `"0.1"` only. |
| `name` | String, at most 64 characters; schema pattern `^[a-z][a-z0-9-]*$`. Use a lowercase name starting with a letter, followed by lowercase letters, digits, or hyphens. |
| `version` | String, at most 32 characters; schema pattern `^[0-9]+\.[0-9]+\.[0-9]+$`. Three numeric dot-separated components; no prerelease suffix or compatibility resolution. |
| `artifact_types` | Array of 1–4 distinct strings from `memory`, `skill`, `script`, `tool`. The requested evaluation type must be listed here. |
| `checks` | Array of 1–32 objects, each containing exactly `capability` and `text`. Repeated checks are permitted. |
| `checks[].capability` | String `"required-text"` only. |
| `checks[].text` | String of 1–256 characters, interpreted literally. |
| `max_input_bytes` | Integer from 1 through 1,048,576, inclusive. JSON booleans and floating-point numbers are not accepted as integers. |
| `human_review` | String `"inherit"` or `"required"`; metadata, not authority. |
| `egress` | Empty array `[]` only. |
| `secret_refs` | Empty array `[]` only. |

### What `required-text` does, and does not do

Every configured string must occur somewhere in the decoded input. Matching is a
case-sensitive substring test, without regex, word boundaries, trimming, Unicode
normalization, semantic interpretation, or document parsing. Whitespace and
newlines in a required string are significant. Length limits on manifest strings
count Python string characters; the input limit counts UTF-8 **bytes**. Non-ASCII
content can hit the byte limit before its character count reaches that number.

The runbook check accepts `Owner:` anywhere, including in a comment, a quotation,
or a larger word. It does not establish that the owner is real, that a runbook is
safe, or that a required section has valid content. An empty input is allowed as
a file but cannot satisfy a nonempty required string. Artifact types are declared
labels, not parsers: checking a `script` or `tool` does not execute it.

This CLI does not invoke the existing security scanner, an LLM grader, or a code
sandbox. Shared finding/result types do not imply shared inspection behavior.

## Files and permissions

- Both manifest and input must be local regular files readable by the invoking
  operating-system user. Parent directories must be accessible too. No extra
  SDK permission or credential is needed; normal filesystem permissions apply.
- Reads reject `..` path components and symlinks at every supplied path component,
  including parent directories. Use a real path without traversal components.
  Both absolute and relative paths are supported.
- Directories and special files are not accepted as content. The reader uses
  bounded reads and checks file size; it does not read indefinitely from a FIFO.
- These commands write results to stdout/stderr, not to an artifact store or a
  registration database. They do not change file permissions.
- `egress` and `secret_refs` are declarations constrained to empty arrays, not
  permission requests that the CLI can approve. Network destinations, secret
  references, executable capabilities, entry points, and authority override
  fields are not supported. Validation does not load or run manifest code.

This is a bounded declarative reader, not an isolation mechanism for arbitrary
third-party programs. It does not create a sandbox or verify that an input file
was immutable while another process was writing it.

## Digest scope

The `digest` is a lowercase SHA-256 hex digest of the **validated manifest data**.
The loader parses UTF-8 JSON, rejects duplicate keys, validates its fields, then
serializes with Python `json.dumps` using `sort_keys=True`, `separators=(',', ':')`,
and `ensure_ascii=True`. It hashes the ASCII bytes of that serialization.

Object-key order and insignificant JSON formatting do not affect the digest.
Array order does, including check order. Every manifest field participates,
including `human_review` and the two empty permission arrays. The digest does
**not** cover the evaluated input, input path, surrounding files, runtime code,
or environment. Matching digests are not proof of common authorship, a trusted
signature, approval, or equivalent input content. The CLI does not emit an input
content digest or a signed evidence record.

## Human review and authority

`inherit` and `required` are preserved as metadata in validation and evaluation
output. The offline runtime does not resolve an inherited policy, authenticate a
reviewer, collect a review, or enforce a human pin. It has no signing or
publication authority. `required` remains a requirement for a separate trusted
workflow to implement; `inherit` is not an approval or a way to disable review.

Do not wire exit `0` directly to publication on the assumption that Doublegate
has admitted the content. A consumer must separately enforce its admission and
human-review policy. This SDK slice does not supply that integration.

## Exit codes and output streams

| Exit | Meaning | Stream |
| --- | --- | --- |
| `0` | Schema/help emitted, manifest valid, or evaluation `clean`. | Stdout; schema is pretty-printed JSON, help is text, other successes are one JSON object. |
| `1` | Evaluation completed with one or more missing-text findings (`flagged`). | One JSON object on stdout. |
| `2` | Handled manifest, file, or evaluation error; also invalid CLI usage. | Handled errors are `{"error": "..."}` on stderr with no stdout. Argument-parser errors are usage text on stderr, **not JSON**. |

These are the CLI's normal return paths, not a promise that interpreter startup,
unsupported platforms, signals, or unexpected failures produce a JSON error.
In shell scripts, capture status before running another command and handle `1`
and `2` separately. Under fail-fast shell settings, both nonzero cases can stop
a script before it inspects the output. Argument-parser diagnostics can repeat
supplied arguments; do not put secrets in command-line arguments.

## Troubleshooting

| Symptom | Check / action |
| --- | --- |
| `No module named doublegate_sdk` | Install this local checkout with `python -m pip install .` in the intended environment. The namespace is `doublegate_sdk`, not `doublegate.sdk`. |
| `invalid_json` | Check UTF-8 encoding, JSON syntax, nesting, and duplicate keys. YAML-only syntax is not supported. Duplicate-key errors currently surface as `invalid_json` at the CLI. |
| `invalid_manifest at /...: ...` | Use the emitted schema. Reasons include `wrong_type`, `unsupported_value`, `unknown_field`, `required_field`, `array_bounds`, `duplicate_item`, `string_bounds`, `invalid_format`, and `integer_bounds`. Paths identify known fields/check indexes; unknown field names and rejected values are not echoed. |
| `unsafe_path` | Remove `..` components or an empty path. Select a direct local path. |
| `unreadable_file` | Check existence, access permissions, and symlink-free path components. OS open failures are deliberately collapsed into this diagnostic. |
| `not_regular_file` | Select a regular file, not a directory or special file. Some path-open failures instead report `unreadable_file`. |
| `file_too_large` | The **manifest** exceeded 65,536 bytes. Only the manifest reports this code; an oversized content file reports `input_too_large` instead. |
| `invalid_utf8` during evaluation | Supply valid UTF-8 input. Invalid manifest encoding instead reports `invalid_json`. |
| `unsupported_artifact_type` | Choose a CLI-supported type also listed in this manifest. Unknown CLI choices are parser errors instead. |
| Unexpected `flagged` | Inspect the zero-based missing-check index. Match exact case, spaces, and newlines; no normalization or regex is applied. |
| `clean` but review is still required | Expected: checks produce evidence and do not complete human review. |

`input_too_large` is the code for an oversized **content** file, whether it is
caught by the bounded read or by the evaluator: `evaluate_file` translates the
loader's `file_too_large` into `EvaluationError('input_too_large')` so that one
condition has one name regardless of which layer noticed. Keep content at or
below the manifest's `max_input_bytes`, and raise that declared limit only
within its allowed maximum of 1,048,576. The in-process evaluator also defines
`invalid_input_type` for direct callers passing a non-`str` body.

## Future scope, not current commands

This repository provides standalone packaging; PyPI publication remains out of scope. See [version policy](versions.md).

This slice also does not provide gate installation/registration, executable
plugins, general YAML, network or secret grants, dependency resolution, daemon
or organization integration, human-review UI, signed evidence, deployment,
policy administration, or admission/publication commands. Those require separate
implementation and verification; they are not hidden options of this CLI.

## Verification scope

Adapted from the client-gate offline manual. Standalone test cases exercise the
same manifest, CLI outputs, return codes, and unsafe-file rejection. The GitHub Actions workflow runs the tests, package build and strict documentation
build on each pull request and main push.
