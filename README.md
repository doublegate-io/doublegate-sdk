# Doublegate SDK

Connect an application or agent to a Doublegate gate, or author the declarative
manifest a gate applies. **A clean result is not admission.**

Development snapshot **0.1.0.dev5**. No PyPI package or stable release yet.

## Connect to a gate

The [gate client](docs/api/mcp-client.md) speaks the gate's public integration
surface, `POST /mcp`, over HTTPS. The endpoint and token are always explicit;
nothing connects on import or on construction, and writes are off until enabled.

```python
from doublegate_sdk import connect

client = connect('https://gate.example.org/mcp', token=token)
answers = client.recall('basalt specimens', limit=5)
```

The contract is deliberately narrow and matches what the maintained client tier
actually serves: there is **no inventory tool**, and `status` requires an
artifact id. Redirects are never followed and nothing is retried.

Writes are off until you pass `allow_writes=True`. See the
[starter apps](docs/use-cases/index.md) for seven runnable workflows.

## Speak the gate's own verbs

The [gate clients](docs/api/client.md) connect over the gate's Unix socket or its
`POST /rpc` door as an agent (`KnowledgeClient`: remember, learn, recall,
annotate; never decide) or as the operator (`CurationClient`: approve, veto,
relate, rank, keys, each under an operator proof the SDK never mints itself).
One operation table lists every `dg.*` verb with what it mutates and what proof
it needs; one `GateError` covers all three doors. No mandatory dependencies, no
connection on import.

Every gate authenticates through [`doublegate_sdk.auth`](docs/api/auth.md): a person
signs in with the deployment's OpenID Connect provider (or the client gate's built-in
issuer), a program holds a `dgk_` API key, and one call, `Authenticator.principal()`,
answers who is calling and with which of four roles.

```python
from doublegate_sdk.knowledge import KnowledgeClient
from doublegate_sdk.transport import UnixSocketTransport

agent = KnowledgeClient(UnixSocketTransport('/explicit/gate/daemon.sock', scope='knowledge'))
memo = agent.remember('The lab labels basalt by collection date.',
                      content_type='memory', trust_class='T-4', source_uri='agent://field/42')
```

## Author a gate manifest (advanced)

The offline half validates a JSON manifest of literal, case-sensitive required
strings and evaluates content against them, returning structured findings. It
never contacts a gate, and it is a manifest validator — not a skill, plugin or
agent installer.

```python
from doublegate_sdk import evaluate_file

evaluation = evaluate_file('gate.json', 'runbook.md', artifact_type='memory')
print(evaluation.outcome, evaluation.human_review)   # e.g. "clean required"
```

A `clean` outcome means the manifest's literal strings matched. It does not
satisfy the manifest's `human_review` requirement, which travels into the result
for exactly that reason.

See the [SDK roadmap](docs/roadmap.md) for the release sequence.

For agent-assisted development, start with [llms.txt](llms.txt). Use
`python -m doublegate_sdk describe-client` for the offline client contract and
`python scripts/check.py` to verify source, tests and documentation in an existing
development environment. Neither command installs packages or starts a gate.

- [Source](https://github.com/doublegate-io/doublegate-sdk)
- [Versioned documentation](https://doublegate-io.github.io/doublegate-sdk/)
- [CI and documentation deployment status](https://github.com/doublegate-io/doublegate-sdk/actions)

## Run locally

Python 3.11+; file loading requires POSIX descriptor-relative operations and
`O_NOFOLLOW`. Verified on Linux/WSL; native Windows is unsupported by the loader.
No runtime dependencies and no service installation.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install '.[test,docs]'
python examples/quickstart.py
doublegate-sdk validate examples/gates/runbook/gate.json
python -m doublegate_sdk evaluate examples/gates/runbook/gate.json examples/gates/runbook/pass.txt --artifact-type memory
python -m pytest
mkdocs build --strict
python scripts/preview_docs.py
python -m http.server 8000 --directory site
```

Open http://localhost:8000/dev/ for the versioned local documentation. The preview
script creates a mike-compatible dev-only layout without Git commits or network
publication. `mkdocs serve` is also available for live editing without versioning.
See [the documentation policy](docs/versions.md) for the mike workflow.

Imports use `doublegate_sdk`, not `doublegate.sdk`. This wheel never installs a
`doublegate` namespace, service, daemon, scanner, or plugin. In-process result types
are structurally compatible with the extracted contract, not identical classes
to client-gate's existing types. Integrate explicitly through payloads.

## License and origin

Apache-2.0, copied unchanged from client-gate. Copyright 2026 Eugene Korniichuk
and the doublegate contributors. See [NOTICE](NOTICE) and
[extraction provenance](PROVENANCE.json). Source SDK files were untracked when
extracted; their recorded hashes, not client-gate HEAD, identify this snapshot.
Only this standalone snapshot is published; no client-gate history is included.
