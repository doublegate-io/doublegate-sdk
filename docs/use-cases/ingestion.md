# Submit a document you already have

You have a local text file — meeting notes, a runbook, an exported record — and
you want it in the gate. This is the smallest honest version of that: read one
file as UTF-8, submit it with a source URI that says where it really came from,
and report what the gate answered.

!!! warning "This is not a crawler, and not a binary pipeline"
    There is no fetching, no queue, no scheduler and no retry loop here, because
    the SDK publishes none of those and a starter that faked them would teach you
    the wrong shape. The client surface stores **text**: `propose` accepts `str`,
    and accepts `bytes` only when they decode as UTF-8. A PDF, an image or a zip
    is refused, not base64-smuggled into a field the service would store verbatim.
    If you need binary ingestion, that is service work that does not exist yet.

## Before you start

A running gate's MCP URL and whatever credential its operator issued. All commands
run **from the repository root**.

```console
$ export DOUBLEGATE_ENDPOINT="https://gate.your-deployment.example/mcp"
$ export DOUBLEGATE_TOKEN="…"          # only if your deployment requires one
```

The credential is read from `DOUBLEGATE_TOKEN` only. There is no `--token` flag,
and the value is never printed.

!!! note "Which interpreter, and which source"
    `python` below means the interpreter of the environment where you installed
    this SDK. From a source checkout, use that checkout's interpreter (for example
    `.venv/bin/python`) so the `doublegate_sdk` you import is the revision you are
    reading. Confirm with `python -c "import doublegate_sdk;
    print(doublegate_sdk.__file__)"`.

## Submit one document

```console
$ python examples/starters/ingestion.py submit ./notes/specimen-labelling.md
```

This writes. It opens the client with `allow_writes=True`; `pending` opens a
read-only client.

The app prints the source URI it derived, the character count it sent, then the
`artifact_id` and `state` the gate returned — plus `findings_count` and
`duplicate` when the gate includes them. Those two come from the service's own
answer; the app does not synthesize them, and a build that omits them simply does
not print them.

The gate scanned the content before answering. A findings count is what the writer
learns; finding *text* is for the operator's review, not for you.

### The source URI

By default the app uses the file's own resolved location as a `file://` URI, so
the record points at something real. Override it when the file is a local copy of
something with a better identity:

```console
$ python examples/starters/ingestion.py submit ./export.txt \
    --source-uri "https://wiki.internal.example/pages/specimen-labelling"
```

Supply a URI that is true. It is the provenance a later reader will rely on.

## What gets refused before anything is sent

Four conditions stop the app locally, without opening a connection at all:

| Condition | Why it is local |
|---|---|
| file missing or unreadable | nothing to send |
| not valid UTF-8 | this surface stores no binary |
| empty or whitespace-only | `propose` requires nonempty content |
| larger than `MAX_DOCUMENT_BYTES` (512,000) | keeps the error specific instead of arriving as `request_too_large` |

Real output from this app:

```console
$ python examples/starters/ingestion.py submit /tmp/bin.dat
document is not valid UTF-8 text; this surface stores no binary
```

Exit is `1`, and no request was built. The transport has its own request bound as
well; the local check exists so you get a message that names the actual problem.

## See what is waiting

```console
$ python examples/starters/ingestion.py pending --limit 20
```

Metadata only — ids, types, states, ages, finding counts. Never content, not even
your own. If you need the body of something you submitted, you already have it:
it is the file you sent.

## Failures, and the one you must not retry

| Exit | Meaning |
|---|---|
| `0` | the call completed |
| `1` | a local refusal: no endpoint, or an unusable document |
| `2` | argparse usage error |
| `3` | the gate call failed; `kind` names the category |
| `4` | a write whose outcome is unknown |

Exit `4` matters most for ingestion, because the natural reflex on a timeout is to
run the command again — and that is how you get the same document filed twice. The
SDK does not retry writes and neither does this app. Check `pending` first, then
decide. The app's message says exactly that.

## Doing it in your own code

A connector is a loop over files, and the loop belongs in your code, not in a
CLI you shell out to. The SDK part is one call.

