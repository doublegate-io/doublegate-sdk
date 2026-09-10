import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_executable_quickstart():
    result = subprocess.run([sys.executable, str(ROOT / 'examples/quickstart.py')], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'clean' in result.stdout
    assert 'flagged' in result.stdout
    assert 'required' in result.stdout


def test_local_versioned_preview(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/preview_docs.py'), '--output', str(tmp_path / 'site')], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    import json
    site = tmp_path / 'site'
    assert json.loads((site / 'versions.json').read_text()) == [{'version': 'dev', 'title': 'dev (unreleased)', 'aliases': []}]
    assert (site / 'dev/api/manifest/index.html').exists()
    assert 'doublegate_sdk.manifest.validate_manifest' in (site / 'dev/api/manifest/index.html').read_text()
