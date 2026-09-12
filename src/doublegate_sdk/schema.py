"""A response schema as a contract, where an undeclared field is a violation.

The failure this exists to catch has already happened twice in this codebase, in the
same shape both times: a producer and a consumer disagreeing about a vocabulary, with
nothing between them checking. A gate emitted two hold-reason values the consumer's
accepted set did not contain, so every held artifact read as ``unstated``. Elsewhere a
reason string is built by interpolating a Python exception class name, which makes the
producer's value set unenumerable by construction — no reader can write down what that
field may contain, because the answer is "whatever exception happened to be raised".

So a schema here is not a minimum. It is the full shape, and ``check_response`` reports
an undeclared field as a violation rather than ignoring it as a harmless extra. An extra
field is not harmless: the moment it is emitted, some consumer may start reading it, and
from then on the producer is committed to a field nobody agreed to and nothing documents.
Catching it at the schema is the only point where removing it is still cheap.

A field may also declare a closed set of values, which is what catches the vocabulary
class directly. ``Field('blockers', str, allowed=BLOCKERS)`` says the permitted values
are exactly those, so a producer that invents a tenth one fails here instead of degrading
silently at the consumer.

Fields are required by default and optional only when they say so. That follows the
surface this describes rather than a preference: the published API doc declares response
objects as JSON Schema with ``required`` naming a subset of ``properties``, so optional
fields are a shape the real responses already have. Defaulting to required keeps the
quieter mistake — forgetting to declare that something may be absent — the one that
fails, while omitting a field a consumer depends on still fails loudly.

Every violation is reported, not just the first. A checker that stops at one turns fixing
a response into a sequence of runs, and the second problem stays hidden until the first
is fixed.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

__all__ = ["Field", "ResponseSchema", "check_response"]


@dataclass(frozen=True, slots=True)
class Field:
    """One field of a response, and what it is allowed to hold.

    Args:
        name: the key as it appears on the wire.
        type: the Python type the value must be an instance of.
        allowed: an optional closed set of permitted values. This is the part that
            catches a producer inventing a vocabulary value nobody agreed to.
        required: whether the field must be present. Defaults to ``True``; see the
            module docstring for why absence is the thing that has to be declared.
    """

    name: str
    type: type
    allowed: Collection[Any] | None = None
    required: bool = True


@dataclass(frozen=True, slots=True)
class ResponseSchema:
    """The full declared shape of one response."""

    name: str
    fields: tuple[Field, ...]


def check_response(schema: ResponseSchema, payload: Any) -> tuple[str, ...]:
    """Return every way ``payload`` departs from ``schema``; empty means it conforms.

    Returns violations rather than raising, because a response can be wrong in several
    ways at once and the caller usually wants all of them. Order is stable so two runs
    over the same payload produce the same list.
    """
    violations: list[str] = []
    declared = {field.name for field in schema.fields}

    for name in sorted(set(payload) - declared):
        violations.append(
            f"{schema.name}.{name}: undeclared field. The schema is the full shape, "
            "not a minimum; declare it or stop emitting it."
        )

    for field in schema.fields:
        if field.name not in payload:
            if field.required:
                violations.append(f"{schema.name}.{field.name}: required field is missing")
            continue

        value = payload[field.name]
        if not isinstance(value, field.type):
            violations.append(
                f"{schema.name}.{field.name}: expected {field.type.__name__}, "
                f"got {type(value).__name__}"
            )
            continue

        if field.allowed is not None and value not in field.allowed:
            permitted = ", ".join(sorted(str(v) for v in field.allowed))
            violations.append(
                f"{schema.name}.{field.name}: {value!r} is not one of the permitted "
                f"values ({permitted})"
            )

    return tuple(violations)
