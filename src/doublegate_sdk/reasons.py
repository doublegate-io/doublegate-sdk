"""The reason categories a gate may return to a principal about its own submission.

`ADR-0053-scan-then-return.md:309-321` fixes these at five values and closes the list:
"Adding a sixth category is a *decision*, recorded here as such, and not an
implementation choice an executor may make while building a surface."

Two things this module is deliberately not.

It is not the blocker vocabulary. Blockers are the gate's internal account of what a
hold is waiting for; categories are what a submitter is told about why its own
submission was refused. Different lists, different audiences, and `INV-SEC-10` continues
to withhold the artifact's body and label regardless of either.

It is also not a mapping from a processing stage to a category. `ADR-0053` closed the
enumeration and left that mapping open, so nothing here decides which stage produces
which value.
"""

from __future__ import annotations

__all__ = ["CATEGORIES", "normalise_category"]

CATEGORIES = frozenset({
    "off-scope",
    "quality",
    "policy",
    "duplicate",
    "unreadable",
})


def normalise_category(category: str | None) -> str | None:
    """Return ``category`` unchanged if the contract permits it, else raise.

    ``None`` passes through. `ADR-0053:320-321` says a stage read "returns one of these
    five **or it returns no category at all**", so having formed no view is an answer
    the type has to be able to carry. Forcing a value here would only move the problem
    to the caller, who would pick a filler.

    An unrecognised value raises instead of being dropped. Dropping it would turn "we
    disagree about why this was refused" into "no reason was given" — silently, and in
    the direction that loses information the submitter is entitled to.

    Raises:
        ValueError: if ``category`` is neither ``None`` nor one of the five.
    """
    if category is None:
        return None
    if category not in CATEGORIES:
        raise ValueError(
            f"not a reason category: {category!r}. The five permitted values are "
            f"{', '.join(sorted(CATEGORIES))}, or None for no category at all. "
            "The list is closed; a sixth value is a decision recorded in ADR-0053, "
            "not a choice made at a call site."
        )
    return category
