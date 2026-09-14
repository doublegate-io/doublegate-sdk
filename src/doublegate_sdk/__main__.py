# Modified for standalone doublegate_sdk namespace; see NOTICE.
"""Offline SDK tools: schema, package validation/evaluation and client description."""
from __future__ import annotations

import argparse
import json
import sys

from doublegate_sdk.authoring import evaluate_file
from doublegate_sdk.errors import DoublegateError
from doublegate_sdk.manifest import manifest_schema
from doublegate_sdk.package import load_package


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('schema', help='emit manifest JSON Schema to stdout')
    sub.add_parser('describe-client', help='describe the SDK client API offline (not server capabilities)')
    sub.add_parser('validate', help='validate a JSON package').add_argument('manifest')
    ev = sub.add_parser('evaluate', help='produce evidence; never publish')
    ev.add_argument('manifest')
    ev.add_argument('input', help='regular UTF-8 file; no stdin or URLs')
    ev.add_argument('--artifact-type', required=True, choices=['memory', 'skill', 'script', 'tool'])
    args = parser.parse_args(argv)
    if args.command == 'describe-client':
        from doublegate_sdk.client import describe_client
        print(json.dumps(describe_client(), indent=2, sort_keys=True))
        return 0
    if args.command == 'schema':
        print(json.dumps(manifest_schema(), indent=2, sort_keys=True))
        return 0
    try:
        if args.command == 'validate':
            loaded = load_package(args.manifest)
            package = loaded.package
            print(json.dumps({'name': package.name, 'version': package.version,
                              'digest': loaded.digest, 'human_review': package.human_review,
                              'valid': True}, sort_keys=True))
            return 0
        evaluation = evaluate_file(args.manifest, args.input, artifact_type=args.artifact_type)
        print(json.dumps(evaluation.to_payload(), sort_keys=True))
        return 1 if evaluation.flagged else 0
    except DoublegateError as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
