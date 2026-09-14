# Python API reference

Generated directly from `src/doublegate_sdk` with mkdocstrings. Signatures,
annotations, member navigation and expandable source are built from this snapshot,
not copied by hand. Use the navigation rail to select a module.

| Module | Responsibility |
| --- | --- |
| [Check a file](authoring.md) | Apply a manifest to a file in one call; the offline operation the CLI runs. |
| [Manifest](manifest.md) | Export the schema and validate immutable gate packages. |
| [Package loader](package.md) | Read bounded POSIX files and hash validated manifest data. |
| [Evaluation](runtime.md) | Evaluate literal required strings without executing code. |
| [Evidence types](checks.md) | Carry findings and JSON-compatible payloads. |
| [Reason categories](reasons.md) | The five closed values a gate may give a submitter. |
| [Skipped reasons](skipped.md) | The four closed values a reducer may give for skipping an item. |
| [Response schemas](schema.md) | Validate published responses against a bounded Draft 2020-12 subset. |
| [CLI](cli.md) | Schema, validation and evaluation command entry point. |
| [Gate client](client.md) | Explicit HTTP/MCP status/recall/pending reads, opt-in proposals, bounded exchanges and the error taxonomy. |

Construct packages using `validate_manifest` or `load_package`, not by trusting
arbitrary direct `GatePackage` construction. Dataclass annotations are not runtime
validation. `Finding` is also a container, not a sanitizer: custom callers must
never put raw secrets in its fields. The bundled evaluator emits fixed diagnostics.

## Published without a reference page

Two modules are importable and used by the starters but have no generated page
in this build. Read them at the source until they do:

- `doublegate_sdk.vocabulary` — the nine closed blocker values and
  `normalise_blockers`.
- `doublegate_sdk.submission` — canonical bytes, `content_digest` derivation and
  `submission_artifact_id`. Its exceptions are **not** under `DoublegateError`;
  catch `CanonicalizationRefused` and `RouteIdMismatch` explicitly.
