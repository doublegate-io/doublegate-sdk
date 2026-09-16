# Python API reference

Generated directly from `src/doublegate_sdk` with mkdocstrings. Signatures,
annotations, member navigation and expandable source are built from this snapshot,
not copied by hand. Use the navigation rail to select a module.

| Module | Responsibility |
| --- | --- |
| [Manifest](manifest.md) | Export the schema and validate immutable gate packages. |
| [Package loader](package.md) | Read bounded POSIX files and hash validated manifest data. |
| [Evaluation](runtime.md) | Evaluate literal required strings without executing code. |
| [Evidence types](checks.md) | Carry findings and JSON-compatible payloads. |
| [CLI](cli.md) | Schema, validation and evaluation command entry point. |
| [Byte submission](submission.md) | Frozen v1 envelopes, canonical base64 and decoded-byte integrity; no authority. |
| [Identity verification](identity.md) | Optional access-token and attribution verification; identity, not authorization. |
| [Gate clients](client.md) | `KnowledgeClient` (an agent: remember, learn, recall, annotate) and `CurationClient` (the operator: approve, veto, relate, rank, keys) over one socket/HTTP transport layer, one operation table, one error vocabulary. |

Construct packages using `validate_manifest` or `load_package`, not by trusting
arbitrary direct `GatePackage` construction. Dataclass annotations are not runtime
validation. `Finding` is also a container, not a sanitizer: custom callers must
never put raw secrets in its fields. The bundled evaluator emits fixed diagnostics.
