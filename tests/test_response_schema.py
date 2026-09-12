"""A response schema is the full shape, and these cases say what that costs a producer.

The two defects behind this module are both a producer and a consumer disagreeing about
a field's permitted values with nothing checking. So the cases that matter most here are
the closed-set ones: they are the ones that would have failed before either defect
shipped.
"""

from doublegate_sdk.schema import Field, ResponseSchema, check_response

BLOCKERS = frozenset({"quorum", "human_required", "authority_conflict"})


def test_a_payload_of_exactly_the_declared_shape_has_no_violations() -> None:
    """The checker refuses on departure, not on principle."""
    schema = ResponseSchema("hold", (Field("artifact_id", str), Field("state", str)))
    assert check_response(schema, {"artifact_id": "a1", "state": "held"}) == ()


def test_an_undeclared_field_is_a_violation_naming_it() -> None:
    """An extra field is not a harmless extra.

    The moment it is emitted some consumer may read it, and from then on the producer is
    committed to a field nobody agreed to. The schema is the only point where removing it
    is still cheap.
    """
    schema = ResponseSchema("hold", (Field("artifact_id", str),))
    violations = check_response(schema, {"artifact_id": "a1", "queue_position": 3})
    assert len(violations) == 1
    assert "queue_position" in violations[0]
    assert "undeclared" in violations[0]


def test_a_missing_declared_field_is_a_violation_naming_it() -> None:
    """A consumer reading a field the producer stopped sending is the mirror failure."""
    schema = ResponseSchema("hold", (Field("artifact_id", str), Field("state", str)))
    violations = check_response(schema, {"artifact_id": "a1"})
    assert len(violations) == 1
    assert "state" in violations[0]
    assert "missing" in violations[0]


def test_a_value_outside_a_closed_set_is_a_violation() -> None:
    """This is the case that would have caught the hold-reason defect.

    A producer emitted values the consumer's accepted set did not contain, and because
    nothing compared the two, every held artifact degraded to `unstated` instead of
    failing where the disagreement was.
    """
    schema = ResponseSchema("hold", (Field("blocker", str, allowed=BLOCKERS),))
    violations = check_response(schema, {"blocker": "shared_backend"})
    assert len(violations) == 1
    assert "shared_backend" in violations[0]


def test_the_closed_set_violation_names_what_is_permitted() -> None:
    """A refusal a producer cannot act on just relocates the confusion."""
    schema = ResponseSchema("hold", (Field("blocker", str, allowed=BLOCKERS),))
    message = check_response(schema, {"blocker": "invented"})[0]
    for permitted in BLOCKERS:
        assert permitted in message


def test_a_permitted_value_from_a_closed_set_passes() -> None:
    """The closed set is a contract, not a rejection of everything."""
    schema = ResponseSchema("hold", (Field("blocker", str, allowed=BLOCKERS),))
    assert check_response(schema, {"blocker": "quorum"}) == ()


def test_an_optional_field_may_be_absent() -> None:
    """Optionality is declared, never assumed.

    The published API doc describes responses as JSON Schema with `required` naming a
    subset of `properties`, so optional fields are a shape the real surface already has.
    """
    schema = ResponseSchema(
        "hold", (Field("artifact_id", str), Field("detail", str, required=False))
    )
    assert check_response(schema, {"artifact_id": "a1"}) == ()


def test_an_optional_field_that_is_present_is_still_checked() -> None:
    """Optional means it may be absent, not that anything goes when it is there."""
    schema = ResponseSchema(
        "hold", (Field("blocker", str, allowed=BLOCKERS, required=False),)
    )
    violations = check_response(schema, {"blocker": "invented"})
    assert len(violations) == 1
    assert "invented" in violations[0]


def test_a_wrong_type_is_a_violation_naming_both_types() -> None:
    """Naming only the expectation leaves the reader to go and look up what arrived."""
    schema = ResponseSchema("hold", (Field("queue_position", int),))
    message = check_response(schema, {"queue_position": "3"})[0]
    assert "int" in message
    assert "str" in message


def test_a_wrong_type_does_not_also_report_a_closed_set_violation() -> None:
    """One fault should read as one fault.

    A string where an int belongs is a type error. Also reporting that it is not in the
    permitted set describes the same mistake twice and buries the actionable half.
    """
    schema = ResponseSchema("hold", (Field("blocker", str, allowed=BLOCKERS),))
    violations = check_response(schema, {"blocker": 7})
    assert len(violations) == 1
    assert "str" in violations[0]


def test_every_violation_is_reported_not_just_the_first() -> None:
    """A checker that stops at one turns fixing a response into a sequence of runs."""
    schema = ResponseSchema(
        "hold", (Field("artifact_id", str), Field("blocker", str, allowed=BLOCKERS))
    )
    violations = check_response(schema, {"blocker": "invented", "extra": 1})
    assert len(violations) == 3


def test_violation_order_is_stable() -> None:
    """Two runs over the same payload must produce the same list.

    Asserting the exact sorted order rather than comparing two calls to each other:
    within one process, sets built from the same strings iterate identically, so a
    self-comparison passes over unsorted output and proves nothing. Otherwise a diff of
    two checker outputs shows churn that is only iteration order, and the reader learns
    to skim it.
    """
    schema = ResponseSchema("hold", (Field("artifact_id", str, required=False),))
    violations = check_response(schema, {"z": 1, "a": 2, "m": 3})
    named = [v.split(":")[0] for v in violations]
    assert named == ["hold.a", "hold.m", "hold.z"]
