# Submission contract

The SDK accepts one closed whole-event profile, **`doublegate.submission/1`**.
There is no `submission_version: 2` shell, legacy fallback or version negotiation.
`decode_submission` returns original **bytes**, not an authorization result.

## Event identity and signing

`SubmissionEvent.from_mapping(document)` freezes the complete eleven-member
object: `contract`, `envelope`, `content_digest`, `evidence_manifest_digest`,
`tenant_id`, `team_id`, `principal`, `membership`, `submitted_at`, `review_refs`,
and `attribution_chain`. Unknown or missing members reject.

For its restricted JCS canonical bytes `B`:

- `artifact_id = "sha256:" + SHA256(B).hexdigest()` identifies the whole event.
- Detached Ed25519 signs `b"doublegate.submission.v1\0" + B`.
- The event's own ID and detached signature are outside `B`.
- `content_digest` is prefixed SHA-256 of raw content bytes.
- `Envelope.envelope_digest` is the bare SHA-256 of the frozen envelope's canonical
  bytes. Both phase payloads use `envelope_digest`, **never the future event ID**.
  The envelope field registry and omission rules are unchanged; no old-name alias exists.

`sign_submission` accepts a caller-held raw Ed25519 seed. It does not generate or
enroll keys. `verify_submission_signature` verifies cryptography and the expected
event ID only: it does not verify phase signatures, signer purpose/principal
relationships, historical records, selected scope, reviews or current authority.
Use the separately enrolled `verify_attribution` API for each phase; that API also
returns evidence, not authorization.

## Encoding a complete submission

```python
from doublegate_sdk.submission import SubmissionEvent, encode_submission, decode_submission


def prepare_submission(blob, document, *, signature, space, local_verdicts,
                       local_promotion, production_evidence_manifest,
                       max_content_bytes):
    event = SubmissionEvent.from_mapping(document)
    wire = encode_submission(
        blob, event.to_dict(), signature=signature, space=space,
        local_verdicts=local_verdicts, local_promotion=local_promotion,
        production_evidence_manifest=production_evidence_manifest,
        max_content_bytes=max_content_bytes,
    )
    assert decode_submission(wire, event.artifact_id,
                             max_content_bytes=max_content_bytes) == blob
    return wire  # Not proof of authentication or admission.
```

The transport requires `document`, `artifact_id`, `signature`, `content`, `space`,
`local_verdicts`, `local_promotion`, and `production_evidence_manifest`.
Only `encoding` is optional. Local-record container checks do not replace gate-owned
signed-record validation. `space` remains an exact Unicode identifier, not trimmed,
normalized or authorized by parsing.

Inside the event, `attribution_chain` holds the exact production and submission
compact JWS strings. The child references SHA-256 of the root's exact ASCII bytes.
Parsing checks phase order, parent and common envelope/evidence/operation/tenant/
requester/contributor/home-team bindings, final actor/principal equality, and
`production.issued_at <= submission.issued_at <= submitted_at`.
The manifest is exactly `{"manifest_version": 1, "evidence_refs": [...]}` with
nonempty sorted unique lowercase SHA-256 references. Its digest must match the
event and both statements. Existence and authenticity of retained records are
not checked. Structural parsing accepts canonically encoded invalid signatures.

## Byte semantics and bounds

The encoder emits standard canonical RFC 4648 base64: no whitespace, excess
padding or nonzero padding bits. Arbitrary bytes survive unchanged; archives are
not extracted or executed. Absent `encoding` means strict UTF-8 text, without
replacement or normalization. Explicit `"utf-8"` is not supported.

Both functions require caller-chosen nonnegative integer `max_content_bytes`
(booleans reject). There is no SDK default or global 1 MiB policy. Declared,
encoded and decoded lengths are bounded; decoded bytes must match envelope
`content_hash` and `size_bytes`. Zero permits empty content only.
`SubmissionTooLarge` is a `ValueError`; HTTP mapping belongs to the gate.
The outer and optional route/lease `artifact_id` must equal the whole-event ID.

## Integration boundary

Gates must independently verify enrolled event and phase signatures, exact
retained evidence/review/promotion and historical references, exact selected
space/scope and audience, and fresh current authority before effects. Operation
conflicts belong to the gate's event-independent scoped operation journal, not to
SDK hashing. Signature success is neither human intent nor admission.

Reuse the frozen event and signature on retries; token renewal must not rewrite
signed history. This SDK migration alone does not cut over client, Org or crawler
runtimes. Preserve unresolved durable work and receipts rather than rewriting or
deleting them.

Transport fixtures use real deterministic **public test** signatures and inert
local records. The vendored reconciliation vector retains source provenance and
immutable hashes. An independent Node consumer checks actual SDK-produced bytes
and signature, both phase signatures, digest/parent links and tamper refusal.
These tests prove bounded byte/crypto agreement, not enrolled trust or admission.

::: doublegate_sdk.submission

::: doublegate_sdk.envelope
