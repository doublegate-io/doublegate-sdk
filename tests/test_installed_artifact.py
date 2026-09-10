"""The release smoke must reject an editable/source test environment."""
import importlib.metadata
import json
import pytest
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_release_smoke_rejects_checkout_install():
    origin = json.loads(importlib.metadata.distribution('doublegate-sdk').read_text('direct_url.json') or '{}')
    if not origin.get('dir_info', {}).get('editable'):
        pytest.skip('Negative control requires editable checkout; CI uses uv sync')
    script = ROOT / 'scripts/verify_installed.py'
    assert script.is_file(), 'Missing installed-artifact release verification'
    result = subprocess.run(
        [sys.executable, '-I', str(script)],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert 'non-editable wheel installation required' in result.stderr
