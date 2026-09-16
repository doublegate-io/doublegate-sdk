# Published response contracts

`check_response(schema, payload)` consumes the published schema dictionary directly.
The former `Field`/`ResponseSchema` Python DSL has been removed: callers should load
JSON and declare `properties`, `required`, and `additionalProperties: false` rather
than maintain a second description of the wire contract.

```python
import json
from pathlib import Path
from doublegate_sdk.schema import check_response

schema = json.loads(Path("note.schema.json").read_text())
payload = json.loads(Path("note.json").read_text())
violations = check_response(schema, payload)
assert not violations, violations
```

## Bounded support, no runtime dependencies

Manifests and responses use **one standard-library validator**, not a full
Draft 2020-12 implementation. A root schema must be an object with `$schema` equal
to `https://json-schema.org/draft/2020-12/schema`. This identifies the published
schema vocabulary; it does **not** promise general dialect conformance.

Supported assertions:

- `type`: `object`, `array`, `string`, `integer`, `boolean`, `null`, or a nonempty
  list of distinct supported types (for example `["integer", "null"]`).
- `const`, nonempty `enum` (JSON equality, so `false` is not `0`).
- `properties`, `required`, boolean `additionalProperties`.
- A single object schema in `items`, `minItems`, `maxItems`, `uniqueItems`.
- `minLength`, `maxLength`, `pattern` (Python `re.search` syntax, not the full
  JSON Schema regular-expression dialect).
- Inclusive `minimum` and `maximum`.

Bounds are optional. Response strings have no implicit maximum. Properties are
optional unless listed in `required`, and objects are open unless explicitly
closed. `items` may be omitted. Nested empty schema objects accept any JSON value;
boolean schemas and schema-valued `additionalProperties` are not supported.

Allowed annotations are `$schema`, `$id`, `$comment`, `title`, `description`,
`default`, `examples`, `deprecated`, `readOnly`, and `writeOnly`. Defaults are not
inserted and IDs are not fetched. All other keywords—including `$ref`, `$defs`,
composition, conditionals, `format`, and unknown extensions—raise `ValueError`,
even in properties absent from the payload. Malformed schemas also raise
`ValueError`. No unsupported assertion is silently ignored.

Inputs are trusted schema dictionaries and decoded finite JSON payloads; this is
not a sandbox for hostile regexes, recursive Python objects, or enormous inputs.
No network requests, coercion, or mutation occur. Response integers include
integral floats such as `2.0`, never booleans. **Manifest policy is unchanged:**
integers must be actual Python `int` values (`4096.0` and `True` are rejected),
and existing bounded paths/reason codes are preserved.

The result is a sorted tuple of `"/instance/pointer: keyword"` violations, empty
on success. Pointer segments escape `~` and `/`. Missing and extra fields report
the containing object; multiple missing fields may yield duplicate `required`
entries. Diagnostics never include rejected values or unknown field names.
A type failure suppresses secondary assertions at that same node.

## Producer contract evidence

`tests/fixtures/published-note` retains three actual signed-ledger → renderer →
JSONL producer outputs and their published schema/provenance. The generator in
`tests/support` can reproduce the pipeline with the pinned producer installed in
an isolated environment; it is not an SDK dependency. Tests validate the emitted
`data` objects and deliberate invalid mutations. This proves producer/validator
contract compatibility, **not that the gate runtime invokes this SDK checker**.
There is no `check-response` CLI command.

::: doublegate_sdk.schema
