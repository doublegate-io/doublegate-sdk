"""Starter app: application memory across two sessions.

An application remembers one observation now and looks it up again later, in a
separate process, using the artifact id the gate returned. The id is the handle;
this app keeps it in a small local JSON file so the second session has something
to ask about. Nothing is inferred from a successful write.

Endpoint comes from --endpoint or DOUBLEGATE_ENDPOINT. A bearer credential, when
the deployment requires one, comes from DOUBLEGATE_TOKEN only: never a command
line argument, never printed. No gate is started and no substitute server is
used; the endpoint must be a real running gate.

Only `remember` writes. `check` and `recall` open a read-only client, so they
cannot write even if the gate would allow it.

`remember` will not overwrite an existing handle file: the artifact id it holds
is the only durable reference to that earlier submission. Pass `--state-file` for
a new one, or `--replace-handle` to discard the old id deliberately.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from doublegate_sdk.client import GateError, connect

#: Exit codes, so a shell caller can branch without reading English. A local
#: refusal (bad usage, unreadable file, missing handle) exits 1 via SystemExit;
#: argparse's own usage errors exit 2. ``HANDLE_NOT_SAVED`` is the one case where
#: the remote write succeeded but the local record of it did not: the id is on
#: stdout and must be kept by hand.
OK, GATE_FAILURE, OUTCOME_UNKNOWN, HANDLE_NOT_SAVED = 0, 3, 4, 5


def _endpoint(argument: str | None) -> str:
    endpoint = argument or os.environ.get('DOUBLEGATE_ENDPOINT')
    if not endpoint:
        raise SystemExit('set --endpoint or DOUBLEGATE_ENDPOINT to a running gate MCP URL')
    return endpoint


def _client(args: argparse.Namespace, connect_fn: Callable[..., Any], *, writes: bool) -> Any:
    return connect_fn(_endpoint(args.endpoint), token=os.environ.get('DOUBLEGATE_TOKEN'),
                      allow_writes=writes,
                      allow_insecure_loopback=args.allow_insecure_loopback,
                      timeout=args.timeout)


def _reserve(path: Path, *, replace: bool) -> bool:
    """Claim the handle path before the write, so a clobber is caught in time.

    An artifact id is the only durable reference to a submission, so overwriting
    one has to be asked for rather than assumed. This runs *before* ``propose``
    for two reasons: refusing afterwards would already have destroyed the earlier
    record, and an unwritable path (missing directory, read-only mount) is found
    while it is still a local usage error rather than a lost id.

    An existing handle is never truncated here, not even under ``--replace-handle``:
    the old id has to outlive a proposal that fails or answers unusably, because
    until a new id is in hand it is still the only reference there is. The
    replacement happens in ``_save``, after a usable id exists.

    Returns True when this call created the file, so a caller that never fills it
    can remove exactly what it made and nothing else.
    """
    if replace and path.is_file():
        if not os.access(path, os.W_OK):
            raise SystemExit(f'cannot write the handle file {path}: Permission denied')
        return False
    try:
        with open(path, 'x', encoding='utf-8'):
            pass
    except FileExistsError:
        if replace:
            return False        # Not a regular file, or created between the check and here.
        raise SystemExit(
            f'{path} already holds an artifact handle; that id is the only durable '
            f'reference to the earlier submission. Pass --state-file to keep both, '
            f'or --replace-handle to overwrite it deliberately.') from None
    except OSError as error:
        raise SystemExit(f'cannot write the handle file {path}: {error.strerror}') from None
    return True


def _release(path: Path, created: bool) -> None:
    """Drop a reservation that no artifact id ever filled. Never deletes data.

    Only a file this run created, and only while it is still empty, is removed —
    an existing handle awaiting replacement is left exactly as it was found.
    """
    if not created:
        return
    try:
        if path.is_file() and path.stat().st_size == 0:
            path.unlink()
    except OSError:
        pass        # An orphan empty file is harmless; failing to remove it is not fatal.


def _save(path: Path, record: dict[str, Any]) -> None:
    """Persist the artifact handle. Content and credential are not stored.

    This is the only place an existing handle is overwritten, and it runs only
    once a usable artifact id is in hand: either the path was reserved by this
    run, or ``--replace-handle`` authorised replacing what was there.
    """
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def _load(path: Path) -> dict[str, Any]:
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise SystemExit(f'no usable artifact handle in {path}; run `remember` first') from None
    if not isinstance(record, dict) or not isinstance(record.get('artifact_id'), str):
        raise SystemExit(f'{path} does not contain an artifact_id recorded by this app')
    return record


def remember(args: argparse.Namespace, connect_fn: Callable[..., Any] = connect) -> int:
    """Session one: propose one observation and keep the returned handle.

    Order matters. The handle path is reserved before the proposal, the returned
    id is printed before it is persisted, and a save failure is reported as its
    own exit code — the id is never printed later than the write it describes,
    and a failed save never sends the write again.
    """
    path = Path(args.state_file)
    created = _reserve(path, replace=args.replace_handle)
    client = _client(args, connect_fn, writes=True)
    try:
        proposal = client.propose(args.text, content_type='memory',
                                  trust_class=args.trust_class,
                                  source_uri=args.source_uri)
    except GateError as error:
        _release(path, created)
        return _report(error)
    try:
        record = {'artifact_id': _required(proposal, 'artifact_id'),
                  'state': _required(proposal, 'state'),
                  'source_uri': args.source_uri}
    except _MalformedAnswer as error:
        # The write happened. The answer is unusable, so nothing is claimed about
        # what it contained and the submission is not sent again.
        _release(path, created)
        return _report_malformed(error)
    print('artifact_id:', record['artifact_id'])
    print('state:', record['state'])
    try:
        _save(path, record)
    except OSError as error:
        print(f'handle_not_saved: artifact_id {record["artifact_id"]} was accepted but '
              f'the handle could not be saved to {path} ({error.strerror}); record the '
              f'id above now — it is the only reference to this submission',
              file=sys.stderr)
        return HANDLE_NOT_SAVED
    print('handle saved to:', args.state_file)
    print('note: this state is the gate\'s answer to the submission, not admission')
    return OK


def check(args: argparse.Namespace, connect_fn: Callable[..., Any] = connect) -> int:
    """Session two: ask the gate where that artifact stands now."""
    record = _load(Path(args.state_file))
    client = _client(args, connect_fn, writes=False)
    try:
        current = client.status(record['artifact_id'])
    except GateError as error:
        return _report(error)
    try:
        state_now = _required(current, 'state')
    except _MalformedAnswer as error:
        return _report_malformed(error)
    print('artifact_id:', record['artifact_id'])
    print('state at submission:', record.get('state'))
    print('state now:', state_now)
    for key in ('sub_level', 'quorum'):
        if key in current:
            print(f'{key}:', current[key])
    if current.get('blocking'):
        print('blocking:', current['blocking'])
    return OK


def recall(args: argparse.Namespace, connect_fn: Callable[..., Any] = connect) -> int:
    """Read served knowledge. Own unreviewed writes are excluded by the SDK."""
    client = _client(args, connect_fn, writes=False)
    try:
        answers = client.recall(args.query, limit=args.limit)
    except GateError as error:
        return _report(error)
    try:
        results = _required(answers, 'results')
    except _MalformedAnswer as error:
        return _report_malformed(error)
    print('results:', len(results))
    for hit in results:
        print(json.dumps(hit, sort_keys=True))
    if not results:
        print('empty: nothing served for this query yet; a pending write is not readable')
    return OK


class _MalformedAnswer(Exception):
    """A successful response that omits a key this app needs. Carries the key name only."""


def _required(answer: Any, key: str) -> Any:
    """Read one required key, or raise with the key name and nothing else."""
    try:
        return answer[key]
    except (KeyError, TypeError, IndexError):
        raise _MalformedAnswer(key) from None


def _report_malformed(error: _MalformedAnswer) -> int:
    """Fixed diagnostic for an unusable answer. The payload is never printed."""
    print('gate_error kind=invalid_response code=None', file=sys.stderr)
    print(f'invalid_response: the answer omitted {error.args[0]!r}; this client will '
          f'not guess at a value for it', file=sys.stderr)
    return GATE_FAILURE


def _report(error: GateError) -> int:
    """Print the fixed diagnostic. Remote error text is never surfaced."""
    print(f'gate_error kind={error.kind} code={error.code}', file=sys.stderr)
    if error.outcome_unknown:
        print('outcome_unknown: the write may have landed; reconcile before retrying',
              file=sys.stderr)
        return OUTCOME_UNKNOWN
    return GATE_FAILURE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='memory.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--endpoint', help='gate MCP URL; defaults to $DOUBLEGATE_ENDPOINT')
    parser.add_argument('--allow-insecure-loopback', action='store_true',
                        help='permit plain http:// to a loopback gate on this host')
    parser.add_argument('--timeout', type=float, default=15.0, help='total deadline per request')
    parser.add_argument('--state-file', default='memory-handle.json',
                        help='local file holding the artifact handle between sessions; '
                             'the default is a fixed name relative to the current '
                             'directory, so give an explicit path per project')
    commands = parser.add_subparsers(dest='command', required=True)

    write = commands.add_parser('remember', help='WRITES: propose one observation')
    write.add_argument('text', help='the observation to submit, as text')
    write.add_argument('--source-uri', default='application://starters/memory',
                       help='where this observation came from, in your own scheme')
    write.add_argument('--trust-class', default='T-4', help='trust class to claim')
    write.add_argument('--replace-handle', action='store_true',
                       help='overwrite an existing handle file; the artifact id it '
                            'holds is the only reference to that earlier submission')
    write.set_defaults(handler=remember)

    read = commands.add_parser('check', help='read the saved artifact\'s current state')
    read.set_defaults(handler=check)

    query = commands.add_parser('recall', help='read served knowledge')
    query.add_argument('query', help='what to look up')
    query.add_argument('--limit', type=int, default=5)
    query.set_defaults(handler=recall)
    return parser


def main(argv: list[str] | None = None, connect_fn: Callable[..., Any] = connect) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, connect_fn)


if __name__ == '__main__':
    raise SystemExit(main())
