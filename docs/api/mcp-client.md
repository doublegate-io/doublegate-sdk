# Gate client (HTTP/MCP)

This is the third door: `HttpMcpTransport` speaks `POST /mcp`, the tool catalog
an MCP host sees. The gate's `dg.*` verbs — an agent's `KnowledgeClient` and the
operator's `CurationClient` over the Unix socket or `POST /rpc` — are on the
[gate clients](client.md) page. All three raise one `GateError`
(`doublegate_sdk.errors`).

`doublegate_sdk.client` connects an application or agent to a running gate over
the product's public integration surface: `POST /mcp` (ADR-0050). It is an
explicit, optional import using only the Python standard library, and it adds no
connection, endpoint discovery or credential discovery on import **or** on
construction.

It is a transport, not an authority. A read is not an admission and a proposal
is not an approval.

## The narrowed contract — read this first

The client speaks the tools the maintained client tier actually serves, and
nothing else. Two absences matter:

| Not available | Why |
|---|---|
| `inventory` / `inventory_pages` | The client tier serves **no inventory tool**. Rather than ship a method that could only ever fail, there is none. |
| Whole-gate `status()` | `doublegate.status` **requires** an artifact id. There is no gate-wide status call on this surface. |

The served catalog is `doublegate.remember`, `recall`, `status`, `why`, `tip`,
`pending`, `rank`, `hide`, `ban`. Ask a specific server what it serves rather
than assuming:

```python
transport.discover()    # server/discover: protocol versions, serverInfo, capabilities
transport.tool_names()  # tools/list: the tool names this server actually lists
```

A tool outside the SDK's declared catalog raises `ValueError` locally instead of
being sent and blamed on the server.

## Connect

```python
import os
from doublegate_sdk import connect

client = connect('https://gate.example.org/mcp', token=os.environ['GATE_TOKEN'])

record = client.status('sha256:' + '…')
print(record['state'])

answers = client.recall('basalt specimens', limit=5)
for hit in answers['results']:
    print(hit['artifact_id'])
```

`connect()` is the one call most callers need. It builds an `HttpMcpTransport`
and wraps it in a `GateClient`, and takes the same bounds and credential
arguments; assemble the two by hand only when you want to hold the transport
itself, for `discover()` or `tool_names()`:

```python
from doublegate_sdk.client import GateClient, HttpMcpTransport

transport = HttpMcpTransport('https://gate.example.org/mcp', token=token)
client = GateClient(transport)
print(transport.tool_names())
```

`connect()` also exposes the transport as `client.transport`, so the low-level
form is rarely necessary.

HTTPS is the default and the only option for a remote endpoint. Plain HTTP is
refused unless you pass `allow_insecure_loopback=True` **and** the host is
loopback — that combination exists for tests and for a daemon on this host, and
it still refuses a remote host:

```python
connect('http://127.0.0.1:8480/mcp', allow_insecure_loopback=True)
```

The endpoint is always explicit. The SDK reads no configuration file, no
environment variable and no well-known path to find a gate.

## Propose

Writes are off unless you turn them on. The transport itself refuses a
write-annotated tool, so a read-only client cannot be talked into writing by a
caller that reaches past `GateClient`:

```python
writer = connect(endpoint, token=token, allow_writes=True)
proposal = writer.propose(
    'The laboratory labels basalt specimens by collection date.',
    content_type='memory', trust_class='T-4', source_uri='application://observation/123',
)
state = writer.status(proposal['artifact_id'])['state']
```

`content` is text. Bytes are accepted only when they are valid UTF-8, and are
decoded; binary is **refused** rather than base64-smuggled into a field the
service would store as literal base64 text. The service's `remember` schema has
no `encoding` parameter, so there is nothing to smuggle it into honestly.

Writer identity is never sent. The service derives identity from the connection
and refuses a request carrying `writer_identity` or `deployment_id`.

`recall` excludes the caller's own unreviewed pending echoes, so an application
does not read back its own un-admitted proposal and mistake it for an answer.
Provisional knowledge is excluded by default; pass `include_provisional=True`
when your policy permits it. Each hit keeps its own provenance flags, and no
score is relabelled as trust or confidence.

## Bounds and errors

`timeout` (default 10s) is one **total deadline** for the whole exchange, not a
per-socket operation timeout: it covers connect, send, the status line, the
response headers and the body, and is re-checked before every read, so a peer
cannot extend it by answering one byte at a time. It is enforced on the calling
thread — no background worker is started, so nothing can outlive the call.

One thing the deadline does **not** cover: **name resolution**. The standard
library resolves the host inside `connect()` with no cancellation hook, so a
hanging DNS server is bounded by your resolver's own timeout, not by this. Pass
an IP literal if that matters to you.

Responses are capped at 1 MiB (`max_response_bytes`) and requests at 1 MiB
(`max_request_bytes`) — separate budgets, so lowering one does not silently trip
the other. A 200 whose `Content-Type` is not `application/json` (or a `+json`
type) is refused as `invalid_response`, even when its body would have parsed: a
captive portal or proxy interstitial is not this gate's answer.

The endpoint itself is validated at construction, so a local configuration
mistake is a `ValueError` you get immediately rather than a `GateError` that
looks like the gate being down:

