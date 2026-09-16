"""Execute from a source checkout after installing doublegate-sdk.

One call applies a check manifest to a file and returns evidence about it.
Nothing here connects to a gate, and neither outcome authorizes publication.
"""
import json
from pathlib import Path

from doublegate_sdk import evaluate_file

gates = Path(__file__).resolve().parent / "gates/runbook"
manifest = gates / "gate.json"
for content_path in (gates / "pass.txt", Path(__file__)):
    evaluation = evaluate_file(manifest, content_path, artifact_type="memory")
    print(json.dumps(evaluation.to_payload(), sort_keys=True))
# `human_review` stays "required" in both payloads: a clean check is evidence
# that the manifest's literal strings matched, not a completed review.
