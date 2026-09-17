# Gate clients

`doublegate_sdk` connects to a gate only when you construct a transport and
call a method. Nothing is discovered from the environment; no credential is
read on import; nothing retries on its own. Two clients share one transport
layer and one operation table ([ADR-0074](https://github.com/doublegate-io/design/blob/main/docs/adr/ADR-0074-the-sdks-two-clients.md)):

| client | who | what it can do | what it can never do |
| --- | --- | --- | --- |
| `KnowledgeClient` | an agent, a connector, an application | remember, learn, propose a skill or a bundle, recall, read status/why/tip/relations/approval/comments, annotate, object, resolve, defer, rank or hide *another* writer's row | sign, promote, demote, reject, relate, ban |
| `CurationClient` | a reviewer or an admin (a person signed in, or a program holding a key with that role) | approve, hold, reject, veto, promote, relate/supersede, override the tip, rank, hide, ban, away/back, submit, semantic review, API keys | reach a verb above the role the gate assigned the caller |

The gate is not callable by the thing being gated. The knowledge client's
transport refuses a deciding verb before any I/O; the gate refuses it again
from the identity it derived off the connection (I2). A successful proposal is
a receipt, not an admission: every result carries `proposals_are_admission = False`.

These two clients speak the gate's `dg.*` verbs over its Unix socket or its
`POST /rpc` door. The third door, `POST /mcp`, is the tool catalog an MCP host
sees; `doublegate_sdk.client` ([Gate client (HTTP/MCP)](mcp-client.md)) speaks
it with `HttpMcpTransport`, exposes only the tools the maintained client tier
serves, and raises the same `GateError`.

## Discover the API

`python -m doublegate_sdk describe-client` prints both clients' methods from
their live signatures, the operation table (`rpc`, `mutates`, `role`, `service`,
`scope`), the four roles, the three scopes, the error kinds and the two code
tables. It describes this SDK. `KnowledgeClient.describe()` is what a *running
gate* answers (`dg.describe`), parsed into `Capabilities`.

## Transports and scopes

```python
from doublegate_sdk.transport import UnixSocketTransport, HttpTransport

local = UnixSocketTransport('/explicit/gate/daemon.sock', timeout=5, scope='knowledge')
console = HttpTransport('http://127.0.0.1:8480', bearer=console_token, scope='knowledge')
org = HttpTransport('https://org.example', bearer=admin_key, scope='curation')
```

`scope` is the widest class of verb the transport will emit: `read` (the
default; every write is `writes_disabled`), `knowledge` (an agent's verbs), or
`curation` (the decisions). A verb outside the scope raises
`GateError('forbidden_operation')` before a connection is opened; an unknown
verb is a `ValueError`. Both transports share one deadline for connect, send
and every read (10 s by default), a 1 MiB request cap and a 1 MiB response cap,
duplicate-key rejection and the same code table. `with_timeout(seconds)`
returns a copy for a short liveness probe. A caller-supplied `GateTransport`
(`call(method, params) -> dict`) owns its own allowlist.

`HttpTransport` posts one JSON-RPC message to `{base}/rpc` with a bearer: a
person's OIDC access token, or a `dgk_` API key the gate issued to a program
([ADR-0082](https://github.com/doublegate-io/design/blob/main/docs/adr/ADR-0082-sign-in-with-your-provider-give-a-program-a-key.md)).
A JSON-RPC refusal rides a 200; the door's own refusals are
HTTP statuses (`auth` 401, `scope` 403, `request_too_large` 413, `busy` 429/503
with `Retry-After` kept as `retry_after_ms`). It is not an SSO adapter and it
adds no authorization: the gate still checks every request against the role it
assigned that credential.

## Errors

`GateError.kind` is what you act on. `code` keeps the server's JSON-RPC number;
`retry_after_ms` is set on `busy`; `outcome_unknown` is true when a write may
have been retained although the reply was lost; `detail` carries the server's
message, capped at 512 characters, and never appears in `str(exc)`.

| kind | when |
| --- | --- |
| `unavailable`, `timeout`, `unsupported_transport` | no gate answered in time |
| `invalid_response`, `response_too_large`, `request_too_large` | the frame, not the gate |
| `writes_disabled`, `forbidden_operation`, `page_limit` | refused on this side, before I/O |
| `identity`, `invalid_params`, `unsupported_operation`, `busy`, `banned`, `refused`, `remote_error` | the gate's answer (`-32000`, `-32600/-32602`, `-32601`, `-32006`, `-32009`, `-32010..-32014`, other) |
| `auth`, `scope` | the HTTP `/rpc` door (401, 403) |
| `unauthorized`, `forbidden`, `tool_error`, `redirect_refused` | the HTTP `/mcp` door ([mcp-client](mcp-client.md)) |

The same two tables live in the constellation app's `rpc.ts`; change both or neither.

## The knowledge client

```python
from doublegate_sdk.knowledge import KnowledgeClient

agent = KnowledgeClient(local)
if agent.present():
    caps = agent.describe()                       # Capabilities: verbs, params, content types
    memo = agent.remember('The lab labels basalt by collection date.',
                          content_type='memory', trust_class='T-4', source_uri='agent://field/42')
    fact = agent.learn('Basalt labels are durable.', source_uri='agent://field/42',
                       evidence=[memo.artifact_id], kind='derived_fact')
    agent.annotate(fact.artifact_id, 'derived from the 2026-09 survey', kind='note')
    row = agent.wait_for(fact.artifact_id, {'ACTIVE', 'REJECTED'}, timeout=30)
    hits = agent.recall('basalt', limit=5, include_provisional=True)
```

* `remember` proposes one artifact: bytes travel base64 and unchanged; the
  gate scans before it answers and holds the row (`L1_SCANNED` or
  `L1B_FLAGGED`). No writer identity is ever sent.
* `learn` is a belief distilled from evidence: `kind` is `derived_fact`,
  `summary` or `memory`; `evidence` becomes `derives_from`, which is provenance
  and never hides a parent; the default trust class is `T-5`, a proposal about
  the world. `replaces=<artifact id>` adds that claim to the provenance and
  leaves a note "proposes: supersedes …" on the new row. Only a gate decision
  replaces belief; `supersedes` itself is the operator's relation.
* `propose_skill`, `propose_prompt_template` and `propose_bundle` (a ZIP,
  inspected locally first, then `dg.ingest_bundle` beside its fetch record).
* `recall` always excludes the caller's own unreviewed rows; provisional,
  superseded and hidden rows only on explicit request, each hit keeping its
  flags. No score is relabelled as trust.
* `annotate`, `raise_objection`, `resolve`, `retract_comment` are remarks on
  the record (ADR-0072). Only a *human's* objection holds a promotion; an
  agent's is a remark the reviewer will see.
* `rank` and `hide` weight a row for an audience; the gate refuses them on the
  caller's own rows (I2).
* `inventory` / `inventory_pages` list retained metadata with scope and
  coverage, bounded by `max_pages`; presence never implies admission.

## The curation client

```python
from doublegate_sdk.curation import CurationClient

# the transport carries the caller's own credential; nothing else is minted
reviewer = CurationClient(UnixSocketTransport(sock, scope='curation'))
reviewer = CurationClient(HttpTransport(org, bearer=my_key, scope='curation'))

reviewer.approve(aid, note='matches the survey')        # dg.sign {promote: true}
reviewer.veto(other, 'contradicts the 2025 audit')      # dg.demote
reviewer.supersede(new=aid, old=other)                  # dg.relate supersedes
reviewer.reject(third, 'duplicate', in_favour_of=aid)
```

Authority is the role the gate assigned the credential on the transport
(AUTH-4): every verb this client emits sits on the `reviewer` or the `admin`
row, and a caller the gate reads as an `agent` is refused at the door. The SDK
mints nothing, loads no key and depends on no crypto library for any of it.
`reviewer.knowledge` reads the same gate the agent's way.

## The organization gate's REST doors

`doublegate_sdk.org_rest` moves the submission body that
`doublegate_sdk.submission` encodes: `put_submission` returns a
`SubmissionOutcome` whose `result` is one of `accepted` (202/200), `retry`
(503/429 with the door's delay, or an unexpected status), `stopped` (401/403:
a key problem does not become correct by waiting) or `refused` (400/413/422:
this body, not the key). `pull_outcomes` pages `GET /outcomes` by cursor.
Policy — backoff, cursors, what to do about `retry` — stays with the caller.

## Verified scope

Unit tests drive both transports against a real `AF_UNIX` listener and a
loopback `http.server`: the allowlist before I/O, the shared deadline, byte
caps, malformed and duplicate-key replies, lost-reply outcomes, the code
tables, every client method against a recording transport, and that no curation
call carries an operator proof or fetches a challenge. `examples/verify_knowledge_lifecycle.py` runs a real Client
Gate in-process with the deterministic `StubBackend`: proposal → held →
duplicate → admission → provisional recall → a learning with provenance. It is
not live-model, cross-principal, Windows or organization-delivery evidence.

::: doublegate_sdk.knowledge

::: doublegate_sdk.curation

::: doublegate_sdk.transport

::: doublegate_sdk.capabilities

::: doublegate_sdk.errors

::: doublegate_sdk.operations

::: doublegate_sdk.inventory

::: doublegate_sdk.org_rest
