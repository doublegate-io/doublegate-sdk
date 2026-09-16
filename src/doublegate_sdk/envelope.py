"""Envelope v1 — the atomic unit everywhere downstream.

Normative source: ADR-0022 (design/docs/adr/ADR-0022-envelope-v1-frozen.md).
This module IS that table. If the two ever disagree, the ADR wins and this
module is the bug.

Rules that make ``envelope_digest`` a stable hash:

* canonical JSON: sorted keys, UTF-8, no whitespace, no null values emitted
  (absent means absent);
* ``envelope_digest`` is ``sha256`` of the canonical envelope and is NOT a field —
  the envelope cannot contain its own hash;
* lifecycle state (``state``, ``provisional``, ``content_erased``, verdicts,
  findings) lives in the store, never in the envelope, so erasure never
  changes an id;
* v2 may ADD optional fields; it may not remove, rename or retype a v1 field.
  ``envelope_version`` selects the canonicalization rules, so v1 ids stay valid
  forever.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

ENVELOPE_VERSION = 1

TRUST_CLASSES = frozenset({"T-0", "T-1", "T-2", "T-3", "T-4", "T-5"})

# Field registry: name -> (required, python type(s)).
# Order is irrelevant for hashing (canonical form sorts keys); it is kept in
# ADR table order for readability only.
_FIELDS: dict[str, tuple[bool, tuple[type, ...]]] = {
    "envelope_version": (True, (int,)),
    "content_hash": (True, (str,)),
    "content_type": (True, (str,)),
    "trust_class": (True, (str,)),
    "source_uri": (True, (str,)),
    "writer_identity": (True, (str,)),
    "deployment_id": (True, (str,)),
    "ingest_ts": (True, (int,)),
    "size_bytes": (True, (int,)),
    "derives_from": (False, (list,)),
    "content_key_id": (False, (str,)),  # F7: schema present from M1, tooling M4
}
REQUIRED_FIELDS = frozenset(k for k, (req, _) in _FIELDS.items() if req)
OPTIONAL_FIELDS = frozenset(k for k, (req, _) in _FIELDS.items() if not req)
ALL_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class EnvelopeError(ValueError):
    """The envelope violates ADR-0022. Message names the field."""


@dataclass(frozen=True, slots=True)
class Envelope:
    """An admitted artifact's immutable description.

    Frozen: the envelope never changes after admission — that is what lets
    ``envelope_digest`` be a hash of it. Build one via :func:`make_envelope` or
    :meth:`Envelope.from_mapping`, never by editing attributes.
    """

    content_hash: str
    content_type: str
    trust_class: str
    source_uri: str
    writer_identity: str
    deployment_id: str
    ingest_ts: int
    size_bytes: int
    derives_from: tuple[str, ...] = field(default=())
    content_key_id: str | None = None
    envelope_version: int = ENVELOPE_VERSION

    # ---- construction ----------------------------------------------------

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any]) -> Envelope:
        """Validate a mapping against ADR-0022 and build an Envelope.

        Unknown keys are rejected: a v1 reader must not silently accept fields
        it cannot hash consistently. (A v2 reader will know v2's fields.)
        """
        unknown = set(m) - ALL_FIELDS
        if unknown:
            raise EnvelopeError(f"unknown field(s): {sorted(unknown)}")
        missing = REQUIRED_FIELDS - set(m)
        if missing:
            raise EnvelopeError(f"missing required field(s): {sorted(missing)}")
        for name, (_, types) in _FIELDS.items():
            if name in m:
                v = m[name]
                if v is None:
                    raise EnvelopeError(f"{name}: null is not allowed; omit the field")
                # bool is an int subclass; reject it for int fields explicitly
                if isinstance(v, bool) and int in types:
                    raise EnvelopeError(f"{name}: expected int, got bool")
                if not isinstance(v, types):
                    raise EnvelopeError(f"{name}: expected {types[0].__name__}, got {type(v).__name__}")
        env = cls(
            content_hash=m["content_hash"],
            content_type=m["content_type"],
            trust_class=m["trust_class"],
            source_uri=m["source_uri"],
            writer_identity=m["writer_identity"],
            deployment_id=m["deployment_id"],
            ingest_ts=m["ingest_ts"],
            size_bytes=m["size_bytes"],
            derives_from=tuple(m.get("derives_from", ())),
            content_key_id=m.get("content_key_id"),
            envelope_version=m["envelope_version"],
        )
        env.validate()
        return env

    def validate(self) -> None:
        """Semantic checks beyond type: version, hash shape, class, sizes."""
        if self.envelope_version != ENVELOPE_VERSION:
            raise EnvelopeError(
                f"envelope_version: this reader knows v{ENVELOPE_VERSION}, got v{self.envelope_version}"
            )
        if not _SHA256_HEX.fullmatch(self.content_hash):
            raise EnvelopeError("content_hash: must be lowercase sha256 hex (64 chars)")
        if self.trust_class not in TRUST_CLASSES:
            raise EnvelopeError(f"trust_class: {self.trust_class!r} not in T-0..T-5")
        for s in ("content_type", "source_uri", "writer_identity", "deployment_id"):
            if not getattr(self, s):
                raise EnvelopeError(f"{s}: must be non-empty")
        if self.ingest_ts < 0:
            raise EnvelopeError("ingest_ts: must be non-negative epoch milliseconds")
        if self.size_bytes < 0:
            raise EnvelopeError("size_bytes: must be non-negative")
        for parent in self.derives_from:
            if not isinstance(parent, str) or not _SHA256_HEX.fullmatch(parent):
                raise EnvelopeError("derives_from: every entry must be an artifact_id (sha256 hex)")
        if self.content_key_id is not None and not self.content_key_id:
            raise EnvelopeError("content_key_id: must be non-empty when present")

    # ---- canonical form -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Plain mapping with NO null values and NO empty optional list.

        ``derives_from`` is emitted only when non-empty, ``content_key_id``
        only when set — "absent means absent" (ADR-0022 decision 1). An empty
        list and an absent list must hash identically, so we never emit ``[]``.
        """
        d: dict[str, Any] = {
            "envelope_version": self.envelope_version,
            "content_hash": self.content_hash,
            "content_type": self.content_type,
            "trust_class": self.trust_class,
            "source_uri": self.source_uri,
            "writer_identity": self.writer_identity,
            "deployment_id": self.deployment_id,
            "ingest_ts": self.ingest_ts,
            "size_bytes": self.size_bytes,
        }
        if self.derives_from:
            d["derives_from"] = list(self.derives_from)
        if self.content_key_id is not None:
            d["content_key_id"] = self.content_key_id
        return d

    def canonical_bytes(self) -> bytes:
        """The exact bytes that are hashed. Sorted keys, no whitespace, UTF-8."""
        return canonical_json(self.to_dict())

    @property
    def envelope_digest(self) -> str:
        """sha256 of the canonical envelope. Not a field (ADR-0022 decision 2)."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def canonical_json(obj: Any) -> bytes:
    """Canonical JSON per ADR-0022: sorted keys, no whitespace, UTF-8, no NaN.

    ``ensure_ascii=False`` so non-ASCII content hashes as its UTF-8 bytes, not
    as ``\\uXXXX`` escapes — the escaped form is not canonical across encoders.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def content_hash(blob: bytes) -> str:
    """sha256 of a content blob — the value that goes in ``content_hash``."""
    return hashlib.sha256(blob).hexdigest()


def make_envelope(
    *,
    blob: bytes,
    content_type: str,
    trust_class: str,
    source_uri: str,
    writer_identity: str,
    deployment_id: str,
    ingest_ts: int,
    derives_from: tuple[str, ...] | list[str] = (),
    content_key_id: str | None = None,
) -> Envelope:
    """Build a validated v1 envelope from a content blob and its provenance.

    ``content_hash`` and ``size_bytes`` are derived from ``blob`` so they
    cannot disagree with it.
    """
    env = Envelope(
        content_hash=content_hash(blob),
        content_type=content_type,
        trust_class=trust_class,
        source_uri=source_uri,
        writer_identity=writer_identity,
        deployment_id=deployment_id,
        ingest_ts=ingest_ts,
        size_bytes=len(blob),
        derives_from=tuple(derives_from),
        content_key_id=content_key_id,
    )
    env.validate()
    return env
