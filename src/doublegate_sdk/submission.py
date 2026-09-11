"""Submission canonicalization and the two identities of ADR-0068.

Normative sources, read at design main ``72754b68``:

* `P1-A/01 §2 <https://github.com/doublegate-io/design>`_ — canonical bytes are
  RFC 8785 JCS narrowed to a profile with no implementation choices left in it.
* **ADR-0068 D2** — content identity is ``content_digest``, the ``sha256:``
  digest of the submitted content bytes. Many submissions share one.
* **ADR-0068, derivation clarification 2026-09-11** — ``artifact_id`` is
  ``sha256`` of the submission's *own* canonical bytes and is **not a member**
  of the signed document. A verifier recomputes it from the received bytes and
  compares to the route; it never reads a carried assertion.

Stdlib only, on purpose: the SDK declares ``dependencies = []`` and this module
must not be the thing that changes that.

The narrowing is refusal-shaped throughout. Where a permissive serializer would
normalize (NFC), round (floats) or widen (big integers), this module raises —
normalizing would silently change bytes a signature covers, which is the whole
reason the profile exists.

Not here, and deliberately: Ed25519 signing, membership assertions and the four
format-dependent refusal codes. This module owns the *format* and the
*identity*, never the signature.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "ENCODING_INVALID",
    "ARTIFACT_ID_MISMATCH",
    "MAX_SAFE_INTEGER",
    "CanonicalizationRefused",
    "RouteIdMismatch",
    "canonical_bytes",
    "content_digest",
    "submission_artifact_id",
    "verify_route_artifact_id",
]

#: P1-A/01 §4. One refusal names exactly one code.
ENCODING_INVALID = "SUBMISSION_ENCODING_INVALID"
ARTIFACT_ID_MISMATCH = "SUBMISSION_ARTIFACT_ID_MISMATCH"

#: §2.4 — integers in ``[0, 2**53-1]``. Above it, a JSON reader elsewhere may
#: round, and a rounded id is a different id.
MAX_SAFE_INTEGER = 2 ** 53 - 1

SHA256_ID = re.compile(r"^sha256:[0-9a-f]{64}$")

# §2.3 — escape ONLY these, and never anything else. Short forms where they
# exist; lowercase ``\u00xx`` otherwise.
_SHORT = {0x08: "\\b", 0x09: "\\t", 0x0A: "\\n", 0x0C: "\\f", 0x0D: "\\r",
          0x22: '\\"', 0x5C: "\\\\"}


class CanonicalizationRefused(ValueError):
    """The document breaks the §2 profile. Carries the contract's code."""

    code = ENCODING_INVALID

    def __init__(self, message: str) -> None:
        super().__init__(message)


class RouteIdMismatch(ValueError):
    """The route id is not ``sha256`` of the received canonical bytes."""

    code = ARTIFACT_ID_MISMATCH

    def __init__(self, message: str) -> None:
        super().__init__(message)


def _string(s: str, *, what: str) -> str:
    """Serialize one string: NFC-checked, minimally escaped (§2.1, §2.3)."""
    try:
        s.encode("utf-8")
    except UnicodeEncodeError as e:          # lone surrogate: not UTF-8 at all
        raise CanonicalizationRefused(f"{what}: not encodable as UTF-8") from e
    if unicodedata.normalize("NFC", s) != s:
        # Refused, NOT normalized: normalizing changes bytes a signature covers.
        raise CanonicalizationRefused(f"{what}: string is not NFC; it is refused, not normalized")
    out = ['"']
    for ch in s:
        o = ord(ch)
        if o in _SHORT:
            out.append(_SHORT[o])
        elif o < 0x20:
            out.append(f"\\u{o:04x}")        # lowercase hex, §2.3
        else:
            out.append(ch)                   # never escape what needs no escape
    out.append('"')
    return "".join(out)


def _member_sort_key(name: str) -> tuple[int, ...]:
    """§2.2 — sort by member name as a sequence of UTF-16 code units.

    Python orders ``str`` by code point, which disagrees with UTF-16 for every
    astral character: U+10000 leads with the surrogate 0xD800 and therefore
    sorts *before* U+E000..U+FFFF, not after. JCS says UTF-16, so we say UTF-16.
    """
    raw = name.encode("utf-16-be")
    return tuple(int.from_bytes(raw[i:i + 2], "big") for i in range(0, len(raw), 2))


