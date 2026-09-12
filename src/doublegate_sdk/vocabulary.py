"""The blocker vocabulary: what may actually be blocking a held artifact.

Both gates and any client that renders a hold need the same list, so it lives here rather
than in either gate. The list is closed. Adding a value is a change to
`docs/04-cross-cutting/05-decision-authority.md` §7 and to whatever a reviewer is expected
to do about the new one — not an implementation convenience.

Quoting that section, which is the source of the nine values below:

    Track actual blockers, independently of a limbo sub-level or mode name:
    `hard_constraint`, `semantic_review`, `needs_evidence`, `quorum`,
    `reviewer_unavailable`, `human_required`, `human_directive`,
    `authority_conflict` and `retry_exhausted`. Multiple blockers may coexist.

"Independently of a limbo sub-level or mode name" is the load-bearing phrase: a gate's own
mode name (`awaiting_signer`, say) is a local routing label and is not one of these. Both
can be reported; only these nine are the contract's answer to "what is blocking this".
"""

from __future__ import annotations

from typing import Iterable

__all__ = ["BLOCKERS", "normalise_blockers"]

BLOCKERS = frozenset({
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


def normalise_blockers(blockers: Iterable[str]) -> tuple[str, ...]:
    """Return the given blockers deduplicated and ordered, refusing anything unlisted.

    Sorted because two holds blocked on the same things should serialise identically;
    without that, a caller diffing two responses sees a change that is only iteration order.

    Raises:
        TypeError: if given a bare string, which would otherwise iterate as characters and
            turn one blocker into several one-letter ones.
        ValueError: if the result would be empty, or if any value is outside the nine. An
            empty blocker list on a held artifact means the hold cannot say what it is
            waiting for, which is a bug to surface rather than a value to render.
    """
    if isinstance(blockers, str):
        raise TypeError(
            "normalise_blockers takes a collection of blockers, not a single string; "
            f"pass [{blockers!r}] if you mean one"
        )
    seen = set(blockers)
    if not seen:
        raise ValueError("a held artifact must name at least one blocker")
    unknown = seen - BLOCKERS
    if unknown:
        raise ValueError(
            f"not in the blocker vocabulary: {', '.join(sorted(unknown))}. "
            f"The nine permitted values are {', '.join(sorted(BLOCKERS))}. "
            "A local mode name is not a blocker; report it alongside one, not instead of one."
        )
    return tuple(sorted(seen))
