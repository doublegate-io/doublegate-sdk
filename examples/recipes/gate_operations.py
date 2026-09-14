"""Recipes for gate operations, over a client the caller supplies.

Every function here takes a :class:`doublegate_sdk.client.GateClient` as its
first argument. None of them calls :func:`doublegate_sdk.connect`, reads an
environment variable, or holds a connection of its own — so importing this
module cannot open anything, and a recipe cannot reach an endpoint you did not
hand it.

Build the client yourself, once, and pass it in::

    from doublegate_sdk import connect

    client = connect("https://gate.example.org/mcp", token=my_token,
                     allow_writes=True)        # writes are off by default

For a read-only workflow leave ``allow_writes`` at its default ``False``: the
transport then refuses a write-annotated tool itself, so a read-only client
cannot be talked into writing by code that reaches past this module.

**Nothing here retries.** A write whose outcome is unknown is reported as such
and handed back to you; re-sending it without an idempotency contract is how the
same observation gets filed twice, so these recipes will not do it.

**Presence is not admission.** A ``state`` on a proposal is the gate's answer to
the submission, not a review outcome, and a recall hit is served knowledge, not
proof that your own write landed.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doublegate_sdk.client import GateClient, GateError

__all__ = [
    "Observed",
    "GateCallFailure",
    "observe",
    "observe_file",
    "artifact_position",
    "artifact_history",
    "event_field_coverage",
    "recall_answers",
    "waiting_for_review",
    "read_only_snapshot",
    "call_or_failure",
    "server_catalog",
]

#: Fields ``doublegate.status`` may carry beyond ``state``, on the maintained
#: client tier. Used only to report presence: a build that sends fewer is
#: reported as such, never filled in with a default.
STATUS_OPTIONAL_FIELDS = ("sub_level", "quorum", "blocking")

#: Keys a ``doublegate.why`` event carries on the maintained client tier. A
#: comparison baseline for :func:`event_field_coverage`, not a requirement.
WHY_EVENT_KEYS = ("event_id", "type", "identity", "ts", "payload", "sig")

#: Error kinds that mean the endpoint was never reached. Anything else — an
#: ``unauthorized`` answer, for instance — is the endpoint answering.
NOT_REACHED_KINDS = frozenset({"unavailable", "timeout", "redirect_refused"})


@dataclass(frozen=True, slots=True)
class Observed:
    """What the gate answered to one proposal, plus the raw response.

    ``artifact_id`` is the only durable reference to the submission: persist it
    before doing anything else, because there is no way to look a submission up
    without it.

    ``state`` is the gate's answer *to the submission*. It is not admission and
    not a review verdict.
    """

    artifact_id: str
    state: str
    response: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GateCallFailure:
    """A :class:`GateError` turned into data, for callers that collect failures.

    ``kind`` and ``code`` are the SDK's stable diagnostic pair. The server's own
    error *text* is deliberately absent here because the SDK withholds it —
    server messages carry internals and a client log is the wrong place for
    them. Branch on ``kind``; do not parse English.

    ``outcome_unknown`` is the one that changes what you may do next: it means a
    write may already have landed. Reconcile before sending anything again.
    """

    kind: str
    code: int | None = None
    outcome_unknown: bool = False

    @property
    def endpoint_reached(self) -> bool:
        """Whether the gate answered at all, including with a refusal."""
        return self.kind not in NOT_REACHED_KINDS

    @classmethod
    def from_error(cls, error: GateError) -> "GateCallFailure":
        return cls(error.kind, error.code, bool(error.outcome_unknown))

    def to_payload(self) -> dict[str, Any]:
        return {"kind": self.kind, "code": self.code,
                "outcome_unknown": self.outcome_unknown,
                "endpoint_reached": self.endpoint_reached}


def observe(client: GateClient, content: str, *, source_uri: str,
            content_type: str = "memory", trust_class: str | None = None,
            space: str | None = None,
            derives_from: list[str] | None = None) -> Observed:
    """Propose one observation and return the handle the gate issued.

    ``client`` must have been built with ``allow_writes=True``; otherwise the
    transport raises ``GateError('writes_disabled')`` before anything is sent.

    ``source_uri`` is provenance a later reader relies on — give one that is
    true. Writer identity is *not* sent: the service derives it from the
    connection and refuses a request that carries it.

    Raises :class:`GateError` unchanged. Check ``error.outcome_unknown`` before
    deciding anything: when it is set, the write may have landed and resending
    would duplicate it.
    """
    response = client.propose(content, content_type=content_type,
                              source_uri=source_uri, trust_class=trust_class,
                              space=space, derives_from=derives_from)
    # ``propose`` already refuses a response missing a usable ``artifact_id`` or
    # ``state``, so these two reads are safe without re-validating.
    return Observed(response["artifact_id"], response["state"], response)


def observe_file(client: GateClient, path: str | Path, *,
                 source_uri: str | None = None,
                 content_type: str = "memory",
                 trust_class: str | None = None,
                 max_bytes: int = 512_000) -> Observed:
    """Submit a local UTF-8 text file, refusing unusable input before sending.

    Four local refusals, all :class:`ValueError`, all raised without opening a
    connection: the file is not readable as UTF-8 text, it is empty or
    whitespace-only, or it is larger than ``max_bytes``. The transport has its
    own request bound; this check exists so the message names the real problem
    instead of arriving as ``request_too_large``.

    This surface stores **text**. A PDF, an image or an archive is refused
    rather than base64-smuggled into a field the service would store verbatim.

    ``source_uri`` defaults to the file's own resolved ``file://`` location, so
    the record points at something real. Override it when the file is a local
    copy of something with a better identity.
    """
    resolved = Path(path).resolve()
    raw = resolved.read_bytes()
    if len(raw) > max_bytes:
        raise ValueError(f"document is {len(raw)} bytes; the local bound is {max_bytes}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("document is not valid UTF-8 text; "
                         "this surface stores no binary") from None
    if not text.strip():
        raise ValueError("document is empty or whitespace-only; propose requires content")
    return observe(client, text, source_uri=source_uri or resolved.as_uri(),
                   content_type=content_type, trust_class=trust_class)


def artifact_position(client: GateClient, artifact_id: str) -> dict[str, Any]:
    """One artifact's position in the register, as the gate returned it.

    The response always carries ``state`` — the SDK raises ``invalid_response``
    otherwise. ``present_optional_fields`` lists which of
    :data:`STATUS_OPTIONAL_FIELDS` this build actually sent, so an absent field
    reads as absent rather than as an empty value someone might trust.

    There is no whole-gate status call on this surface: ``status`` requires an
    artifact id.
    """
    response = client.status(artifact_id)
    return {
        "artifact_id": artifact_id,
        "state": response["state"],
        "present_optional_fields": [name for name in STATUS_OPTIONAL_FIELDS
                                    if name in response],
        "response": response,
    }


def artifact_history(client: GateClient, artifact_id: str) -> list[dict[str, Any]]:
    """Every ledger event that moved one artifact, in the order returned.

    ``why`` raises ``invalid_response`` unless the answer is an object with an
    ``events`` list, so the list here is real. Its *contents* are the service's
    payload, handed back unmodified.

    Reading a history is not verifying it. ``sig`` values are returned when the
    build sends them; nothing here checks a signature, and signature
    verification is not on this client surface.
    """
    return list(client.why(artifact_id)["events"])


def event_field_coverage(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Which :data:`WHY_EVENT_KEYS` the returned events actually carried.

    Presence reporting, not validation: a build that sends fewer fields is
    reported through ``absent``, never rejected and never quietly filled in.
    """
    present: set[str] = set()
    for event in events:
        if isinstance(event, dict):
            present.update(event)
    return {
        "event_count": len(events),
        "event_types": [event.get("type") if isinstance(event, dict) else None
                        for event in events],
        "present": sorted(present),
        "absent": [key for key in WHY_EVENT_KEYS if key not in present],
    }


