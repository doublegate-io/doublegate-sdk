"""Importable Python recipes for the Doublegate SDK.

Two modules, both small and both plain Python:

* :mod:`recipes.offline_checks` — deterministic ``evaluate_file`` work. No
  network, no gate, no credential.
* :mod:`recipes.gate_operations` — gate operations over a client **the caller
  supplies**. Nothing here calls :func:`doublegate_sdk.connect`, so a recipe
  can never open a connection you did not ask for.

Every function returns a value. None of them prints, none of them exits, and
none of them retries a write. They are meant to be read, copied, and called
from your own code — not subclassed or configured.
"""

__all__ = ["gate_operations", "offline_checks"]
