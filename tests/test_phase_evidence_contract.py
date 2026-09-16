"""Explicit signed reference profile; parsing is never authorization."""
from copy import deepcopy

import pytest

from doublegate_sdk.identity import AttributionError, validate_attribution
from test_identity_wire import PAYLOAD

KINDS = ('membership', 'mapping', 'relationship', 'policy', 'attester_enrollment')


def payload():
    value = deepcopy(PAYLOAD)
    value['identity_refs'] = {kind: {'revision': f'{kind}-{i}', 'digest': f'{i:x}' * 64}
                              for i, kind in enumerate(KINDS, 1)}
    value['authority_ref'] = {'revision': 17, 'digest': 'a' * 64}
    value['grant_refs'] = {role: [{'grant_id': role, 'grant_digest': digest * 64}]
                           for role, digest in [('requester', 'b'), ('actor', 'c')]}
    return value


def test_independent_identity_refs_and_role_chains_are_the_only_shape():
    claims = payload()
    assert validate_attribution(claims).to_dict() == claims
    assert len({r['revision'] for r in claims['identity_refs'].values()}) == 5
    for old in ('membership_revision', 'mapping_revision', 'policy_revision', 'authority_revision'):
        with pytest.raises(AttributionError):
            validate_attribution({**claims, old: 'old'})


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('fault', ['missing', 'empty_revision', 'bad_digest', 'unknown', 'list'])
def test_each_identity_reference_is_required_closed_and_exact(kind, fault):
    claims = payload()
    ref = claims['identity_refs'][kind]
    if fault == 'missing': del claims['identity_refs'][kind]
    elif fault == 'empty_revision': ref['revision'] = ''
    elif fault == 'bad_digest': ref['digest'] = 'a' * 64 + '\n'
    elif fault == 'unknown': ref['allow'] = True
    elif fault == 'list': claims['identity_refs'][kind] = [ref]
    with pytest.raises(AttributionError):
        validate_attribution(claims)


@pytest.mark.parametrize('role', ['requester', 'actor'])
@pytest.mark.parametrize('fault', ['missing', 'empty', 'duplicate', 'over_depth'])
def test_each_role_has_its_own_bounded_nonempty_chain(role, fault):
    claims = payload()
    if fault == 'missing': del claims['grant_refs'][role]
    elif fault == 'empty': claims['grant_refs'][role] = []
    elif fault == 'duplicate': claims['grant_refs'][role] *= 2
    else: claims['grant_refs'][role] = [dict(grant_id=str(i), grant_digest='b'*64) for i in range(9)]
    with pytest.raises(AttributionError):
        validate_attribution(claims)


@pytest.mark.parametrize('revision', [True, 1.0, '17', 0, -1, 9007199254740992])
def test_authority_revision_is_positive_safe_integer_not_identity_revision(revision):
    claims = payload()
    claims['authority_ref']['revision'] = revision
    with pytest.raises(AttributionError):
        validate_attribution(claims)
