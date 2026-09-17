#!/usr/bin/env python3
"""Verify this SDK checkout with the selected interpreter; never install or publish."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'src' / 'doublegate_sdk'


def check_origins():
    for name, module in list(sys.modules.items()):
        if name == 'doublegate_sdk' or name.startswith('doublegate_sdk.'):
            origin = getattr(module, '__file__', None)
            if origin is None or not Path(origin).resolve().is_relative_to(SOURCE):
                raise RuntimeError(f'SDK source mismatch: {name}')


def preflight():
    sys.path.insert(0, str(ROOT / 'src'))
    importlib.import_module('doublegate_sdk')
    check_origins()
    missing = [name for name in ('pytest', 'mkdocs') if importlib.util.find_spec(name) is None]
    if missing:
        raise RuntimeError('Selected interpreter lacks: ' + ', '.join(missing) + '; nothing installed')
    return {'interpreter': sys.executable, 'source': str(SOURCE), 'root': str(ROOT)}


def coverage(path, *, require_success=True):
    cases = ET.parse(path).getroot().findall('.//testcase')
    counts = {'collected': len(cases), 'passed': 0, 'failed': 0, 'errors': 0, 'skipped': 0}
    for case in cases:
        if case.find('failure') is not None: counts['failed'] += 1
        elif case.find('error') is not None: counts['errors'] += 1
        elif case.find('skipped') is not None: counts['skipped'] += 1
        else: counts['passed'] += 1
    if require_success and (not cases or counts['passed'] != len(cases)):
        raise RuntimeError('Incomplete test acceptance: ' + json.dumps(counts, sort_keys=True))
    return counts


def snapshot():
    # examples/ is hashed because the suite executes it: tests/test_examples.py
    # runs examples/quickstart.py, and the starter tests run and import
    # examples/starters/*. A snapshot that skips the programs under test cannot
    # support a source_unchanged claim about them.
    paths = [ROOT / 'pyproject.toml', ROOT / 'mkdocs.yml', ROOT / 'README.md',
             ROOT / 'llms.txt', ROOT / 'MANIFEST.in']
    paths = [p for p in paths if p.is_file()]
    paths += [p for base in (SOURCE, ROOT / 'docs', ROOT / 'tests', ROOT / 'scripts',
                             ROOT / 'examples')
              for p in base.rglob('*') if p.is_file() and '__pycache__' not in p.parts
              and p.suffix != '.pyc']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


class SourceGuard:
    def pytest_collection_finish(self, session): check_origins()
    def pytest_sessionfinish(self, session, exitstatus): check_origins()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', action='store_true', help='check prerequisites without tests/build')
    parser.add_argument('--test-worker', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        info = preflight()
        if args.preflight:
            print(json.dumps(info, sort_keys=True)); return 0
        if args.test_worker:
            import pytest
            # The suite is run here with plugin autoload off (below), so a
            # distribution plugin named in `addopts` would be an unrecognised
            # `-n`. Load it by name when it is installed, and drop the option
            # when it is not, so the gate runs the same tests either way.
            argv = [str(ROOT/'tests'), '-q', '-p', 'no:cacheprovider',
                    '--junitxml=' + str(args.test_worker)]
            if importlib.util.find_spec('xdist') is not None:
                argv[1:1] = ['-p', 'xdist']
            else:
                argv[1:1] = ['-p', 'no:xdist', '-o', 'addopts=']
            return int(pytest.main(argv, plugins=[SourceGuard()]))
    except RuntimeError as error:
        print(str(error), file=sys.stderr); return 2
    output_parent = ROOT / '.tmp' / 'verification'
    output_parent.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix='run-', dir=output_parent))
    report = {**info, 'status': 'running', 'steps': [], 'live_deployment_verified': False}
    result_path = output/'result.json'
    result_path.write_text(json.dumps(report, indent=2)+'\n')
    before = None
    phase = 'snapshot'
    env = dict(os.environ)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'PYTEST_ADDOPTS', 'PYTEST_PLUGINS'):
        env.pop(key, None)
    env.update(PYTHONPATH=str(ROOT/'src'), PYTHONDONTWRITEBYTECODE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    commands = [
        ('tests', [sys.executable, str(Path(__file__).resolve()), '--test-worker', str(output/'tests.xml')]),
        ('docs', [sys.executable, '-m', 'mkdocs', 'build', '--strict', '--site-dir', str(output/'site')]),
    ]
    code = 0
    try:
        before = snapshot()
        for name, command in commands:
            phase = name
            step = {'name': name, 'exit_code': None}
            report['steps'].append(step)
            result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
            (output/f'{name}.log').write_text(result.stdout + result.stderr)
            step['exit_code'] = result.returncode
            if name == 'tests' and (output/'tests.xml').is_file():
                report['coverage'] = coverage(output/'tests.xml', require_success=False)
            if result.returncode:
                raise RuntimeError(name + ' failed; inspect ' + str(output/f'{name}.log'))
            if name == 'tests': report['coverage'] = coverage(output/'tests.xml')
        phase = 'source_stability'
        if snapshot() != before:
            raise RuntimeError('Source changed during verification')
        report.update(status='passed', source_unchanged=True, source_hashes=before)
    except (RuntimeError, OSError, subprocess.TimeoutExpired, ET.ParseError) as error:
        code = 1
        if isinstance(error, subprocess.TimeoutExpired):
            def text(value):
                return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value or ''
            (output/f'{phase}.log').write_text(text(error.stdout) + text(error.stderr))
            report['steps'][-1]['exit_code'] = 124
        unchanged = None
        if before is not None:
            try:
                unchanged = snapshot() == before
            except OSError:
                pass  # Missing/unreadable source is unknown, not an unchanged tree.
        report.update(status='failed', error=str(error), failure_phase=phase,
                      source_unchanged=unchanged)
    finally:
        result_path.write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps({'status': report['status'], 'coverage': report.get('coverage'),
                          'report': str(result_path), 'error': report.get('error')}, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
