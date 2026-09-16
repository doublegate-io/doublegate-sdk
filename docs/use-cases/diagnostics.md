# Work out why the gate is not answering

An integration that worked yesterday is failing, and you need to know whether the
endpoint is wrong, the gate is down, the credential was rejected, or the call is
simply not served by that build. The starter app negotiates with the endpoint the
way the SDK does and maps whatever failed onto a fixed error kind with a fixed next
step.

!!! warning "There is no instrumentation behind this"
    This app collects no metrics, emits no traces, reads no server logs and hooks
    nothing inside the SDK. It reports the results of calls it makes itself, in
    this process, and nothing else. If you came looking for SDK telemetry, that is
    not what this is — building a probe that *looked* like observability would
    misrepresent what the SDK publishes.

## Before you start

An endpoint you already have. All commands run **from the repository root**.

```console
$ export DOUBLEGATE_ENDPOINT="https://gate.your-deployment.example/mcp"
$ export DOUBLEGATE_TOKEN="…"          # only if your deployment requires one
```

The credential is read from `DOUBLEGATE_TOKEN` only — never an argument, never
printed. The app reports it as `token supplied: True` or `False` and nothing more.
That matters here specifically: a diagnostic tool is exactly the thing people paste
into a ticket.

!!! note "Which interpreter, and which source"
    `python` means the interpreter of the environment where you installed this SDK.
    From a source checkout, use that checkout's interpreter (for example
    `.venv/bin/python`) so the `doublegate_sdk` you import is the revision you are
    reading. Confirm with `python -c "import doublegate_sdk;
    print(doublegate_sdk.__file__)"`.

## Probe the endpoint

```console
$ python examples/starters/diagnostics.py probe
```

Everything is a read; the client is opened with `allow_writes=False`. Two steps run
by default — `server/discover` and `tools/list` — because those are the negotiation
methods the maintained server answers. The SDK does not send `initialize`: that is
refused unless an operator turned on a legacy flag.

`tools:` is the catalog **that server actually lists**, in its own order. Compare
it against the call you were trying to make; a missing tool explains an
`unsupported_operation` without any further digging.

Add reads when you want to test past negotiation:

```console
$ python examples/starters/diagnostics.py probe --query "specimen labelling" --limit 1
$ python examples/starters/diagnostics.py probe --artifact-id sha256:…
```

The app prints `failed steps: N` and exits `3` if any step failed, so this drops
into a health check without output parsing.

## A real failure transcript

Against a port with nothing listening:

```console
$ python examples/starters/diagnostics.py --allow-insecure-loopback \
    --endpoint http://127.0.0.1:44995/mcp probe
discover: gate_error kind=unavailable code=None
discover: next step — no usable connection to that host and port; confirm the gate is running and the endpoint is the MCP URL, not the daemon administration URL
tools: gate_error kind=unavailable code=None
tools: next step — no usable connection to that host and port; confirm the gate is running and the endpoint is the MCP URL, not the daemon administration URL
endpoint: http://127.0.0.1:44995/mcp
token supplied: False
failed steps: 2
exit=3
```

That is actual output, not an illustration.

## What each kind means, and what to do

The app maps the SDK's own error kinds. The next-step text is fixed for each kind —
literal strings chosen in advance, never assembled from a response:

| `kind` | What it tells you |
|---|---|
| `unavailable` | no usable connection; wrong host/port, or the gate is not running |
| `timeout` | the deadline passed with no complete answer |
| `unauthorized` (401) | the endpoint rejected the credential |
| `forbidden` (403) | credential accepted, this call not permitted |
| `redirect_refused` | the endpoint redirected; the SDK will not carry your token to another host |
| `unsupported_operation` | this build does not serve that call, or the id is unknown |
| `invalid_response` | the answer was not one this client will guess at — often a URL that is not a gate |
| `remote_error` | the gate failed and did not explain it to clients |
| `request_too_large` / `response_too_large` | a configured bound was exceeded |
| `writes_disabled` | a write was attempted on a read-only client |
| `tool_error` | the gate ran the call and reported a failure |

Two of those are worth calling out. `unavailable` is very often the
**administration URL used in place of the MCP URL** — both are HTTP, and they serve
different operations. And `invalid_response` usually means the URL reaches
something that is not a gate at all.

An error kind this app does not recognise prints `unrecognized error kind for this
SDK revision` and asks you to report the kind. It does not pick the nearest
familiar message.

### No server text, ever

The app prints `kind` and `code`, never the server's message. That is the SDK's
deliberate design — remote error text carries internals and a client log is the
wrong place for it — and this app does not work around it. Callers branch on
`kind`; nobody parses English.

## Exit statuses