def _value(v: Any, *, path: str) -> str:
    if v is None:
        return "null"                        # §2.5 — a real value, not absence
    if v is True:
        return "true"
    if v is False:
        return "false"                       # §2.6 — no other spelling
    if isinstance(v, int):
        if v < 0:
            raise CanonicalizationRefused(f"{path}: negative integer; no sign is permitted")
        if v > MAX_SAFE_INTEGER:
            raise CanonicalizationRefused(f"{path}: integer exceeds 2**53-1")
        return str(v)
    if isinstance(v, float):
        # §2.4 — refused outright rather than rounded.
        raise CanonicalizationRefused(f"{path}: floating point is refused, not rounded")
    if isinstance(v, str):
        return _string(v, what=path)
    if isinstance(v, Mapping):
        return _object(v, path=path)
    if isinstance(v, (list, tuple)) and isinstance(v, Sequence):
        # §2.7 — order is significant and preserved exactly as submitted.
        return "[" + ",".join(_value(x, path=f"{path}[{i}]") for i, x in enumerate(v)) + "]"
    raise CanonicalizationRefused(f"{path}: {type(v).__name__} has no canonical JSON form")


def _object(m: Mapping[str, Any], *, path: str) -> str:
    names = []
    for k in m:
        if not isinstance(k, str):
            raise CanonicalizationRefused(f"{path}: member name must be a string")
        names.append(k)
    names.sort(key=_member_sort_key)
    parts = [f"{_string(k, what=f'{path}.{k} (member name)')}:{_value(m[k], path=f'{path}.{k}')}"
             for k in names]
    return "{" + ",".join(parts) + "}"


def canonical_bytes(doc: Mapping[str, Any]) -> bytes:
    """The one byte string this document has under the §2 profile.

    UTF-8, no BOM, no insignificant whitespace, members ordered by UTF-16 code
    unit. Raises :class:`CanonicalizationRefused` rather than normalizing.
    """
    if not isinstance(doc, Mapping):
        raise CanonicalizationRefused("a submission is one JSON object")
    return _object(doc, path="$").encode("utf-8")


def content_digest(content: bytes) -> str:
    """ADR-0068 D2 — the **content** identity: ``sha256:`` + 64 lowercase hex.

    This is what duplicate detection, change detection and re-fetch suppression
    are keyed on. It is deliberately *not* unique per record: two submissions of
    the same bytes share it, and that shared value is the relation those
    capabilities observe.
    """
    if not isinstance(content, (bytes, bytearray, memoryview)):
        raise TypeError("content_digest hashes the document bytes")
    return "sha256:" + hashlib.sha256(bytes(content)).hexdigest()


def submission_artifact_id(doc: Mapping[str, Any]) -> str:
    """ADR-0068 — the **submission event** identity.

    ``sha256`` of this document's own canonical bytes, spelled ``sha256:`` + 64
    lowercase hex. The document must not carry ``artifact_id``: a document
    computed over its own bytes cannot contain its own hash, which is why the
    clarification took it out of the closed member set (ten members, not eleven).
    """
    if "artifact_id" in doc:
        raise CanonicalizationRefused(
            "artifact_id is not a member of the signed document (ADR-0068 clarification); "
            "it is sha256 of the document's own canonical bytes")
    return "sha256:" + hashlib.sha256(canonical_bytes(doc)).hexdigest()


def verify_route_artifact_id(route_id: str, doc: Mapping[str, Any]) -> str:
    """Recompute the id from the received body and refuse on mismatch.

    This is step 6 of the clarification's implementer sequence, and the reason
    the route segment needs no signature of its own: the id is a pure function
    of the signed bytes, so tampering with either side moves the comparison.
    """
    if not isinstance(route_id, str) or not SHA256_ID.match(route_id):
        raise RouteIdMismatch("route artifact_id must be sha256: + 64 lowercase hex")
    computed = submission_artifact_id(doc)
    if route_id != computed:
        raise RouteIdMismatch("route artifact_id does not equal sha256 of the received canonical bytes")
    return computed