```python
import os
from pathlib import Path
from doublegate_sdk import connect
from doublegate_sdk.client import GateError

writer = connect("https://gate.your-deployment.example/mcp",
                 token=os.environ.get("DOUBLEGATE_TOKEN"),
                 allow_writes=True, timeout=15.0)

path = Path("notes/specimen-labelling.md").resolve()
answer = writer.propose(path.read_text(encoding="utf-8"),
                        content_type="memory",
                        source_uri=path.as_uri())
```

`propose` guarantees `artifact_id` and `state` in the response; the SDK raises
`invalid_response` without them. A build may also send `findings_count` and
`duplicate` — read those with `.get`, because a build that omits them sent
nothing, and a default would be an invention:

```python
answer["artifact_id"]          # the only durable reference to this submission
answer["state"]                # the gate's answer to the submission, not admission
answer.get("findings_count")   # None when this build does not send it
answer.get("duplicate")
```

The gate scans the content before answering. A findings *count* is what the
writer learns; finding text is for the operator's review.

**What next.** Record `artifact_id` against the file you sent, so a later run
can ask `status` instead of submitting the same document again.

### Refuse unusable input before you send it

`propose` accepts `str`, and accepts `bytes` only when they decode as UTF-8 —
it raises `ValueError` otherwise. Doing the check yourself first gets you a
message that names the real problem rather than a `request_too_large` from the
transport:

```python
raw = path.read_bytes()
if len(raw) > 512_000:
    raise ValueError(f"document is {len(raw)} bytes; too large to submit whole")
try:
    text = raw.decode("utf-8")
except UnicodeDecodeError:
    raise ValueError("document is not valid UTF-8 text; this surface stores no binary")
if not text.strip():
    raise ValueError("document is empty or whitespace-only; propose requires content")
```

### The importable recipe

[`examples/recipes/gate_operations.py`](https://github.com/doublegate-io/doublegate-sdk/blob/main/examples/recipes/gate_operations.py)
has that as one function. It takes the client you built — it never calls
`connect` — and raises `ValueError` locally, before any request is built, for
all three refusals:

```python
from recipes.gate_operations import observe_file, waiting_for_review

observed = observe_file(writer, "notes/specimen-labelling.md")
observed.artifact_id, observed.state
```

`source_uri` defaults to the file's own resolved `file://` URI, so the record
points at something real. Override it when the file is a local copy of something
with a better identity:

```python
observed = observe_file(writer, "export.txt",
                        source_uri="https://wiki.internal.example/pages/specimen-labelling")
```

Supply a URI that is true. It is the provenance a later reader will rely on.

### The failure you must not retry

```python
try:
    observed = observe_file(writer, path)
except GateError as error:
    if error.outcome_unknown:
        # The write may already have landed. Check what is waiting first.
        waiting = waiting_for_review(reader, limit=100)
        ...    # decide from `waiting`; do not resend
    else:
        ...    # branch on error.kind
except ValueError:
    ...        # a local refusal: nothing was sent
```

This is the exit-`4` case from the table above, in Python. The reflex on a
timeout is to run it again, and that is how the same document gets filed twice.
Neither the SDK nor these recipes retry a write; `waiting_for_review` returns
the `pending` metadata list — ids, types, states, ages, finding counts, never
content — which is what you reconcile against.

The `reader` above is a second client built with the default
`allow_writes=False`, so the reconciliation path cannot write:

```python
reader = connect("https://gate.your-deployment.example/mcp",
                 token=os.environ.get("DOUBLEGATE_TOKEN"), timeout=15.0)
```

## What this does not establish

Sending `DOUBLEGATE_TOKEN` means the SDK sets a bearer header. It does not mean
the receiving endpoint verifies it.

!!! warning "Current authentication gap"
    On the maintained client tier, `POST /mcp` is dispatched before the console's
    authentication check; only `/api/*` enforces the console bearer token, and
    loopback is the credential on that tier. A successful local run proves the
    protocol path, not an authenticated remote one. This is the service's to
    close. See [client reference](../api/client.md) and the
    [roadmap](../roadmap.md).

For a gate on this host, `--allow-insecure-loopback` permits plain `http://`; it
refuses any non-loopback host.

## Completion status

App and tests are complete and pass. The tests are **unit tests** with an injected
client double: they prove the document is read and refused locally where it should
be, that the `file://` source URI is derived from the real path, that `submit`
writes and `pending` does not, and that an unknown write outcome is reported
without a retry. No socket is opened. Real-service acceptance is recorded
separately in [the client reference](../api/client.md) and was not re-run here.
