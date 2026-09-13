"""Release smoke: run with a fresh wheel-only venv's Python and -I.

Uses only stdlib until validating the installed SDK. No repository examples,
source paths, pytest, daemon, credentials or service are needed.
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def main() -> int:
    distribution = importlib.metadata.distribution('doublegate-sdk')
    origin = json.loads(distribution.read_text('direct_url.json') or '{}')
    if origin.get('dir_info') is not None or not origin.get('url', '').endswith('.whl'):
        print('non-editable wheel installation required', file=sys.stderr)
        return 1

    import doublegate_sdk
    from doublegate_sdk.package import load_package
    from doublegate_sdk.runtime import evaluate

    package_root = Path(str(distribution.locate_file('doublegate_sdk'))).resolve()
    assert Path(doublegate_sdk.__file__).resolve().parent == package_root
    assert package_root.is_relative_to(Path(sys.prefix).resolve()), 'SDK outside consumer venv'
    assert (package_root / 'py.typed').is_file(), 'Missing typing marker'
    for namespace in ('doublegate', 'doublegate_org'):
        assert importlib.util.find_spec(namespace) is None, 'Authority distribution in SDK consumer'

    # -I ignores PYTHONPATH, user site and the current/script directory.
    with tempfile.TemporaryDirectory(prefix='sdk-consumer-', dir=Path.cwd()) as directory:
        work = Path(directory)
        manifest = work / 'gate.json'
        manifest.write_text(json.dumps({
            'schema_version': '0.1', 'sdk_version': '0.1',
            'name': 'release-smoke', 'version': '1.0.0',
            'artifact_types': ['memory'],
            'checks': [{'capability': 'required-text', 'text': 'Owner:'}],
            'max_input_bytes': 4096, 'human_review': 'required',
            'egress': [], 'secret_refs': [],
        }), encoding='utf-8')
        package = load_package(manifest).package
        assert package.human_review == 'required'
        for content, flagged in [('Owner: Operations', False), ('No owner recorded', True)]:
            evidence = evaluate(package, content, 'memory')
            assert evidence.flagged is flagged
            source = work / 'input.txt'
            source.write_text(content, encoding='utf-8')
            # Check both the installed module and generated console entry point.
            for command in ([sys.executable, '-I', '-m', 'doublegate_sdk'],
                            [str(Path(sys.executable).parent / 'doublegate-sdk')]):
                result = subprocess.run(
                    [*command, 'evaluate', str(manifest), str(source), '--artifact-type', 'memory'],
                    cwd=work, capture_output=True, text=True, timeout=15,
                )
                assert result.returncode == int(flagged), result.stderr
                payload = json.loads(result.stdout)
                assert payload['human_review'] == 'required'
                assert payload['outcome'] == ('flagged' if flagged else 'clean')
        result = subprocess.run(
            [sys.executable, '-I', '-m', 'doublegate_sdk', 'schema'],
            cwd=work, capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)['type'] == 'object'
    print(json.dumps({'version': distribution.version, 'wheel_origin': origin['url'],
                      'runtime': 'clean/flagged', 'cli': 'module/entrypoint/schema',
                      'human_review': 'required'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
