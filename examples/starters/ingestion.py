"""Starter app: submit a local UTF-8 document through the SDK.

This is the smallest honest ingestion path: one local text file, read as UTF-8,
submitted with a source URI derived from its real path. There is no crawler, no
fetcher, no queue and no binary handling here — the client surface stores text,
and a document that is not valid UTF-8 is refused locally rather than smuggled
through a text field.

Endpoint comes from --endpoint or DOUBLEGATE_ENDPOINT; a bearer credential, when
required, from DOUBLEGATE_TOKEN only. Only `submit` writes; `pending` opens a
read-only client.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from doublegate_sdk.client import GateError, connect

#: Exit codes; a local refusal exits 1 via SystemExit, argparse usage errors 2.
OK, GATE_FAILURE, OUTCOME_UNKNOWN = 0, 3, 4

#: A document larger than this is refused before a request is built. The
#: transport has its own request bound; this one keeps the error local and
#: specific instead of arriving as `request_too_large`.
MAX_DOCUMENT_BYTES = 512_000


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


def read_document(path: Path, *, max_bytes: int = MAX_DOCUMENT_BYTES) -> str:
    """Read one local document as UTF-8 text, with the refusals stated up front.

    Raises SystemExit with a fixed message for: missing file, oversize file,
    non-UTF-8 bytes, and an empty document. None of those reach the gate.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        raise SystemExit(f'cannot read document: {path}') from None
    if len(raw) > max_bytes:
        raise SystemExit(f'document exceeds {max_bytes} bytes; split it before submitting')
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        raise SystemExit('document is not valid UTF-8 text; this surface stores no binary') from None
    if not text.strip():
        raise SystemExit('document is empty; nothing to submit')
    return text


def source_uri_for(path: Path, override: str | None = None) -> str:
    """The document's own location as the source URI, unless one is supplied."""
    if override:
        return override
    return path.resolve().as_uri()


def submit(args: argparse.Namespace, connect_fn: Callable[..., Any] = connect) -> int:
    """WRITES: submit one local document. Success is acceptance, not admission."""
    path = Path(args.path)
    text = read_document(path)
    source_uri = source_uri_for(path, args.source_uri)
    client = _client(args, connect_fn, writes=True)
    try:
        result = client.propose(text, content_type=args.content_type,
                                trust_class=args.trust_class, source_uri=source_uri)
    except GateError as error:
        return _report(error)
    try:
        artifact_id = _required(result, 'artifact_id')
        state = _required(result, 'state')
    except _MalformedAnswer as error:
        # The write happened; the answer is unusable. Nothing is resent.
        return _report_malformed(error)
    print('source_uri:', source_uri)
    print('characters:', len(text))
    print('artifact_id:', artifact_id)
    print('state:', state)
    for key in ('findings_count', 'duplicate'):
        if key in result:
            print(f'{key}:', result[key])
    print('note: the gate accepted this submission; review is separate')
    if 'findings_count' in result:
        # findings_count is the only evidence in this answer that content was
        # scanned. Without it, acceptance is all this app observed.
        print('note: findings_count above is the gate\'s own report that it scanned '
              'the content')
    return OK


def pending(args: argparse.Namespace, connect_fn: Callable[..., Any] = connect) -> int:
    """Read what is awaiting review — metadata only, never document content."""
    client = _client(args, connect_fn, writes=False)
    try:
        waiting = client.pending(limit=args.limit)
    except GateError as error:
        return _report(error)
    try:
        entries = _required(waiting, 'pending')
    except _MalformedAnswer as error:
        return _report_malformed(error)
    print('pending:', len(entries))
    for entry in entries:
        print(json.dumps(entry, sort_keys=True))
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
    print(f'gate_error kind={error.kind} code={error.code}', file=sys.stderr)
    if error.outcome_unknown:
        print('outcome_unknown: this document may already be filed; check `pending` '
              'before submitting it again', file=sys.stderr)
        return OUTCOME_UNKNOWN
    return GATE_FAILURE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='ingestion.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--endpoint', help='gate MCP URL; defaults to $DOUBLEGATE_ENDPOINT')
    parser.add_argument('--allow-insecure-loopback', action='store_true',
                        help='permit plain http:// to a loopback gate on this host')
    parser.add_argument('--timeout', type=float, default=30.0, help='total deadline per request')
    commands = parser.add_subparsers(dest='command', required=True)

    write = commands.add_parser('submit', help='WRITES: submit one local UTF-8 document')
    write.add_argument('path', help='path to a local UTF-8 text document')
    write.add_argument('--content-type', default='document', help='content type to declare')
    write.add_argument('--trust-class', default='T-3', help='trust class to claim')
    write.add_argument('--source-uri', help='override the file:// URI derived from the path')
    write.set_defaults(handler=submit)

    queue = commands.add_parser('pending', help='list what is awaiting review (metadata only)')
    queue.add_argument('--limit', type=int, default=20)
    queue.set_defaults(handler=pending)
    return parser


def main(argv: list[str] | None = None, connect_fn: Callable[..., Any] = connect) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, connect_fn)


if __name__ == '__main__':
    raise SystemExit(main())