- **credentials in the URL are refused.** `https://user:pw@gate.example/mcp`
  raises — `urlsplit` strips userinfo, so such a credential is never sent, and
  keeping it would only leave it in `transport.endpoint` waiting to be logged.
  Pass `token=` instead. An empty username (`https://@gate.example/mcp`) is still
  userinfo and is still refused.
- **control characters and whitespace in the path are refused**, as are tab, CR
  and LF anywhere in the URL — `urlsplit` deletes those silently, which would
  otherwise turn `/mcp\tx` into `/mcpx` behind your back.
- **a token the HTTP header encoding cannot carry is refused at construction.**
  Headers are Latin-1; a token with CJK, emoji or Cyrillic raises `ValueError`
  when you build the transport rather than `UnicodeEncodeError` mid-request.

Nothing is retried. A write repeated without an idempotency contract is worse
than a write that failed loudly. Redirects are refused, never followed: following
one would carry the bearer token to whatever host the response names.

`GateError.kind` is a stable string; branch on it rather than parsing English.

| `kind` | Meaning |
|---|---|
| `unavailable` | The endpoint could not be reached. |
| `timeout` | The total deadline expired (connect, send, headers or body). |
| `unauthorized` / `forbidden` | HTTP 401 / 403 from the serving surface. |
| `redirect_refused` | The server answered a redirect; it was not followed. |
| `unsupported_operation` | JSON-RPC `-32601`/`-32602`, or HTTP 405 — this build does not serve that. |
| `remote_error` | The server refused; the numeric `code` is kept. |
| `tool_error` | The tool answered with `isError`. |
| `invalid_response` | The response did not satisfy the checked shape. |
| `request_too_large` / `response_too_large` | A byte budget was exceeded. |
| `writes_disabled` | A write tool was called without `allow_writes=True`. |

Remote error *text* is withheld deliberately; the numeric `code` is kept. Server
messages carry internals, and a client log is the wrong place for them. This
version does not claim a universal authorization taxonomy — map server codes
explicitly in your application.

`GateError.outcome_unknown` is true when a write may have been received but the
outcome could not be read (timeout after sending, a truncated or malformed
response, a server `-32603`). Reconcile before resubmitting; the service's
content-duplicate detection is not an exactly-once or idempotency-key guarantee.

## What the MCP support actually is

The server serves the 2026-07-28 **stateless JSON request/response subset** of
Streamable HTTP, and so does this client:

- one JSON-RPC message per `POST /mcp`, one JSON object back;
- **no SSE**, no streaming — `GET /mcp` is a 405 by design;
- **no sessions** — the server issues no `Mcp-Session-Id` and the client sends
  none;
- negotiation is `server/discover` and `tools/list`. `initialize` is refused by
  the server unless an operator enables a legacy flag, so the SDK does not send
  it.

This is not a general MCP framework and does not claim to be one.
`describe_client()['mcp']` reports these facts, and a test asserts the SDK never
claims streaming or session support.

## Explicit transport injection

`GateClient` accepts any `McpTransport` implementing
`call(tool, arguments) -> dict`. A host application can supply its own supported
transport — an MCP client it already runs, for instance — without the SDK
importing an agent framework. A custom injected transport owns its own
write-enablement contract; none of these client-side flags grant server
authority.

## Verified scope

Proved by the scripted-responder suite (`tests/test_memory_client.py`, real
loopback HTTP servers, real bytes — not mock transport objects): tool mapping,
bearer header presence and absence, token never appearing in an error, response
and request bounds as separate budgets, redirect refusal, 401/403/405 mapping,
JSON-RPC error-code mapping, write opt-in at both client and transport layers,
UTF-8 enforcement, unknown-outcome flagging, and the absence of an `inventory`
method. The suite also asserts that no socket transport is re-exported, so a
removed transport cannot quietly return.

Proved by one isolated real-service run (`examples/verify_memory_lifecycle.py`,
against the product's own console `POST /mcp` on an ephemeral loopback port):
`server/discover` and `tools/list` answered by the real server, a propose →
status → recall cycle with the record's state coming from the service, own
pending excluded from recall, provisional requiring opt-in, a read-only client
refusing a write, and the served catalog containing **no** inventory tool.

### Not proved, and why

!!! warning "The MCP endpoint on the maintained client tier is keyless"
    `console.py` dispatches `POST /mcp` **before** its `_authed()` check; only
    `/api/*` routes enforce the console bearer token. On that tier, loopback is
    the credential (INV-SEC-11 / FR-65).

    So the real-service run proves the *functional* path over MCP, and proves the
    product's *actual* token guard separately on `/api/status` (401 without a
    token, 200 with). It does **not** prove an authenticated remote MCP client.
    The SDK's own 401/403 handling is proved against scripted responders. No
    server-side change was made to close this; it is the service's to close.

Also not covered: HTTPS against a real certificate chain, a remote (non-loopback)
deployment, an org-tier keyed MCP surface, cross-tenant authorization,
installed-wheel or Windows execution, and organization delivery. This is
source-mode verification on Linux/WSL.

## Generated reference

::: doublegate_sdk.client
