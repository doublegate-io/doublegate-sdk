"""Starter app: is this gate reachable, and what did it refuse?

An operational probe for a deployment you already have. It negotiates with the
real endpoint the way the SDK does — `server/discover`, then `tools/list` — and
optionally performs one read, then maps whatever failed onto the SDK's fixed
error kinds with a fixed next step for each.

What this app does not do: it does not instrument the SDK, collect metrics, trace
requests, or read server logs. There is no hidden telemetry here. It reports the
result of calls it made itself, in this process.

Nothing sensitive is printed. The bearer credential is read from DOUBLEGATE_TOKEN
and reported only as present or absent; its value never reaches stdout, stderr or
an argument list. Remote error text is not printed either — the SDK withholds it
deliberately, because server messages carry internals.

Every operation is a read; the client is opened with writes disabled.
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

#: One fixed next step per SDK error kind. Fixed text, chosen from the kinds
#: `doublegate_sdk.client` actually raises — never a formatted server message.
NEXT_STEP: dict[str, str] = {
    'unauthorized': 'the endpoint rejected the credential; get a valid token from the operator '
                    'and supply it through DOUBLEGATE_TOKEN',
    'forbidden': 'the credential is accepted but not permitted this call; ask the operator for '
                 'the access this deployment requires',
    'unavailable': 'no usable connection to that host and port; confirm the gate is running and '
                   'the endpoint is the MCP URL, not the daemon administration URL',
    'timeout': 'the deadline passed with no complete answer; raise --timeout or check the gate\'s load',
    'redirect_refused': 'the endpoint answered with a redirect; the SDK refuses to carry the '
                        'credential to another host, so use the final URL directly',
    'unsupported_operation': 'this build does not serve that call, or the id is unknown to it',
    'invalid_response': 'the answer was not a response this client will guess at; confirm the URL '
                        'is a gate MCP endpoint and not another service',
    'remote_error': 'the gate answered with a failure it did not explain to clients; the operator\'s '
                    'logs hold the detail, this client does not',
    'response_too_large': 'the answer exceeded the configured response bound; lower the limit you '
                          'asked for or raise max_response_bytes deliberately',
    'request_too_large': 'the request exceeded the configured request bound; send less content',
    'writes_disabled': 'this client was opened read-only, and a write was attempted',
    'tool_error': 'the gate ran the call and reported a failure for it',
}


def _endpoint(argument: str | None) -> str:
    endpoint = argument or os.environ.get('DOUBLEGATE_ENDPOINT')
    if not endpoint:
        raise SystemExit('set --endpoint or DOUBLEGATE_ENDPOINT to a running gate MCP URL')
    return endpoint


def explain(error: GateError) -> str:
    """The fixed next step for an error kind, or a fixed fallback."""
    return NEXT_STEP.get(error.kind, 'unrecognized error kind for this SDK revision; '
                                     'report the kind, not a server message')


class _MalformedAnswer(Exception):
    """A successful response that omits a key this app needs. Carries the key name only."""


def _required(answer: Any, key: str) -> Any:
    """Read one required key, or raise with the key name and nothing else."""
    try:
        return answer[key]
    except (KeyError, TypeError, IndexError):
        raise _MalformedAnswer(key) from None


def probe(args: argparse.Namespace, connect_fn: Callable[..., Any] = connect) -> int:
    """Negotiate with the endpoint, then optionally read, reporting each step."""
    endpoint = _endpoint(args.endpoint)
    print('endpoint:', endpoint)
    print('token supplied:', bool(os.environ.get('DOUBLEGATE_TOKEN')))
    client = connect_fn(endpoint, token=os.environ.get('DOUBLEGATE_TOKEN'),
                        allow_writes=False,
                        allow_insecure_loopback=args.allow_insecure_loopback,
                        timeout=args.timeout)
    failures = 0
    for step, call in (('discover', lambda: client.transport.discover()),
                       ('tools', lambda: client.transport.tool_names())):
        try:
            answer = call()
        except GateError as error:
            failures += 1
            print(f'{step}: gate_error kind={error.kind} code={error.code}', file=sys.stderr)
            print(f'{step}: next step — {explain(error)}', file=sys.stderr)
            continue
        if step == 'tools':
            print('tools:', ', '.join(answer) or '(none listed)')
        else:
            print('discover:', json.dumps(answer, sort_keys=True, default=str))

    if args.query is not None:
        try:
            answers = client.recall(args.query, limit=args.limit)
            print('recall results:', len(_required(answers, 'results')))
        except GateError as error:
            failures += 1
            print(f'recall: gate_error kind={error.kind} code={error.code}', file=sys.stderr)
            print(f'recall: next step — {explain(error)}', file=sys.stderr)
        except _MalformedAnswer as error:
            failures += 1
            print(f'recall: gate_error kind=invalid_response code=None', file=sys.stderr)
            print(f'recall: the answer omitted {error.args[0]!r}; '
                  f'next step — {NEXT_STEP["invalid_response"]}', file=sys.stderr)

    if args.artifact_id is not None:
        try:
            print('status:', _required(client.status(args.artifact_id), 'state'))
        except GateError as error:
            failures += 1
            print(f'status: gate_error kind={error.kind} code={error.code}', file=sys.stderr)
            print(f'status: next step — {explain(error)}', file=sys.stderr)
        except _MalformedAnswer as error:
            failures += 1
            print(f'status: gate_error kind=invalid_response code=None', file=sys.stderr)
            print(f'status: the answer omitted {error.args[0]!r}; '
                  f'next step — {NEXT_STEP["invalid_response"]}', file=sys.stderr)

    print('failed steps:', failures)
    return GATE_FAILURE if failures else OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='diagnostics.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--endpoint', help='gate MCP URL; defaults to $DOUBLEGATE_ENDPOINT')
    parser.add_argument('--allow-insecure-loopback', action='store_true',
                        help='permit plain http:// to a loopback gate on this host')
    parser.add_argument('--timeout', type=float, default=15.0, help='total deadline per request')
    commands = parser.add_subparsers(dest='command', required=True)

    check = commands.add_parser('probe', help='negotiate, optionally read, and report each step')
    check.add_argument('--query', help='also attempt one recall with this query')
    check.add_argument('--limit', type=int, default=1, help='limit for the optional recall')
    check.add_argument('--artifact-id', help='also attempt one status read for this id')
    check.set_defaults(handler=probe)
    return parser


def main(argv: list[str] | None = None, connect_fn: Callable[..., Any] = connect) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, connect_fn)


if __name__ == '__main__':
    raise SystemExit(main())
