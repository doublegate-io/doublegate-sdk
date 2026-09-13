# Building and publishing documentation

## Tool choice

[Material for MkDocs](https://squidfunk.github.io/mkdocs-material/setup/setting-up-versioning/)
provides accessible navigation, dark mode, search and native mike version selection.
[mkdocstrings-python](https://mkdocstrings.github.io/python/) collects Python source
with Griffe and renders signatures, annotations, source and per-member anchors.
[mike](https://github.com/jimporter/mike) retains built version snapshots in a Git
branch. Together these provide a Javadoc-like API index with a Python-native build.
They are documentation extras, never runtime dependencies.

## Local verification

With an existing SDK development environment, the normal source verification is:

```sh
python scripts/check.py --preflight
python scripts/check.py
python -m doublegate_sdk describe-client
```

Select the interpreter explicitly (for example `.venv/bin/python`). The checker
does not install missing packages: it names missing prerequisites and exits.
It resolves this checkout independently of the current working directory, pins
the SDK source for test subprocesses, checks loaded SDK origins during collection
and completion, and records test counts plus source hashes. Empty or skipped
tests fail acceptance. Each run gets its own `.tmp/verification/run-*/` directory,
including logs and `result.json`, so a failed run does not overwrite earlier
passing evidence. Strict MkDocs build is part of the same command.

Failed tests retain their JUnit coverage counts. Timeout reports retain partial
stdout/stderr and mark the failed step with exit code 124. Snapshot failures are
recorded as failures; if source identity cannot be measured, `source_unchanged`
is null rather than a fabricated true/false assertion. These records identify
the failure phase so the next coding session can resume without rediscovery.

These checks prove source/test/documentation consistency, not wheel installation,
cross-principal authorization, real model quality or a deployed gate lifecycle.
The description command describes the SDK client only; remote gate capability
discovery remains separate. Start agent-assisted work from the root `llms.txt`.

The following are manual setup/build commands for maintainers with the appropriate
installation and publication permissions; they are not run by `check.py`:

```sh
python -m pip install '.[test,docs]'
python -m pytest
python -m build
mkdocs build --strict
python scripts/preview_docs.py
python -m http.server 8000 --directory site
```

The preview script builds `site/dev/`, writes a one-entry version index and a root
redirect. It does not commit, push or claim to execute mike deployment. Keep the
local server bound to localhost when needed with `--bind 127.0.0.1`.

## Installed wheel acceptance

The Linux CI matrix also installs the newly built wheel without extras into a
fresh virtual environment and runs `scripts/verify_installed.py` with isolated
Python imports (`-I`) outside the source root. The check rejects editable installs,
checks package origin and `py.typed`, and exercises the installed runtime, module
CLI, console entry point and schema command. Both clean and flagged evidence must
retain required human review. Neither service distribution may be installed.

To repeat locally after `uv build`:

```sh
uv venv .tmp/wheel-consumer
uv pip install --python .tmp/wheel-consumer/bin/python dist/*.whl
uv pip check --python .tmp/wheel-consumer/bin/python
mkdir -p .tmp/consumer-work
consumer_python="$PWD/.tmp/wheel-consumer/bin/python"
verifier="$PWD/scripts/verify_installed.py"
(cd .tmp/consumer-work && "$consumer_python" -I "$verifier")
```

Use a new consumer directory for each verification. This proves the built SDK's
installed behavior, not client/org release readiness, server authorization,
native Windows support or production acceptance. No artifact is uploaded by this
check and no existing GitHub release asset is replaced.

## GitHub Pages deployment

The SDK checks and documentation workflow tests Python 3.11–3.13 on Linux,
builds distributions and strict docs, then deploys the `dev` channel on main.
Actions are pinned to commit SHAs. Pull requests cannot deploy. Mike retains
version snapshots on `gh-pages`; Pages uses the uploaded artifact.

The canonical site root is https://doublegate-io.github.io/doublegate-sdk/.
Override `SDK_DOCS_URL` only for another host, including its trailing slash.
Check GitHub Actions for the actual deployment result.

After explicit publication approval, the maintainer can run:

```sh
# Creates gh-pages commits; pushes only with the explicit --push option.
mike deploy --title 'dev (unreleased)' dev
mike set-default dev
```

For a real tested release, substitute its actual version below; this is a
procedure, not a claim that any release already exists:

```sh
mike deploy --update-aliases "$RELEASE_VERSION" stable
mike set-default stable
```

Fetch an existing gh-pages branch before updating it. Never overwrite prior
release paths. Set the Material default alias to `stable` only after that alias
exists. Release snapshots are maintainer-owned; CI updates only `dev`. No PyPI upload
or automatic stable release is configured.

## Provenance and licensing

The root LICENSE is byte-for-byte Apache-2.0 from client-gate, including Eugene
Korniichuk and contributor attribution. NOTICE describes adaptations. The source
SDK was untracked; PROVENANCE.json hashes identify it without pretending its HEAD
contains those files. There is no missing-license blocker. The public repository contains a fresh standalone history, not client-gate history.
No PyPI release is implied.
