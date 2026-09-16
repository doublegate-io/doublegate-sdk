# Modified for standalone doublegate_sdk namespace; see NOTICE.
"""Offline SDK tools: schema, package validation/evaluation and client description."""
from __future__ import annotations

import argparse
import json
import sys

from doublegate_sdk.manifest import ManifestError, manifest_schema
from doublegate_sdk.package import PackageError, load_package, read_bounded
from doublegate_sdk.runtime import EvaluationError, evaluate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('schema', help='emit manifest JSON Schema to stdout')
    sub.add_parser('describe-client', help='describe the SDK clients offline: methods, verbs, scopes, errors (not a gate\'s capabilities)')
    sub.add_parser('validate', help='validate a JSON package').add_argument('manifest')
    ev = sub.add_parser('evaluate', help='produce evidence; never publish')
    ev.add_argument('manifest')
    ev.add_argument('input', help='regular UTF-8 file; no stdin or URLs')
    ev.add_argument('--artifact-type', required=True, choices=['memory', 'skill', 'script', 'tool'])
    args = parser.parse_args(argv)
    if args.command == 'describe-client':
        from doublegate_sdk.describe import describe_client
        print(json.dumps(describe_client(), indent=2, sort_keys=True))
        return 0
    if args.command == 'schema':
        print(json.dumps(manifest_schema(), indent=2, sort_keys=True))
        return 0
    try:
        loaded = load_package(args.manifest)
        package = loaded.package
        metadata = dict(name=package.name, version=package.version, digest=loaded.digest,
                        human_review=package.human_review)
        if args.command == 'validate':
            print(json.dumps(metadata | {'valid': True}, sort_keys=True))
            return 0
        try:
            content = read_bounded(args.input, package.max_input_bytes).decode('utf-8')
        except UnicodeDecodeError:
            raise EvaluationError('invalid_utf8') from None
        result = evaluate(package, content, args.artifact_type)
        print(json.dumps(metadata | result.to_payload(), sort_keys=True))
        return 1 if result.flagged else 0
    except (ManifestError, PackageError, EvaluationError) as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
