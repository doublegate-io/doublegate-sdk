"""Build a mike-compatible dev preview without creating Git commits."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, default=Path("site"))
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
output = args.output.resolve()
if output == root or root.is_relative_to(output):
    parser.error("output must not contain the source checkout")
subprocess.run([sys.executable, "-m", "mkdocs", "build", "--strict", "--site-dir", str(output / "dev")], cwd=root, check=True)
(output / "versions.json").write_text(json.dumps([{"version": "dev", "title": "dev (unreleased)", "aliases": []}], indent=2) + "\n")
(output / "index.html").write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>Doublegate SDK</title><meta http-equiv="refresh" content="0; url=dev/"><a href="dev/">Development documentation</a></html>')
print(f"Development documentation: {output / 'dev/index.html'}")
