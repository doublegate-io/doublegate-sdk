# Real publisher fixture

These bytes were produced by Doublegate commit
`7e9b1e25f72ea41b9c6917dba9e0a51c29a404e0`, not authored as SDK payloads.
The original archive PAX commit was checked during the schema review. The
unchanged capture's `provenance.json` records the installed renderer hash, local
import/install paths, output hash and event count. Absolute paths describe that
capture; they are not required by tests.

`tests/support/generate_note_fixture.py` retains the actual generator:
`Ledger.append -> note_from_event -> JsonlSink.deliver -> UTF-8 JSONL` for promote,
relation and scan. It generates an ephemeral signing key in memory and reads no
configured credentials. No model, network or service publication occurs.

To reproduce, install that producer revision (with its own dependencies) in an
isolated environment, then run from the SDK repository:

```sh
env -u PYTHONPATH /path/to/producer-venv/bin/python -I \
  tests/support/generate_note_fixture.py \
  --producer-source /path/to/pinned-producer-source \
  --output .tmp/regenerated-note-fixture
```

The generator rejects a different renderer hash and checks the rendered schema
against the source's published `docs/api/note.schema.json`. It writes fresh notes
and provenance, not a byte-for-byte deterministic replay: keys, deployment IDs,
event IDs and timestamps can change. The maintained fixture is an immutable
capture; normal SDK tests need no producer installation. The generator was also
exercised against the prepared installed producer during this implementation.

This is producer/consumer contract evidence, not proof that gate runtime calls
`check_response`. The SDK has no authority over producer runtime wiring.
