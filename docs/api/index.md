# Python API reference

Generated directly from `src/doublegate_sdk` with mkdocstrings. Signatures,
annotations, member navigation and expandable source are built from this snapshot,
not copied by hand. Use the navigation rail to select a module.

| Module | Responsibility |
| --- | --- |
| [Gate clients (socket, `/rpc`)](client.md) | `KnowledgeClient` (an agent: remember, learn, recall, annotate) and `CurationClient` (a reviewer: approve, veto, relate, rank, keys) over one socket/HTTP transport layer, one operation table, one error vocabulary. |
| [Gate client (HTTP/MCP)](mcp-client.md) | Explicit `POST /mcp` tool calls: status/recall/pending reads, opt-in proposals, bounded exchanges; the third door, for hosts that speak the tool catalog. |
| [Check a file](authoring.md) | Apply a manifest to a file in one call; the offline operation the CLI runs. |
| [Manifest](manifest.md) | Export the schema and validate immutable gate packages. |
| [Package loader](package.md) | Read bounded POSIX files and hash validated manifest data. |
| [Evaluation](runtime.md) | Evaluate literal required strings without executing code. |
| [Evidence types](checks.md) | Carry findings and JSON-compatible payloads. |
| [Reason categories](reasons.md) | The five closed values a gate may give a submitter. |
| [Skipped reasons](skipped.md) | The four closed values a reducer may give for skipping an item. |
| [Response schemas](schema.md) | Validate published responses against a bounded Draft 2020-12 subset. |
| [Byte submission](submission.md) | Canonical bytes, the two identities (`content_digest`, `artifact_id`), the signed submission event, the evidence-manifest digest, canonical base64 and decoded-byte integrity; no authority. |
| [Sign-in, roles and keys](auth.md) | The whole of authentication for every service: one `[auth]` block, one `principal()` call, four roles, API keys, OIDC bearer verification behind the `identity` extra. |
| [CLI](cli.md) | Schema, validation, evaluation and client description command entry point. |

Construct packages using `validate_manifest` or `load_package`, not by trusting
arbitrary direct `GatePackage` construction. Dataclass annotations are not runtime
validation. `Finding` is also a container, not a sanitizer: custom callers must
never put raw secrets in its fields. The bundled evaluator emits fixed diagnostics.

## Errors, in one place

Every SDK failure is a `doublegate_sdk.errors.DoublegateError`, so one `except`
catches the SDK without swallowing an application's own `ValueError`s. The wire
error is `GateError`, one class for all three doors: `kind` is what a caller acts
on, `code` the server's JSON-RPC number or HTTP status, `detail` the server's
message capped and kept out of `str(exc)`. The canonicalization exceptions in
`doublegate_sdk.submission` (`CanonicalizationRefused`, `RouteIdMismatch`) are
`ValueError`s, not `DoublegateError`s: catch them explicitly.

## Published without a reference page

One module is importable and used by the starters but has no generated page in
this build. Read it at the source until it does:

- `doublegate_sdk.vocabulary` — the nine closed blocker values and
  `normalise_blockers`.
