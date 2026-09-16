# doublegate-sdk — `doublegate_sdk`

Standalone offline gate contracts plus an explicitly connected local gate client. Zero runtime
dependencies. A scan result, a successful proposal or an HTTP/RPC response is **not** admission
authority. The developer map, with the boundaries an agent must preserve, is `llms.txt`, imported
below. Workspace map and skills: `../CLAUDE.md`.

@llms.txt

Quick reference:

- Verify: `/dg-verify doublegate-sdk` runs `scripts/check.py` (tests, strict mkdocs, build;
  each run recorded under `.tmp/verification/`); `--fast` is `pytest -q` only.
- Every gate imports this package from `src/` editable; a change here is a change to three
  consumers. Run `/dg-verify client-gate` afterwards. `/dg-env` shows which consumer venvs still
  hold a snapshot.
- Docs are versioned with `mike` and published from `main` by CI; `docs/maintaining.md` says how.
