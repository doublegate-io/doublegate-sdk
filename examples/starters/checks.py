"""Starter: run a deterministic check manifest over files and emit a machine result.

Fully working and fully offline. ``evaluate_file`` loads the manifest, reads one
bounded regular file and matches the manifest's literal required strings. It
opens no connection and reaches no gate.

What a ``clean`` outcome means, exactly: the manifest's literal strings were
present. It is evidence, not admission, and it does not satisfy the manifest's
``human_review`` requirement — which is why that value is carried into every
result below rather than dropped once the strings matched.

Usage::

    python examples/starters/checks.py --help
    python examples/starters/checks.py
    python examples/starters/checks.py --format text
    python examples/starters/checks.py --manifest my.gate.json --artifact-type memory FILE...

Exit status: ``0`` every file was clean, ``1`` at least one file was flagged,
``2`` a file or manifest could not be evaluated. The non-zero statuses are what
makes this usable as a pre-commit or CI step.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from doublegate_sdk.authoring import evaluate_file
from doublegate_sdk.errors import DoublegateError

DATA = Path(__file__).resolve().parent / "data"
DEFAULT_MANIFEST = DATA / "release-note.gate.json"
DEFAULT_INPUTS = (DATA / "release-note.clean.md", DATA / "release-note.flagged.md")

#: Exit statuses, named so a caller wiring this into CI does not guess.
OK, FLAGGED, UNEVALUATED = 0, 1, 2


def check_paths(manifest: Path, paths: tuple[Path, ...], *, artifact_type: str) -> dict[str, Any]:
    """Evaluate every path against one manifest; return one machine-readable result.

    An unevaluable file does not abort the run: it becomes an ``unevaluated``
    entry carrying the SDK's fixed diagnostic ``kind``. A check run that stops at
    the first bad file tells you less than one that reports all of them.
    """
    files: list[dict[str, Any]] = []
    for path in paths:
        try:
            evaluation = evaluate_file(manifest, path, artifact_type=artifact_type)
        except DoublegateError as error:
            # Fixed diagnostics only. SDK errors carry no path, key or content,
            # and this starter does not add any beyond the path it was given.
            files.append({"path": str(path), "status": "unevaluated",
                          "kind": getattr(error, "kind", "sdk_error")})
            continue
        payload = evaluation.to_payload()
        files.append({"path": str(path), "status": payload["outcome"], **payload})

    outcomes = [entry["status"] for entry in files]
    return {
        "manifest": str(manifest),
        "artifact_type": artifact_type,
        "files": files,
        "counts": {name: outcomes.count(name)
                   for name in ("clean", "flagged", "unevaluated")},
        # Stated on every result so no consumer has to infer it from an outcome.
        "is_admission_decision": False,
        "human_review_satisfied": False,
    }


def exit_status(result: dict[str, Any]) -> int:
    counts = result["counts"]
    if counts["unevaluated"]:
        return UNEVALUATED
    return FLAGGED if counts["flagged"] else OK


def render_text(result: dict[str, Any]) -> str:
    lines = [f"manifest: {result['manifest']}  artifact_type: {result['artifact_type']}"]
    for entry in result["files"]:
        if entry["status"] == "unevaluated":
            lines.append(f"  unevaluated  {entry['path']}  ({entry['kind']})")
            continue
        lines.append(f"  {entry['status']:<11}  {entry['path']}  "
                     f"gate={entry['name']}@{entry['version']} "
                     f"human_review={entry['human_review']}")
        for finding in entry["findings"]:
            lines.append(f"      - {finding['kind']}/{finding['severity']}: {finding['detail']}")
    counts = result["counts"]
    lines.append(f"clean={counts['clean']} flagged={counts['flagged']} "
                 f"unevaluated={counts['unevaluated']}")
    lines.append("evidence only: no outcome here is an admission decision, and "
                 "human review remains outstanding as the manifest declares it.")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="checks.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inputs", nargs="*", type=Path,
                        help="files to evaluate; defaults to the bundled fixtures")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST,
                        help="check manifest JSON (default: the bundled release-note gate)")
    parser.add_argument("--artifact-type", default="memory",
                        choices=["memory", "skill", "script", "tool"],
                        help="artifact type the manifest is applied as (default: memory)")
    parser.add_argument("--format", choices=["json", "text"], default="json",
                        help="machine result (default) or a human summary")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = tuple(args.inputs) if args.inputs else DEFAULT_INPUTS
    result = check_paths(args.manifest, paths, artifact_type=args.artifact_type)
    print(json.dumps(result, sort_keys=True, indent=2) if args.format == "json"
          else render_text(result))
    return exit_status(result)


if __name__ == "__main__":
    sys.exit(main())
