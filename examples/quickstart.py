"""Execute from a source checkout after installing doublegate-sdk."""
import json
from pathlib import Path

from doublegate_sdk.package import load_package
from doublegate_sdk.runtime import evaluate

manifest = Path(__file__).resolve().parent / "gates/runbook/gate.json"
loaded = load_package(manifest)
for content in ("Owner: Operations", "No owner recorded"):
    result = evaluate(loaded.package, content, "memory")
    print(json.dumps(result.to_payload() | {"human_review": loaded.package.human_review}, sort_keys=True))
# Neither result authorizes publication or satisfies human review.