| Exit | Meaning |
|---|---|
| `0` | every step attempted succeeded |
| `1` | a local refusal: no endpoint given |
| `2` | argparse usage error |
| `3` | at least one step failed; each is reported with its next step |

## Doing it in your own code

A health check inside your own service does not want a subprocess. The two
negotiation reads are available directly on the transport:

```python
import os
from doublegate_sdk import connect
from doublegate_sdk.client import GateError

reader = connect("https://gate.your-deployment.example/mcp",
                 token=os.environ.get("DOUBLEGATE_TOKEN"),
                 timeout=15.0)          # allow_writes defaults to False

catalog = reader.transport.tool_names()     # the tools THIS server lists, its own order
info = reader.transport.discover()          # protocol versions, server info, capabilities
```

`discover` and `tool_names` live on `HttpMcpTransport`, reached through the
client's `transport` property — they are protocol negotiation, not tool calls,
which is why they are not methods on `GateClient`. `tool_names` returns a tuple
of names and raises `invalid_response` if the server's list is not well-formed.
`discover` returns the server's `server/discover` result as-is.

Both are reads. Neither depends on `allow_writes`, and neither sends
`initialize` — the maintained server refuses that unless an operator turned on a
legacy flag.

**What next.** Compare `catalog` against the call that was failing. A tool
missing from that tuple explains an `unsupported_operation` with no further
digging; a tool that *is* listed points the investigation at the call's
arguments or the artifact id instead.

### What the SDK's own description says, offline

```python
from doublegate_sdk.client import describe_client

description = describe_client()
description["operations"]               # this SDK's calls and the tool behind each
description["mcp"]["tools"]             # the tool catalog this client tier knows
description["unsupported_operations"]   # e.g. inventory, whole-gate status
description["automatic_retries"]        # False
description["follows_redirects"]        # False
```

This opens nothing and contacts nothing. It describes **this SDK's contract**,
not a remote gate's capabilities — which is the distinction its `scope` member
states explicitly (`'sdk-contract-not-server-capabilities'`). Use it to answer
"can this client even make that call", and `tool_names()` to answer "does that
deployment serve it".

### The importable recipe

```python
from recipes.gate_operations import server_catalog, call_or_failure

catalog, failure = call_or_failure(server_catalog, reader)
if failure is None:
    catalog["tools"]        # list of tool names, the server's own order
    catalog["discover"]     # the server/discover result
else:
    failure.kind, failure.code, failure.endpoint_reached
```

`server_catalog` performs both negotiation reads. It raises `TypeError` if you
hand it a client over a caller-supplied transport that offers neither — the
`McpTransport` protocol declares only `call`, so negotiation is not something
every transport has, and reporting its absence as a gate failure would be wrong.

### Mapping the failure

```python
NOT_REACHED = {"unavailable", "timeout", "redirect_refused"}

try:
    reader.transport.discover()
except GateError as error:
    reached = error.kind not in NOT_REACHED   # an `unauthorized` IS the gate answering
```

`GateCallFailure.endpoint_reached` in the recipe module is exactly that
comparison. The distinction matters in a diagnostic: "the gate refused you" and
"nothing answered on that port" lead to different next steps, and both are
failures.

The kind-to-next-step table above is the mapping to reuse. Keep the next-step
text fixed per kind, as the starter does — never assembled from a response,
because the SDK withholds server error text on purpose.

## What a clean probe does not prove

A successful probe means the endpoint speaks the protocol and answered. It does not
mean your credential was verified.

!!! warning "Current authentication gap"
    On the maintained client tier, `POST /mcp` is dispatched **before** the
    console's authentication check; only `/api/*` routes enforce the console bearer
    token, and loopback is the credential on that tier. So a probe that succeeds
    without a token is not evidence of a misconfiguration you can fix from here —
    on that tier it is expected. Equally, a probe that succeeds *with* a token does
    not show the token was checked. The SDK's own 401/403 handling is proved
    against scripted responders. Closing the server-side gap is the service's work.
    See [client reference](../api/mcp-client.md) and the [roadmap](../roadmap.md).

For a gate on this host, `--allow-insecure-loopback` permits plain `http://`; it
refuses any non-loopback host, so it cannot send a credential unencrypted to a
remote endpoint.

## Completion status

App and tests are complete and pass. The tests are **unit tests** with an injected
client double: they prove the probe reads only, makes no reads unless asked, maps
each error kind to its fixed next step, reports an unknown kind without guessing,
and reports only the *presence* of a credential. They open no socket. The failure
transcript above was produced by running the app against a genuinely closed local
port. Real-service acceptance for this SDK is recorded separately in
[the client reference](../api/mcp-client.md) and was not re-run for these starters.
