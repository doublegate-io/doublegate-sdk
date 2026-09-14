# Remember something now, look it up later

Your application observes something worth keeping — a decision, a measurement, a
label convention — and needs it again in a later run, possibly a later day, in a
different process. This is the case the starter app covers: one write, one handle,
and a second session that reads the gate's current answer for that handle.

The thing that makes this work across sessions is the **artifact id**. It is the
only durable reference to a submission. The starter writes it to a small local
JSON file so the second invocation has something to ask about; your application
would put it wherever it already keeps state.

## Before you start

You need a gate you already have: a running deployment's MCP URL, and whatever
credential its operator issued you. Nothing here starts a gate, and there is no
substitute server to fall back to — if the endpoint is not real, the app reports
that it could not connect and stops.

All commands below run **from the repository root**.

```console
$ export DOUBLEGATE_ENDPOINT="https://gate.your-deployment.example/mcp"
$ export DOUBLEGATE_TOKEN="…"          # only if your deployment requires one
```

The credential is read from `DOUBLEGATE_TOKEN` and nowhere else. There is
deliberately no `--token` flag: an argument lands in shell history, in `ps`, and
in CI logs. The app never prints the value either.

!!! note "Which interpreter, and which source"
    These commands use `python` as shorthand for the interpreter of the
    environment where you installed this SDK. If you are working from a source
    checkout rather than an installed distribution, run them with that checkout's
    interpreter (for example `.venv/bin/python`) so the `doublegate_sdk` being
    imported is the revision you are reading. `python -c "import doublegate_sdk;
    print(doublegate_sdk.__file__)"` tells you which one you actually got.

## Session one: remember

```console
$ python examples/starters/memory.py remember \
    "The laboratory labels basalt specimens by collection date." \
    --source-uri "application://laboratory/observation/123"
```

This is the only subcommand that writes. It opens the client with
`allow_writes=True`; the other two open a read-only client, so they cannot write
even by mistake.

On success you get the artifact id, the state the gate answered with, and the path
where the handle was saved. The state is the gate's response *to the submission* —
it is not admission. A successful write means the gate accepted and scanned the
content, not that anyone has reviewed it.

## Session two: check where it stands

Run this later, from anywhere, as a separate process. It reads the handle file and
asks the gate:

```console
$ python examples/starters/memory.py check
```

It prints the state recorded at submission time next to the state now, so a change
is visible rather than implied. If the file is missing or does not contain an
artifact id this app wrote, it refuses before opening any connection:

```console
$ python examples/starters/memory.py --state-file /tmp/nope-handle.json check
no usable artifact handle in /tmp/nope-handle.json; run `remember` first
```

That transcript is real output from this app.

## Reading knowledge back

```console
$ python examples/starters/memory.py recall "How are basalt specimens labelled?"
```

Expect an empty result right after your own write, and do not read that as
failure. The SDK asks for recall with your own unreviewed submissions excluded, so
you cannot read back your own pending proposal and mistake it for served
knowledge. The app says so explicitly when the list is empty rather than printing
nothing.

## Failures, and the one you must not retry

The app catches `GateError` and prints `kind` and `code` — never the server's own
message, which the SDK withholds on purpose because those carry internals. Exit
codes let a shell caller branch:

| Exit | Meaning |
|---|---|
| `0` | the call completed |
| `1` | a local refusal: no endpoint, no usable handle file |
| `2` | argparse usage error |
| `3` | the gate call failed; `kind` names the category |
| `4` | a write whose outcome is unknown |

Exit `4` is the important one. When `outcome_unknown` is set on a write, the
request may already have landed. The SDK never retries writes, and neither does
this app — it tells you to reconcile with the gate first. Re-running `remember`
after a `4` risks a second submission of the same observation.

The SDK is deliberately conservative about this: once it has started sending a
write, a connection failure is reported as `outcome_unknown` rather than as a
clean failure, because it cannot tell how far the request got. So a failed
`remember` will often exit `4` where a failed read of the same endpoint exits `3`.
That asymmetry is intentional, not a bug in the app.

## What this does not establish

The app talks to whatever endpoint you name, and supplying `DOUBLEGATE_TOKEN`
means the SDK sends a bearer header — it does not mean the receiver checks it.

!!! warning "Current authentication gap"
    On the maintained client tier, `POST /mcp` is dispatched **before** the
    console's authentication check; only `/api/*` routes enforce the console
    bearer token. On that tier loopback is the credential. So a working run
    against a local gate proves the protocol path, not an authenticated remote
    one. Closing that is the service's work, not the SDK's. See
    [client reference](../api/client.md) and the [roadmap](../roadmap.md).

For a local development gate on this host, pass `--allow-insecure-loopback` to
permit plain `http://`. It refuses any non-loopback host, so it cannot be used to
send a credential unencrypted to a remote endpoint.

## Completion status

The starter app and its behaviour tests are complete and pass. Those tests are
**unit tests**: they inject a recording double in place of `connect`, and prove
which operation each subcommand calls, with which write permission, and that the
credential comes from the environment. They open no socket and are not evidence
about any deployment. Real-service acceptance for this SDK is recorded separately
in [the client reference](../api/client.md); it was not re-run for these starters.
