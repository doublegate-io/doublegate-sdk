# Sign-in, roles and keys

Sign in with your provider; give a program a key. A service configures one
`[auth]` block, builds an `Authenticator` from it and asks, on every request, who
is calling; nothing else in a service reads a token, hashes a key or names a role
(ADR-0082, cross-cutting 11 AUTH-1 to AUTH-6).

The base module is standard library only. Verifying a bearer JWT needs the
`identity` extra (`PyJWT[crypto]`), imported lazily; without it a service still
authenticates API keys and socket peers and refuses every JWT with
`AuthError('unsupported')`, which its doctor line reports.

## The configuration block

The same block on every service, parsed with `AuthConfig.from_mapping`, which
refuses an unknown key by name:

```toml
[auth]
issuer = "local"                     # or "https://accounts.google.com", "https://login.microsoftonline.com/<tenant>/v2.0", …
client_id = "doublegate-console"     # the console's client id at that issuer
admins = []                          # subjects or emails that are admin on this service from the first request
trusted_issuers = []                 # [{issuer = "https://token.actions.githubusercontent.com", audience = "…"}] for workloads
roles_claim = ""                     # a claim carrying groups or roles, mapped below; empty = ignored
issuer_url = ""                      # the URL the built-in issuer names itself by, when issuer = "local"
[auth.role_map]                      # "group or role value" = "reader|agent|reviewer|admin"
```

`issuer = "local"` means the client gate's built-in issuer; the service fills
`issuer_url` (the `AuthConfig.local_issuer_url` field) with that gate's address.
Any other value is the OIDC issuer URL, discovered through
`<issuer>/.well-known/openid-configuration`.

## The call

```python
from doublegate_sdk.auth import AuthConfig, AuthError, Authenticator

auth = Authenticator(
    AuthConfig.from_mapping(settings['auth']),
    key_lookup=keys.get,                 # sha256 hex -> KeyRow | None, folded with fold_keys
    role_lookup=people.role,             # (iss, sub) -> role | None, from role-assigned events
    owner_uid=os.getuid(),
)

try:
    who = auth.principal(request.headers.get('Authorization'), socket_peer_uid=peer_uid)
except AuthError as refusal:
    return unauthorized(refusal.kind)    # never the token, never the issuer's text
```

Resolution is first match wins (AUTH-4):

1. A Unix-socket peer is `local`: the daemon owner's uid is `admin`, any other
   uid is `agent`. Identity stays what the socket says.
2. A bearer starting with `dgk_` is looked up by `sha256(key)`; an unknown,
   revoked or expired key refuses identically. The principal is a `program` with
   the key row's role.
3. Any other bearer is a JWT verified against the configured issuer (signature
   from the issuer's JWKS cached by `kid` and refetched once on an unknown `kid`;
   exact `iss`; `aud` containing the client id; `exp` and `nbf` with 60 seconds of
   tolerance; `sub` present; RS256, ES256 or EdDSA, never `none` or HMAC). The
   principal is a `person` whose role is the assigned one, else `admin` when
   `sub` or `email` is in `admins`, else the mapped claim, else `reader`. A token
   from a `trusted_issuers` entry is a workload: a `program` with its assigned
   role or `reader`.
4. No header and no socket is `AuthError('missing')`.

`Principal.as_record()` is the `principal` object a journal event carries;
`Principal.identity` is the `identity` string (AUTH-5).

## API keys

`new_api_key()` mints `dgk_` plus 43 base64url characters and returns the key,
its `sha256` hex and the `key_id` (the first 12 hex characters, what people see).
The service stores the hash as an `apikey-issued` event and folds the journal
with `fold_keys` into `KeyRow`s at start; `apikey-revoked` sets `revoked_at`.
Times in a `KeyRow` are epoch milliseconds, the journal's unit.

## The roles

`reader` < `agent` < `reviewer` < `admin`; `role_allows(have, need)` compares
them. What each role may do is a column in the service's verb catalog, the only
place it is written. An `agent` never holds a curation role: the gate is not
callable by the thing being gated, on any credential, over any transport.

## The errors

Every refusal is an `AuthError(kind, message)`, a `DoublegateError`. `kind` is one
of `missing`, `malformed`, `expired`, `issuer`, `audience`, `signature`,
`unavailable` (the issuer could not be fetched) and `unsupported` (the `identity`
extra is not installed). The message is the SDK's own sentence, never the
token's contents or the issuer's text. A corrupt role table or key journal (a
role outside the four) raises `ValueError`: a programming error, not a refusal.

## Reference

::: doublegate_sdk.auth
