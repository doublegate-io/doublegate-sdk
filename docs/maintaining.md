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
