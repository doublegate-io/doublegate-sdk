"""A skip names why, from a closed set, or it is the silence this module exists to end.

The enumeration is derived from the skips that exist in `client-gate`'s integrator today,
not from imagination. Two of those skips currently write free-text strings and two emit
nothing at all; all four are the same event — a reducer pass declined to act on an item —
and all four get a code here.

The case worth pinning is that absence is *not* a legal answer, which is where this parts
company with the reason categories it sits beside. `decision-cards.md:1065` lists "any
silent skip" under what proves the design does not work. A skip with no reason is that
silence, so the type refuses it rather than carrying it.
"""

import pytest

from doublegate_sdk.skipped import SKIPPED_REASONS, normalise_skipped_reason


def test_the_enumeration_is_the_four_skips_the_code_actually_performs() -> None:
    """Guards the closed list in both directions.

    Each member answers to a skip path read in `client-gate/src/doublegate/integrator.py`:
    the duplicate at `:216`, the unwritable surface at `:240`, the `last_promote is None`
    continue, and the unserved branch. A fifth member appearing here without a fifth skip
    path is the `other` bucket arriving under a better name.
    """
    assert SKIPPED_REASONS == frozenset(
        {"duplicate", "surface_unwritable", "never_promoted", "not_served"}
    )


def test_no_reason_is_not_an_answer() -> None:
    """The difference from the reason categories next door, and it comes from the contract.

    `ADR-0053:320-321` lets a stage read return "no category at all", so
    `normalise_category` passes ``None`` through. Nothing grants a skip the same licence.
    The opposite: `decision-cards.md:1065` lists "any silent skip" among the things that
    prove this design does not work, and a skip carrying no reason is exactly that skip.

    So absence raises here and passes there. The two functions look alike and disagree on
    this one point because their contracts disagree on it, not because of taste.
    """
    with pytest.raises(ValueError) as excinfo:
        normalise_skipped_reason(None)

    assert "silent" in str(excinfo.value)


def test_an_interpolated_code_is_refused() -> None:
    """The defect this enumeration exists to make impossible, stated as a case.

    `client-gate/src/doublegate/integrator.py:240` builds its reason as
    ``f"skill:{d}:unwritable:{e.__class__.__name__}"``. The exception class name is
    interpolated into the code, so the set of values that line can emit is the set of
    exception classes `OSError` can be — a set nobody can write down, which means no
    consumer can enumerate what it must accept.

    It is only not-broken today because `:315` discards every reason and keeps the count.
    `organization-gate` is the same shape with a consumer attached, and there every
    held artifact reports ``unstated``.

    So the code is a member or it is refused. Detail belongs in a field beside it.
    """
    with pytest.raises(ValueError):
        normalise_skipped_reason("surface_unwritable:PermissionError")


def test_the_refusal_names_what_is_permitted() -> None:
    """A refusal a caller cannot act on just moves the confusion.

    The message carries all four so whoever hit it can see the nearest legitimate value
    without going to the card.
    """
    with pytest.raises(ValueError) as excinfo:
        normalise_skipped_reason("busy")

    message = str(excinfo.value)
    for permitted in SKIPPED_REASONS:
        assert permitted in message


def test_there_is_no_other_bucket() -> None:
    """`decision-cards.md:1061-1062` names this as the way the design rots.

    A closed list under pressure grows an `other` member that absorbs everything and
    tells nobody anything. The card's stop condition at `:1071-1072` is the alternative:
    if the set cannot stay closed and meaningful, ship a boolean skipped flag and no
    reason. The escape hatch is dropping the vocabulary, never widening it.
    """
    for catch_all in ("other", "unknown", "unstated", "misc"):
        with pytest.raises(ValueError):
            normalise_skipped_reason(catch_all)


def test_each_permitted_value_survives_unchanged() -> None:
    """The check refuses on violation, not on principle.

    Asserted per value rather than over the set, so that a member the function rejects
    is a failure here and not something a set comparison rounds off.
    """
    for permitted in ("duplicate", "surface_unwritable", "never_promoted", "not_served"):
        assert normalise_skipped_reason(permitted) == permitted
