# Python quickstart

From the standalone repository root, create a virtual environment and install the
local distribution. No published package is required:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install .
python examples/quickstart.py
```

The executable example is reproduced below:

```python
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
```

The first result is `clean`, the second `flagged`; both retain `human_review:
required`. Use `load_package` for bounded POSIX file loading, or
`validate_manifest` when you already have parsed trusted JSON data in memory.
Duplicate-key detection is a property of file loading, not of an already parsed
dictionary. Neither function grants installation or publication authority.
