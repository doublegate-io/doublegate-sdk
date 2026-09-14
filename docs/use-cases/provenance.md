# Ask why an artifact is where it is

Something is in a state you did not expect — still pending, flagged, or not coming
back from recall — and you want the record rather than a guess. `doublegate.why`
is the audit answer for one artifact: every ledger event that moved it, in order,
with signatures.

The starter app reads that and prints **what the service actually returned**. That
distinction is the whole point of this page.

## Before you start

A running gate's MCP URL, whatever credential its operator issued, and an artifact
id — the one `propose` gave you. There is no whole-gate audit call on this surface:
`why` requires an id.

All commands run **from the repository root**.

```console
$ export DOUBLEGATE_ENDPOINT="https://gate.your-deployment.example/mcp"
$ export DOUBLEGATE_TOKEN="…"          # only if your deployment requires one
```

Credential comes from `DOUBLEGATE_TOKEN` only; there is no `--token` flag and the
value is never printed.

!!! note "Which interpreter, and which source"
    `python` means the interpreter of the environment where you installed this SDK.
    From a source checkout, use that checkout's interpreter (for example
    `.venv/bin/python`) so you import the revision you are reading. Confirm with
    `python -c "import doublegate_sdk; print(doublegate_sdk.__file__)"`.

## Read the audit trail

```console
$ python examples/starters/provenance.py why sha256:…
```

Nothing here writes. The client is opened with `allow_writes=False`, so this app
cannot modify anything even if the deployment would permit it.

You get the artifact id as answered, the number of events, the event types in
order, and the keys the events actually carried. Add `--events` to print each
returned event as JSON:

```console
$ python examples/starters/provenance.py why sha256:… --events
```

## The returned schema, not an assumed one

On the maintained client tier, `doublegate.why` returns `artifact_id` and an
ordered `events` list, and each event carries `event_id`, `type`, `identity`,
`ts`, `payload` and `sig`. The audit view keeps everything except the envelope
body — that already lives on `status`.

The SDK validates the parts it will not guess at: `why` raises `invalid_response`
unless the answer is an object with an `events` **list**. Beyond that it hands you
the service's payload.

So the app derives its summary from the payload instead of asserting a shape:

* `event_keys_present` is the union of keys that were actually there.
* `expected_keys_absent` lists any of the six documented keys that at least one
  event did not carry, with a line telling you not to assume a default for them.

A build that sends fewer fields is **reported**, not rejected and not quietly
filled in. `EXPECTED_EVENT_KEYS` in the app is a comparison baseline, not a
requirement — which is why a shorter event list prints an `expected_keys_absent`
line rather than raising.

An empty `events` list prints `event_count: 0` and `event_types: (none)`. It does
not invent a placeholder event.

## When the id is not known

An unknown artifact id comes back as `unsupported_operation`, because the server
answers an unknown-tool *and* an unknown-artifact case with JSON-RPC `-32602`,
which the SDK maps to that kind. The app says so explicitly rather than letting
you conclude the gate does not support `why`:

```
gate_error kind=unsupported_operation code=-32602
the gate answered that it does not serve this call for this id; an unknown artifact id also lands here
```

Both readings are genuinely possible from that code. The app reports the ambiguity
instead of resolving it by guessing.

## Exit statuses

| Exit | Meaning |
|---|---|
| `0` | the audit answer was read |
| `1` | a local refusal: no endpoint given |
| `2` | argparse usage error |
| `3` | the gate call failed; `kind` names the category |

There is no unknown-outcome exit here, because nothing in this app writes.

## What this does not establish

Reading an audit trail is not verifying its signatures. The app prints `sig` values
when they are present; it does not check them. Signature verification is not on
this client surface.

!!! warning "Current authentication gap"
    On the maintained client tier, `POST /mcp` is dispatched before the console's
    authentication check; only `/api/*` enforces the console bearer token, and
    loopback is the credential there. Supplying `DOUBLEGATE_TOKEN` makes the SDK
    send a bearer header; it does not make the receiver verify it. The service owns
    closing this. See [client reference](../api/client.md) and the
    [roadmap](../roadmap.md).

For a gate on this host, `--allow-insecure-loopback` permits plain `http://`; it
refuses any non-loopback host.

## Completion status

App and tests are complete and pass. The tests are **unit tests** with an injected
client double: they prove the app reads only, calls `why` with the id given,
derives its summary from the payload, reports missing event keys as absent, and
handles an empty event list and an unknown id without inventing anything. They open
no socket. Real-service acceptance is recorded separately in
[the client reference](../api/client.md) and was not re-run for these starters.
