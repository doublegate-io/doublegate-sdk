"""Structural wire tests, deliberately not authentication/authority tests.

Data materialized from the executed design fixtures; tests need no sibling repo.
"""
import importlib.util
import json
from pathlib import Path

import pytest

DATA = json.loads((Path(__file__).parent / 'data/identity-contract.json').read_text())
POSITIVE = [c for c in DATA['cases'] if c['category'] == 'shape_positive']


def identity():
    from doublegate_sdk import identity
    return identity


def test_attribution_contract_entrypoint_exists():
    import doublegate_sdk
    assert importlib.util.find_spec('doublegate_sdk.identity') is not None


@pytest.mark.parametrize('case', POSITIVE, ids=lambda c: c['name'])
def test_positive_payload_roundtrip(case):
    api = identity()
    parsed = api.validate_attribution(case['payload'])
    assert isinstance(parsed, api.AttributionV1)
    assert parsed.to_dict() == case['payload']
    assert api.decode_attribution(parsed.canonical_bytes()).to_dict() == case['payload']


NEGATIVE = [c for c in DATA['cases'] if c['category'] == 'shape_negative']


@pytest.mark.parametrize('case', NEGATIVE, ids=lambda c: c['name'])
def test_negative_payload(case):
    with pytest.raises(identity().AttributionError):
        identity().validate_attribution(case['payload'])


@pytest.mark.parametrize('raw', DATA['strict_negative'])
def test_strict_decoding(raw):
    with pytest.raises(identity().AttributionError):
        identity().decode_attribution(raw.encode())


@pytest.mark.parametrize('change', [
    {'grant_refs': {'requester': [{'grant_id': 'same', 'grant_digest': 'a'*64},
                    {'grant_id': 'same', 'grant_digest': 'b'*64}],
                    'actor': POSITIVE[0]['payload']['grant_refs']['actor']}},
    {'membership_observed_at': 1800000002},
    {'attribution_version': 1.0}, {'actor_id': '\ud800'},
])
def test_pure_semantic_rejections(change):
    with pytest.raises(identity().AttributionError):
        identity().validate_attribution({**POSITIVE[0]['payload'], **change})


def test_noncanonical_payload_and_immutable_result():
    api = identity()
    payload = POSITIVE[0]['payload']
    with pytest.raises(api.AttributionError):
        api.decode_attribution(json.dumps(payload).encode())
    parsed = api.validate_attribution(payload)
    copy = parsed.to_dict()
    copy['grant_refs']['requester'][0]['grant_id'] = 'changed'
    assert parsed.to_dict() == payload
