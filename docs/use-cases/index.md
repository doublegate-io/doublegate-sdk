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

For connection setup see [Python quickstart](../quickstart.md), and for agent-host
tools without Python see [MCP integration](../agents.md).