def recall_answers(client: GateClient, query: str, *, limit: int = 5,
                   spaces: list[str] | None = None,
                   include_provisional: bool = False,
                   audience: str | None = None) -> list[dict[str, Any]]:
    """Read served knowledge for one query.

    Your own unreviewed submissions are excluded by the SDK, so an empty list
    straight after your own write is the expected answer, not a failure: a
    pending proposal is not served knowledge, and reading it back would let a
    caller mistake its own echo for an answer.
    """
    return list(client.recall(query, limit=limit, spaces=spaces,
                              include_provisional=include_provisional,
                              audience=audience)["results"])


def waiting_for_review(client: GateClient, *, limit: int = 100) -> list[dict[str, Any]]:
    """What is waiting for review, as metadata only — never content.

    Useful after a write whose outcome was unknown: check here before deciding
    whether the submission landed, instead of sending it again.
    """
    return list(client.pending(limit=limit)["pending"])


def read_only_snapshot(client: GateClient, artifact_id: str) -> dict[str, Any]:
    """Position plus history for one artifact, with per-call failures as data.

    Both calls are attempted even when the first fails, so one unreachable call
    does not hide the other's answer. A failed call contributes a
    :class:`GateCallFailure` payload under its own key; ``complete`` says
    whether both answered.

    Pass a client built with ``allow_writes=False``. That is load-bearing rather
    than decorative — the transport refuses a write-annotated tool itself, so
    this path cannot be talked into a mutation.
    """
    snapshot: dict[str, Any] = {"artifact_id": artifact_id, "mutated": False}
    failures: list[GateCallFailure] = []
    for name, call in (("status", artifact_position), ("history", artifact_history)):
        try:
            snapshot[name] = call(client, artifact_id)
        except GateError as error:
            failure = GateCallFailure.from_error(error)
            failures.append(failure)
            snapshot[name] = {"failed": failure.to_payload()}
    snapshot["complete"] = not failures
    snapshot["endpoint_reached"] = all(failure.endpoint_reached for failure in failures)
    return snapshot


