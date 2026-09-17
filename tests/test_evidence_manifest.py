"""The production evidence manifest digest: closed shape, exact canonical bytes."""
import hashlib

import pytest

from doublegate_sdk.identity_wire import evidence_manifest_digest

MANIFEST = {'manifest_version': 1, 'evidence_refs': ['a' * 64, 'b' * 64]}


def test_evidence_manifest_exact_canonical_digest():
    expected = hashlib.sha256(
        ('{"evidence_refs":["' + 'a' * 64 + '","' + 'b' * 64 +
         '"],"manifest_version":1}').encode()).hexdigest()
    assert evidence_manifest_digest(MANIFEST) == expected


@pytest.mark.parametrize('manifest', [None, {}, [],
    {**MANIFEST, 'extra': 0}, {**MANIFEST, 'manifest_version': True},
    {**MANIFEST, 'manifest_version': 1.0}, {**MANIFEST, 'manifest_version': 2},
    {**MANIFEST, 'evidence_refs': []}, {**MANIFEST, 'evidence_refs': ['b' * 64, 'a' * 64]},
    {**MANIFEST, 'evidence_refs': ['a' * 64, 'a' * 64]},
    {**MANIFEST, 'evidence_refs': ['A' * 64]},
    {**MANIFEST, 'evidence_refs': ['a' * 64 + '\n']},
    {**MANIFEST, 'evidence_refs': [None]}, {**MANIFEST, 'evidence_refs': 'a' * 64},
])
def test_rejects_invalid_manifest(manifest):
    with pytest.raises(ValueError):
        evidence_manifest_digest(manifest)


def test_the_attribution_sidecar_is_gone():
    """ADR-0082 d1, d7: no statement is signed, so nothing here decodes one."""
    from doublegate_sdk import identity_wire
    for name in ('AttributionV1', 'validate_attribution', 'verify_attribution',
                 'decode_attribution', 'decode_attribution_jws', 'decode_attribution_chain',
                 'AttributionBinding', 'ParsedAttributionChain'):
        assert not hasattr(identity_wire, name), name
    with pytest.raises(ImportError):
        import doublegate_sdk.identity  # noqa: F401
