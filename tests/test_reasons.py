"""The reason-category enumeration is closed, and an empty answer is a valid one.

`ADR-0053-scan-then-return.md:309-321` fixes the categories at five values and states
the list is closed: "Adding a sixth category is a *decision*, recorded here as such, and
not an implementation choice an executor may make while building a surface."

The same paragraph carries the part that is easy to miss: "A stage read returns one of
these five **or it returns no category at all**." So unlike the blocker vocabulary —
where an empty set means a hold that cannot say what it waits for, and raises — silence
here is a legitimate answer. Withholding a category is what the gate does when it has
not formed one, and a type that forces a value would make it invent one.

These cases pin both halves: the five are exactly the five, and absence survives.
"""

import pytest

from doublegate_sdk.reasons import CATEGORIES, normalise_category


def test_the_enumeration_is_the_contract_s_five_values() -> None:
    """Guards the closed list in both directions.

    A sixth value appearing here means someone made a decision in code that
    `ADR-0053:317` reserves for the register. A missing one means a submitter stops
    being told something the contract says they are told.
    """
    assert CATEGORIES == frozenset(
        {"off-scope", "quality", "policy", "duplicate", "unreadable"}
    )


def test_no_category_is_a_valid_answer() -> None:
    """`ADR-0053:320-321` — "or it returns no category at all".

    The gate is allowed to have formed no view. Returning ``None`` unchanged is how it
    says so; the alternative is a type that makes silence unrepresentable and pushes
    callers into inventing a filler value.
    """
    assert normalise_category(None) is None


def test_a_value_outside_the_five_is_refused() -> None:
    """Refuse rather than drop.

    Dropping an unrecognised category silently turns "we disagree about why this was
    refused" into "no reason was given", which is the same erasure the closed list
    exists to prevent.
    """
    with pytest.raises(ValueError) as excinfo:
        normalise_category("wrong scope")

    assert "wrong scope" in str(excinfo.value)


def test_the_refusal_names_what_is_permitted() -> None:
    """A refusal a caller cannot act on just moves the confusion.

    The message carries all five so whoever hit it can see the nearest legitimate
    value without going to the register.
    """
    with pytest.raises(ValueError) as excinfo:
        normalise_category("spam")

    message = str(excinfo.value)
    for permitted in CATEGORIES:
        assert permitted in message


def test_a_near_miss_is_still_a_miss() -> None:
    """`off_scope` is not `off-scope`, and guessing between them is not this layer's job.

    The separator is the kind of detail that drifts between a wire format and a Python
    identifier. Normalising it here would hide the drift instead of surfacing it at the
    boundary where someone can fix it.
    """
    with pytest.raises(ValueError):
        normalise_category("off_scope")


def test_a_permitted_value_survives_unchanged() -> None:
    """The check refuses on violation, not on principle."""
    assert normalise_category("duplicate") == "duplicate"
