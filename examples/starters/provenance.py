"""Starter app: read one artifact's recorded provenance.

`doublegate.why` is the audit answer for a single artifact: every ledger event
that moved it, in order, with signatures. This app prints what the service
actually returned rather than a shape invented here — the summary lists the keys
present on each event, so a field this build does not send shows as absent
instead of as an empty value someone might trust.

The client tier's `why` needs an artifact id. There is no whole-gate audit call
on this surface, and nothing here writes: the client is opened read-only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable

from doublegate_sdk.client import GateError, connect

#: Exit codes; a local refusal exits 1 via SystemExit, argparse usage errors 2.
OK, GATE_FAILURE = 0, 3

#: Keys the maintained client tier's `why` events carry today (mcp.py
#: `doublegate.why` → daemon `dg.why`). Listed so the app can report presence,
#: not to require them: a build that sends fewer is reported, not rejected.
EXPECTED_EVENT_KEYS = ('event_id', 'type', 'identity', 'ts', 'payload', 'sig')


def _endpoint(argument: str | None) -> str:
    endpoint = argument or os.environ.get('DOUBLEGATE_ENDPOINT')
    if not endpoint:
        raise SystemExit('set --endpoint or DOUBLEGATE_ENDPOINT to a running gate MCP URL')
    return endpoint


def _client(args: argparse.Namespace, connect_fn: Callable[..., Any]) -> Any:
    # allow_writes stays False: every operation in this app is a read.
    return connect_fn(_endpoint(args.endpoint), token=os.environ.get('DOUBLEGATE_TOKEN'),
                      allow_writes=False,
                      allow_insecure_loopback=args.allow_insecure_loopback,
                      timeout=args.timeout)


def summarize(answer: dict[str, Any]) -> dict[str, Any]:
    """Describe the returned audit payload using only what is in it.

    Returns the artifact id as answered, the event count, the ordered event
    types, the keys each event actually carried, and which expected keys were
    missing from at least one event.

    A build that does not send ``events`` at all is reported the same way a build
    that omits an event field is: the key is listed as absent and nothing is
    invented for it. ``events_present`` says which of the two happened, so an
    absent list is never read as an empty one.
    """
    raw = answer.get('events') if isinstance(answer, dict) else None
    events = raw if isinstance(raw, list) else []
    present: set[str] = set()
    types: list[Any] = []
    for event in events:
        if isinstance(event, dict):
            present.update(event)
            types.append(event.get('type'))
        else:
            types.append(None)
    absent = [key for key in EXPECTED_EVENT_KEYS if key not in present]
    return {
        'artifact_id': answer.get('artifact_id') if isinstance(answer, dict) else None,
        'events_present': isinstance(raw, list),
        'event_count': len(events),
        'event_types': types,
        'event_keys_present': sorted(present),
        'expected_keys_absent': absent if isinstance(raw, list) else ['events', *absent],
    }


def why(args: argparse.Namespace, connect_fn: Callable[..., Any] = connect) -> int:
    """Read the ledger events for one artifact and print them in order."""
    client = _client(args, connect_fn)
    try:
        answer = client.why(args.artifact_id)
    except GateError as error:
        return _report(error)
    summary = summarize(answer)
    print('artifact_id:', summary['artifact_id'])
    if not summary['events_present']:
        print('events: absent')
        print('note: this build did not send an events list; that is reported, not '
              'read as zero events')
    print('event_count:', summary['event_count'])
    print('event_types:', ', '.join(str(t) for t in summary['event_types']) or '(none)')
    print('event_keys_present:', ', '.join(summary['event_keys_present']) or '(none)')
    if summary['expected_keys_absent']:
        print('expected_keys_absent:', ', '.join(summary['expected_keys_absent']))
        print('note: this build did not send those keys; do not assume a default for them')
    if args.events:
        for event in (answer.get('events') or []):
            print(json.dumps(event, sort_keys=True, default=str))
    return OK if summary['events_present'] else GATE_FAILURE


def _report(error: GateError) -> int:
    print(f'gate_error kind={error.kind} code={error.code}', file=sys.stderr)
    if error.kind == 'unsupported_operation':
        print('the gate answered that it does not serve this call for this id; '
              'an unknown artifact id also lands here', file=sys.stderr)
    return GATE_FAILURE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='provenance.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--endpoint', help='gate MCP URL; defaults to $DOUBLEGATE_ENDPOINT')
    parser.add_argument('--allow-insecure-loopback', action='store_true',
                        help='permit plain http:// to a loopback gate on this host')
    parser.add_argument('--timeout', type=float, default=15.0, help='total deadline per request')
    commands = parser.add_subparsers(dest='command', required=True)

    audit = commands.add_parser('why', help='read the ledger events for one artifact')
    audit.add_argument('artifact_id', help='the id returned when the artifact was proposed')
    audit.add_argument('--events', action='store_true',
                       help='also print each event as returned JSON')
    audit.set_defaults(handler=why)
    return parser


def main(argv: list[str] | None = None, connect_fn: Callable[..., Any] = connect) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, connect_fn)


if __name__ == '__main__':
    raise SystemExit(main())
