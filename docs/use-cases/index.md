# Build with the SDK: starter apps

Choose the workflow you need. Each of the seven starters is a small command-line
application with its own guide and tests, not a framework or a substitute gate.

| Build | Starter guide | Needs a gate? | What it demonstrates |
|---|---|---|---|
| A deterministic content check | [Checks](checks.md) | No | Evaluate files and handle clean, flagged and unevaluated results |
| A reviewer integration | [Reviewer preparation](reviewer.md) | No | Offline application contract exercise; no live reviewer binding |
| A correction workflow | [Correction preparation](correction.md) | Optional | Prepare a request; not a completed withdrawal operation |
| Memory across sessions | [Assistant memory](memory.md) | Yes | Propose, save a handle, inspect and recall in another process |
| A document connector | [Ingestion](ingestion.md) | Yes | Submit local UTF-8 content with source attribution |
| An explanation view | [Provenance](provenance.md) | Yes | Inspect the gate's recorded history for an artifact |
| Operational diagnostics | [Diagnostics](diagnostics.md) | Yes | Inspect supported calls and safe error outcomes |

The first two run entirely offline against the installed SDK, so they are the
ones to start with if you do not have an endpoint yet. Correction is offline
unless you pass `--endpoint`.

## CLI starter, or direct Python?

Both, and they are not alternatives. Every guide has a **Doing it in your own
code** section showing the same workflow as plain calls on the SDK's public API,
with the actual response fields and what to do next.

| You want | Use |
|---|---|
| To see the workflow run end to end, now | the starter CLI — `--help` on each |
| To wire it into your own application | the Python section of that guide |
| Importable functions you can call directly | `examples/recipes/` |

The starters stay exactly as they are: they are the runnable reference, and they
own their own argument parsing, exit codes and printing. The Python sections are
what you copy into an application, where argparse and `print` are not what you
want.

## The importable recipes

[`examples/recipes/`](https://github.com/doublegate-io/doublegate-sdk/tree/main/examples/recipes)
holds two small modules of named functions:

| Module | Covers | Needs a gate? |
|---|---|---|
| [`offline_checks.py`](https://github.com/doublegate-io/doublegate-sdk/blob/main/examples/recipes/offline_checks.py) | `evaluate_file`, many files, error kinds as data | No |
| [`gate_operations.py`](https://github.com/doublegate-io/doublegate-sdk/blob/main/examples/recipes/gate_operations.py) | propose, status, why, recall, pending, negotiation | Yes |

Three rules hold across both, and they are what makes them safe to copy:

* **They take the client; they never build one.** No recipe calls `connect`,
  reads an environment variable or holds a connection, so importing one cannot
  open anything and a recipe cannot reach an endpoint you did not hand it.
* **They return values.** Nothing prints, nothing calls `sys.exit`. Rendering is
  yours.
* **They never retry a write.** A write whose outcome is unknown is reported as
  such and handed back — resending it without an idempotency contract is how the
  same observation gets filed twice.

They are examples, not a framework: no base classes, no configuration, no
registry. Copy the function you need into your own code and delete the rest.

## Running the starters

Use the SDK revision containing these apps and an existing compatible Python
environment. Run commands from the repository root. Each guide gives exact
arguments; every app supports `--help`.

Network starters require your real HTTP/MCP endpoint. Credentials come from the
application environment, never command-line token arguments. Read operations do
not enable writes. Submission commands do write observations and are labelled in
the guides. No starter launches a fake gate as a fallback.

## What the examples prove

Offline checks and application-contract exercises run locally. Network operation
unit tests use injected clients and do not prove deployment authorization or model
quality. Run the network starter against your selected gate for service acceptance.
The reviewer and correction starters explicitly stop short of unsupported server
operations; they must not be used as evidence that review or withdrawal occurred.

The recipes are covered by `tests/test_api_recipes.py`, and that suite is split
deliberately. The offline half runs the **real SDK against the real fixture
data** — the outcomes, findings and manifest digest asserted there are what your
installed package produces. The gate half builds a real `GateClient` over a
scripted in-process transport: it exercises the SDK's own response validation on
real dicts, but it opens **no socket** and proves nothing about any deployment —
not authorization, not admission, not a service's field set.

For connection setup see [Python quickstart](../quickstart.md), and for agent-host
tools without Python see [MCP integration](../agents.md).
