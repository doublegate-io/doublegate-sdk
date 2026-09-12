"""The reason codes a reducer may give for skipping an item.

`decision-cards.md:1046-1050` states the problem: a pipeline stage that skips an item
produces silence, so a user cannot tell a skip from a failure from a no-op. The obligation
to say why is not new — `05-decision-authority.md` §2 and §5 already require a disposition
per decision, and a skip is a decision with no visible output. Only the vocabulary is new.

The four values are derived from the skip paths that exist in `client-gate`'s integrator,
not from a taxonomy invented here:

- ``duplicate`` — the content is already present under another artifact in the same space,
  so writing it would be an overwrite (`integrator.py:216`, ADR-0023 d5).
- ``surface_unwritable`` — a target surface refused the write. Which exception was raised
  is *detail*, carried alongside the code and never inside it; today `integrator.py:240`
  interpolates the exception class name into the reason string, which makes the emittable
  set the set of exception classes `OSError` can be. No consumer can enumerate that.
- ``never_promoted`` — the artifact's history carries no promotion to materialize.
- ``not_served`` — promoted, but not the served member: superseded, or the pin is on
  another member (ADR-0035).

There is deliberately no ``other``. `decision-cards.md:1061-1062` names an `other` bucket
that absorbs everything and tells nobody anything as a failure mode of this design, and
`:1071-1072` gives the stop condition: if the set cannot be kept closed and meaningful,
ship a boolean skipped flag and no reason at all. A dishonest enumeration is worse than an
honest boolean, so the escape hatch is removal of the vocabulary, never a catch-all member.
"""

from __future__ import annotations

__all__ = ["SKIPPED_REASONS", "normalise_skipped_reason"]

SKIPPED_REASONS = frozenset({
    "duplicate",
    "surface_unwritable",
    "never_promoted",
    "not_served",
})


def normalise_skipped_reason(reason: str | None) -> str:
    """Return ``reason`` unchanged if it names one of the four skips, else raise.

    Unlike `normalise_category`, ``None`` does not pass through, and the difference is
    the contract's rather than this module's. `ADR-0053:320-321` explicitly permits a
    stage to return "no category at all", so a submitter being told nothing is a state
    the register sanctions. Nothing sanctions a reasonless skip:
    `decision-cards.md:1063-1065` says a skip must emit a receipt naming a code from the
    declared set, and lists "any silent skip" under what proves the design does not work.
    A ``None`` that passed through here would reconstruct that silence one layer down,
    inside a type built to end it.

    Raises:
        ValueError: if ``reason`` is ``None``, or is outside the four.
    """
    if reason is None:
        raise ValueError(
            "a skip must name a reason; None is the silent skip this enumeration exists "
            f"to prevent. The four permitted values are {', '.join(sorted(SKIPPED_REASONS))}."
        )
    if reason not in SKIPPED_REASONS:
        raise ValueError(
            f"not a skipped reason: {reason!r}. The four permitted values are "
            f"{', '.join(sorted(SKIPPED_REASONS))}. "
            "Detail about this particular skip goes in a field beside the code, never "
            "inside it; a code with a value interpolated into it is not enumerable."
        )
    return reason
