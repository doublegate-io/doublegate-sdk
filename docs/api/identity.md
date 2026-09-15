# Identity verification (optional)

Install `doublegate-sdk[identity]`. Base SDK/identity imports remain stdlib-only;
verification lazily loads the already-declared PyJWT/cryptography dependencies.

## Access tokens

```python
from doublegate_sdk.identity import (
    AccessTokenError, IdentityMappingRequest, IssuerProfile,
    ResolvedIdentity, TokenClaims, verify_access_token,
)

# These values come from owner-approved enrollment, NEVER the incoming token.
profile = IssuerProfile(
    issuer="https://issuer.example",
    tenant_id="tenant",
    audience="organization",
    tenant_claim="tid",          # illustrative enrolled claim, not an OIDC convention
    mapping_revision="map-1",
    token_kind="direct",         # or explicitly enrolled "app_only"
)

# verified = verify_access_token(
#     incoming_token, issuer_profile=profile,
#     jwks=trusted_current_jwks, resolver=trusted_identity_resolver,
#     now=receiver_unix_seconds,
# )
```

`verify_access_token(token, *, issuer_profile, jwks, resolver, now) -> TokenClaims`
raises `AccessTokenError` on verification or mapping failure. All profile, mapping
request, resolved identity and result objects are frozen dataclasses.

The trusted resolver receives `IdentityMappingRequest(issuer, tenant_id, subject,
client_id, mapping_revision, token_kind)` only **after** signature and claim checks.
It must resolve that exact tuple against enrolled, current authority, including
whether that subject/client pair is permitted for the selected direct/app-only
interpretation, and return `ResolvedIdentity(requester_id, requester_kind,
actor_id, actor_kind, client_principal_id)`. Unknown mappings must fail, not create
accounts or merge identities by email. The SDK cannot establish that a supplied
resolver or profile was enrolled. That trust boundary belongs to the receiving gate.

### First bounded profile

Implements design 10 §§2–3's selected RFC 9068 RS256 fixture, not arbitrary customer
OIDC interoperability:

- Protected header is exactly `{alg, typ, kid}`, with `alg="RS256"`,
  `typ="at+jwt"` and nonempty `kid`. All other headers reject, including `jku`,
  `x5u`, `jwk`, `crit` and unencoded/detached payload options. Three nonempty,
  canonical unpadded base64url compact segments are required.
- JSON objects reject duplicate keys, including nested duplicates, and non-JSON
  constants. Required identity claims must be nonempty strings. Unknown access
  claims may parse, but are neither passed to the resolver nor returned as authority.
- Required claims: `iss`, `sub`, `aud`, `exp`, `iat`, `jti`, `client_id` and the
  explicitly enrolled tenant claim. Issuer/tenant matching is exact, case-sensitive
  and unnormalized. This bounded profile accepts only one exact string audience;
  audience arrays, even a singleton, reject. Use a separately qualified adapter
  rather than silently broadening the destination contract.
- `exp`, `iat`, optional `nbf`, and receiver `now` are nonnegative integer Unix
  seconds (not booleans, floats, strings or null). Require `0 < exp-iat <= 300`,
  `iat <= now+30`, `nbf <= now+30`, `exp > now-30`, and `nbf < exp` when present.
  Tolerance does not extend the maximum issued lifetime.
- Keys come exclusively from the caller-supplied JWKS snapshot. Exactly one key
  must match `kid`; missing or duplicate matches reject with no fallback. Selected
  keys must be public RSA keys, with `alg=RS256`, `use=sig`, `key_ops=["verify"]`
  if those optional metadata fields are present. Private parameters reject.
  PyJWT supplies key parsing, signature verification and issuer/audience checks;
  there is no bespoke cryptography.
- Direct mapping requires requester=actor with equal `human` or `workload` kind.
  Presenting `client_id` resolves separately; it is not assumed to equal `sub`.
  App-only additionally requires a workload requester/actor equal to the resolved
  presenting client principal. Mapping an app-only token to a human rejects.
- Delegated `act` claims reject (including null). The design does not qualify an
  actual exchange/OBO issuer or prescribe a customer token-kind discriminator.
  No OBO issuer, actor-claim guessing, exchange, or universal `tid` mapping is invented.
  A deployment must only select a profile through trusted enrollment, never from
  token hints; the resolver must validate the subject/client/token-kind binding.

JWKS acquisition, bounded refresh on unknown `kid`, size/time/redirect limits,
rotation overlap deadlines and freshness checks remain caller-owned. The SDK never
fetches a URL, starts a service, caches keys or trusts token-directed networking.
Callers can supply an updated JWKS and retry; no automatic refresh occurs.

### Verification is not authorization

`TokenClaims` contains verified external identifiers, token time/id/destination,
mapping revision and resolved requester/actor/client identities. It has no
`authorized` flag or raw claim bag. Token `groups`, `roles`, `scope`, home-team or
grant claims confer **no** team membership or permission here. No enrolled
membership authority adapter is implemented in this slice.

Gate integration remains pending: current membership, applicable grant binding
(including `jti`/subject/actor/client), every parent grant and revocation, action and
team permission, freshness, human-action proof, replay handling and transactional
rechecks before queue execution/admission/publication must occur outside this SDK.
Token validity is not an admission receipt, grant, durable permission, or evidence
of human presence. There are no gate or admission-status side effects and no
changes to the single submission format.

## Attribution

The separate `verify_attribution` API continues to verify enrolled Ed25519
attribution signatures and request bindings. Its signed historical evidence is
not a replacement for a fresh access token or current gate authority.

### Signed reference payload (attribution v1, submission event)

The only accepted signed payload has `identity_refs` with exactly `membership`,
`mapping`, `relationship`, `policy`, and `attester_enrollment`. Each is a closed
`{revision, digest}` object: an independent nonempty string revision and lowercase
SHA-256 digest of the exact retained record. `authority_ref` is a closed
`{revision, digest}` object whose revision is a positive safe integer.
`grant_refs` is a closed object with required `requester` and `actor` arrays;
each contains 1–8 root-to-leaf `{grant_id, grant_digest}` records, with distinct
IDs within that chain. Both chains remain required when the identities coincide.

The old scalar revisions and flat grant list are rejected, not upgraded or
retried under a second format. Access-token `mapping_revision` above is a
separate enrolled resolver input, not a signed attribution payload field.
The two phases may name different actors, record revisions and attesters.
Gate-owned exact retained lookup and historical/current authorization remain
necessary; signature success alone is not admission or a durable receipt.

::: doublegate_sdk.identity
