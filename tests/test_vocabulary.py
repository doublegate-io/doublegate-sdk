"""The blocker vocabulary is closed, and both gates must agree on what is in it.

A hold names what is actually blocking an artifact. The contract fixes that list at nine
values and says multiple may coexist. Two gates and the SDK each need the list; if they keep
their own copies the copies drift, and a blocker one side emits becomes a blocker the other
side cannot render.
"""

from __future__ import annotations

import pytest

from doublegate_sdk.vocabulary import BLOCKERS, normalise_blockers


def test_the_vocabulary_is_the_contract_s_nine_values():
    """Quoted from 05-decision-authority.md:240-242. A tenth value is a contract change."""
    assert BLOCKERS == frozenset({
        "hard_constraint",
        "semantic_review",
        "needs_evidence",
        "quorum",
        "reviewer_unavailable",
        "human_required",
        "human_directive",
        "authority_conflict",
        "retry_exhausted",
    })


def test_multiple_blockers_may_coexist():
    """The contract says so explicitly, so the carrier is a collection, not a single value."""
    assert normalise_blockers(["quorum", "needs_evidence"]) == ("needs_evidence", "quorum")


def test_blockers_come_back_in_a_stable_order():
    """Two holds blocked on the same things must serialise identically.

    Asserting the exact sorted sequence, not just that two calls agree with each other:
    within one process two sets built from the same strings iterate the same way, so
    comparing two calls passes even when nothing sorts anything.
    """
    assert normalise_blockers(["quorum", "needs_evidence", "hard_constraint"]) == (
        "hard_constraint",
        "needs_evidence",
        "quorum",
    )


def test_a_duplicate_blocker_is_not_two_blockers():
    assert normalise_blockers(["quorum", "quorum"]) == ("quorum",)


def test_a_value_outside_the_nine_is_refused():
    """An unlisted blocker must not reach a consumer that cannot render it."""
    with pytest.raises(ValueError) as excinfo:
        normalise_blockers(["submission_authority_unavailable"])
    assert "submission_authority_unavailable" in str(excinfo.value)


def test_an_empty_blocker_list_is_refused():
    """A hold with nothing blocking it is a bug to surface, not a value to render."""
    with pytest.raises(ValueError):
        normalise_blockers([])


def test_a_bare_string_is_refused_rather_than_iterated_as_characters():
    """`normalise_blockers("quorum")` must not silently become six one-character blockers."""
    with pytest.raises(TypeError):
        normalise_blockers("quorum")
