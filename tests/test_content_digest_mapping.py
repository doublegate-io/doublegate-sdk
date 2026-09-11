"""ADR-0068 content-digest mapping clarification (design main ``7f211bf1``).

The normative sentence these tests pin::

    content_digest == "sha256:" + submission.envelope.content_hash

and its three normative consequences: the producer *derives* rather than
recomputing, the bare hex is the stored spelling (so the prefix round trip must
be lossless), and a disagreement is a malformed document refused with
``SUBMISSION_ENCODING_INVALID``.

These are shape tests as much as value tests. The point of the mapping is that a
second, independent computation of the member is *unrepresentable*, so the
module must not offer any function that turns bytes into the prefixed member.
"""
from __future__ import annotations

import hashlib

import pytest

from doublegate_sdk import submission as S

BLOB = b"the bytes the envelope actually described"
BARE = hashlib.sha256(BLOB).hexdigest()
PREFIXED = "sha256:" + BARE


# --- the byte-hashing operation, which is legitimate but is NOT the member ---

def test_envelope_content_hash_returns_bare_hex_never_the_prefixed_member():
    got = S.envelope_content_hash(BLOB)
    assert got == BARE
    assert not got.startswith("sha256:")
    assert len(got) == 64


def test_envelope_content_hash_matches_client_gate_content_hash():
    # client-gate envelope.py derives content_hash from the blob this way.
    assert S.envelope_content_hash(BLOB) == hashlib.sha256(BLOB).hexdigest()


# --- the mandated derivation -------------------------------------------------

def test_content_digest_from_content_hash_is_exactly_prefix_plus_hex():
    assert S.content_digest_from_content_hash(BARE) == "sha256:" + BARE


def test_content_digest_from_envelope_equals_prefix_plus_that_envelopes_member():
    env = {"content_hash": BARE, "content_type": "text/plain", "size_bytes": len(BLOB)}
    assert S.content_digest_from_envelope(env) == "sha256:" + env["content_hash"]


def test_the_invariant_holds_for_an_envelope_built_from_a_blob():
    env = {"content_hash": S.envelope_content_hash(BLOB)}
    assert S.content_digest_from_envelope(env) == "sha256:" + env["content_hash"]


# --- the door: strip at the door, store bare hex, losslessly -----------------

def test_round_trip_through_the_door_is_lossless():
    digest = S.content_digest_from_envelope({"content_hash": BARE})
    stored = S.content_hash_from_content_digest(digest)
    assert stored == BARE
    assert S.content_digest_from_content_hash(stored) == digest


def test_stored_spelling_is_bare_so_the_dedup_index_is_unchanged():
    assert not S.content_hash_from_content_digest(PREFIXED).startswith("sha256:")


# --- misuse must not pass ----------------------------------------------------

def test_module_offers_no_function_turning_bytes_into_the_member():
    assert not hasattr(S, "content_digest"), (
        "a bytes -> 'sha256:...' helper reintroduces the second computation "
        "ADR-0068's mapping was chosen to make unrepresentable")
    assert "content_digest" not in S.__all__


def test_an_already_prefixed_value_is_refused_as_a_content_hash():
    with pytest.raises(S.CanonicalizationRefused):
        S.content_digest_from_content_hash(PREFIXED)


def test_bytes_are_refused_where_a_content_hash_is_expected():
    with pytest.raises(TypeError):
        S.content_digest_from_content_hash(BLOB)  # type: ignore[arg-type]


def test_uppercase_hex_is_refused_not_lowered():
    with pytest.raises(S.CanonicalizationRefused):
        S.content_digest_from_content_hash(BARE.upper())


def test_envelope_without_content_hash_is_refused():
    with pytest.raises(S.CanonicalizationRefused):
        S.content_digest_from_envelope({"content_type": "text/plain"})


def test_bare_hex_is_refused_at_the_door():
    with pytest.raises(S.CanonicalizationRefused):
        S.content_hash_from_content_digest(BARE)


# --- the verifier check: an encoding check, not a reconciliation -------------

def test_verify_content_digest_accepts_the_derived_document_and_yields_bare_hex():
    doc = {"content_digest": PREFIXED, "envelope": {"content_hash": BARE}}
    assert S.verify_content_digest(doc) == BARE


def test_verify_content_digest_refuses_disagreement_as_malformed():
    doc = {"content_digest": "sha256:" + "0" * 64, "envelope": {"content_hash": BARE}}
    with pytest.raises(S.CanonicalizationRefused) as ei:
        S.verify_content_digest(doc)
    assert ei.value.code == S.ENCODING_INVALID
