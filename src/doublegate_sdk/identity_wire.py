"""The production evidence manifest digest, and the shape checks the submission
contract runs.

Everything else this module held was the ADR-0064 attribution sidecar — the
statement payload, the JWS decoding and the two-statement chain. ADR-0082 d1 and
d7 end signed attribution: a hop is a program acting for a named person, the
``on_behalf_of`` record on the body, and no statement is signed. What is left is
content integrity, which the chain never was: the digest of the closed evidence
manifest a submission event commits to.

A digest is not verification of the evidence bytes, and none of these checks
authenticates anyone.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from doublegate_sdk.envelope import canonical_json


class ShapeError(ValueError):
    """Malformed wire data (never an authentication or authority decision)."""


def _identifier(value: Any) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= 512:
        raise ShapeError('identifier must contain 1–512 Unicode code points')
    try:
        value.encode('utf-8')
    except UnicodeError as exc:
        raise ShapeError('invalid Unicode') from exc


def _digest(value: Any) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(
            c not in '0123456789abcdef' for c in value):
        raise ShapeError('digest must be lowercase SHA-256 hex')


def _closed(value: Any, required: set | frozenset,
            optional: set | frozenset = frozenset()) -> None:
    if not isinstance(value, Mapping) or not required <= value.keys() or (
            value.keys() - required - optional):
        raise ShapeError('object has missing or unknown fields')


def evidence_manifest_digest(manifest: dict) -> str:
    """Hash the closed production manifest; does NOT check retained evidence bytes."""
    _closed(manifest, {'manifest_version', 'evidence_refs'})
    if type(manifest['manifest_version']) is not int or manifest['manifest_version'] != 1:
        raise ShapeError('manifest_version must be integer 1')
    refs = manifest['evidence_refs']
    if not isinstance(refs, list) or not refs:
        raise ShapeError('evidence_refs must be a nonempty list')
    for ref in refs:
        _digest(ref)
    if refs != sorted(set(refs)):
        raise ShapeError('evidence_refs must be sorted and unique')
    return hashlib.sha256(canonical_json(dict(manifest))).hexdigest()