def call_or_failure(call: Any, *args: Any, **kwargs: Any
                    ) -> tuple[Any | None, GateCallFailure | None]:
    """Run one bound client method, returning ``(result, None)`` or ``(None, failure)``.

    For callers that would rather branch on a value than write a ``try`` around
    every request::

        answers, failure = call_or_failure(recall_answers, client, "labelling")
        if failure is not None and failure.outcome_unknown:
            ...    # a write may have landed; reconcile, do not resend

    Only :class:`GateError` is caught. A ``ValueError`` from bad arguments is a
    bug in the calling code and is left to raise.
    """
    try:
        return call(*args, **kwargs), None
    except GateError as error:
        return None, GateCallFailure.from_error(error)


def server_catalog(client: GateClient) -> dict[str, Any]:
    """What this endpoint says it is, and which tools it actually lists.

    Two reads through ``client.transport``: ``server/discover`` then
    ``tools/list`` — the negotiation methods the maintained server answers. The
    SDK does not send ``initialize``; that is refused unless an operator turned
    on a legacy flag.

    ``tools`` is the catalog *that server* lists, in its own order. Compare it
    against the call you were trying to make: a missing tool explains an
    ``unsupported_operation`` with no further digging.

    Negotiation is **not** part of the ``GateTransport`` protocol, which
    declares only ``call``. :class:`doublegate_sdk.client.HttpMcpTransport`
    provides both methods; a caller-supplied transport may not, and this raises
    :class:`TypeError` in that case rather than pretending the endpoint refused.
    """
    transport = client.transport
    discover = getattr(transport, "discover", None)
    tool_names = getattr(transport, "tool_names", None)
    if discover is None or tool_names is None:
        raise TypeError("this transport does not offer server/discover and tools/list; "
                        "negotiation is outside the GateTransport protocol")
    return {"discover": discover(), "tools": list(tool_names())}
