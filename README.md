# Doublegate SDK

Offline gate authoring for Python. Validate a declarative manifest, run bounded
literal checks, and return evidence. **A clean result is not admission.**

Development snapshot **0.1.0.dev0**. No PyPI package or stable release yet.

The optional [gate memory client](docs/api/client.md) connects explicitly to
an authorized local Client Gate socket for status, inventory and recall, with
proposal writes requiring explicit opt-in. It adds no
mandatory dependencies and makes no connection on import. See the
[SDK roadmap](docs/roadmap.md) for the service-client and adapter release sequence.

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
