# PR #23 ↔ two-phase identity: bounded reconciliation packet

**Proposal for the existing design owner, not an accepted wire contract or SDK cutover.**
Read-only source review and local executable experiments, 2026-09-11. No competing ADR,
no implementation branch edits, no remote comment, commit, service or credential changes.

## Outcome and ownership

The premise changed: authenticated GitHub reads show [design PR #23](https://github.com/doublegate-io/design/pull/23)
**merged**, not open: author `e8kor`, head `b6bc7cd6330b10600430bc821bfe70c5c386efe0`,
merge `72754b68b9ad503ad0a208840069a57dfbda7723`, merged at `2026-09-11T15:01:42Z`.
That merge was also `main` when read. No issue comments/reviews were returned by the API.
The author is the ownership handoff, not evidence that their editing session remains active.
The local design checkout is dirty and stale; its identity document is local WIP, not a
remote accepted-wire-format replacement. This packet does not edit it.

**Keep PR #23's derivation verbatim:**

```
B = P1-A JCS-profile canonical bytes of the complete event document
artifact_id = "sha256:" + SHA256(B).hexdigest()
signature = Ed25519(ASCII("doublegate.submission.v1") || 0x00 || B)
```

Neither the event's own `artifact_id` nor its detached signature belongs in B. Keep
`content_digest` over raw bytes, `envelope_digest` for the frozen envelope's own derived
hash, and no `sub_` identity. A hash of some newly invented identity tuple is not proposed.

**Minimal recommended bridge:** retain the ten PR members, add **one closed member**,
`attribution_chain`, containing the exact existing two phase JWS strings (production then
submission), after resolving their old envelope-hash spelling. Derive operation, final
principal and final destination from those signed statements; bind exact space through
an already-signed retained scope record, with an explicit equality check to the gate's
selected destination space. This avoids reflexively adding every WIP field at the top
level. The scope-resolution rule below is a proposal requiring owner confirmation, not
an assertion that every existing scope already equals a collection space.

## Source evidence and status boundaries

Pinned remote sources were read via authenticated `gh api`, never raw unauthenticated URLs.
Snapshots are in `sources/`; `pr.json` and `source-manifest.json` retain initial provenance.
Local source snapshots have a `local__` prefix and are explicitly **not main**.

- [ADR-0068 at merge](https://github.com/doublegate-io/design/blob/72754b68b9ad503ad0a208840069a57dfbda7723/docs/adr/ADR-0068-artifact-id-is-the-submission-event.md):
  lines 191–276 settle derivation as engineering judgement open to challenge. The original
  body is historical; its earlier open-shape table is not a competing current choice.
- [P1-A/01 at merge](https://github.com/doublegate-io/design/blob/72754b68b9ad503ad0a208840069a57dfbda7723/docs/06-roadmap/execution/P1-A/01-submission-contract.md):
  lines 44–70 enumerate ten members and drop space; 124–139 specify signature coverage;
  182–194 explicitly mark fixtures stale and independent agreement outstanding.
- [P1-A/02 at merge](https://github.com/doublegate-io/design/blob/72754b68b9ad503ad0a208840069a57dfbda7723/docs/06-roadmap/execution/P1-A/02-issuer-trust.md):
  §2 already commits transitively to the exact historical membership assertion;
  §§3–6 enroll issuer keys and distinguish grants from sponsorship. Not every missing
  top-level field is a missing security binding.
- [ADR-0066 at merge](https://github.com/doublegate-io/design/blob/72754b68b9ad503ad0a208840069a57dfbda7723/docs/adr/ADR-0066-the-submission-carries-the-envelope.md):
  D3's space omission is justified from an earlier implementation inventory, not from the
  later scope/action security contract. D2/D3/D4 remain proposed.
- Local `design/docs/04-cross-cutting/10-federated-identity-attribution.md` §§3–6.2:
  distinct hop audiences, scoped parent grants, exact immutable references, two phases,
  fresh authority on every retry, and distinct requester/actor/contributor roles.
  Lines 336–364 still call the envelope hash `artifact_id`; blindly changing its meaning
  to event hash would create a cycle. Lines 488–517 require trusted observation provenance.
- Local SDK `src/doublegate_sdk/identity.py`: `_REQUIRED` and `AttributionBinding` use old
  `artifact_id`; verification at lines 192–229 checks expected phase/resource bindings but
  explicitly does not authorize. Org `phase_relationships.py:16–18,39–150` resolves signed
  references under the scope lock; `identity_history.py` includes tenant and scope **in the
  digested record** and rejects wrong-scope records. This is a real existing external binding,
  not an absent-space proof. The proposed mapping of selected HTTP space to that scope still
  needs explicit contract spelling and installed-runtime acceptance.
- Remote security §4d and INV-SEC-2 retain current scoped authority and independent genuine
  review/promotion. Local identity §6.2 refines historical and current phase authority;
  no signature or receipt becomes a reviewer vote, human intent or publication authority.

The P1-A acceptance ledger itself separates accepted event semantics from engineering
judgement on derivation and unresolved independent byte agreement. It has stale internal
prose (e.g. generic “nothing accepted” and legacy inventory wording). Do not turn that
editorial inconsistency into another six-role approval ceremony: the operator is the single
semantic owner. This packet's member/role/scope bridge is not self-approved.

## All ten existing members inspected

| Existing member | What already binds | Narrow reconciliation, not a requirement to preserve arbitrary WIP |
|---|---|---|
| `contract` | Closed profile and domain interpretation | Keep `doublegate.submission/1` for this proposed event profile; distinguish it from existing WIP outer `submission_version: 2` and attribution version 1. One eventual format, not runtime negotiation. |
| `envelope` | Frozen eleven-field registry, proposal trust class, metadata and raw-content hash | Keep envelope rules, omission/null boundary and existing validators. Only derived-property/reference naming changes. No operation fields injected into envelope v1. |
| `content_digest` | Raw submitted bytes; checked against envelope `content_hash` | Keep content dedup/change detection here. Two events with the same content remain distinct. |
| `evidence_manifest_digest` | Exact manifest commitment; potential external bindings already possible | Define it as the production manifest from identity §5.1. Check retained evidence bytes. Do not assume a generic digest already prescribes phase relationships; adding those now to the production manifest risks chronology/circularity. |
| `tenant_id` | Tenant tied to verified membership and issuer enrollment | Equality to both phase tenants and gate-owned tenant context; not caller assertion. |
| `team_id` | Selected team at submission, verified through assertion | Equality to chain home team. Contributor membership and requester team-action permission remain independent of submitter membership. |
| `principal` | One verified principal plus kind/sponsor | Explicitly the **submission-phase actor/presenting principal**, not source author, requester, contributor or deployment-key owner by inference. Chain retains other roles. Crosswalk `person` ↔ canonical `human` only via enrolled mapping; sponsor is not delegation/human consent. |
| `membership` | Issuer + revision + digest of retained assertion (§2 already transitive) | Retain as the event principal's historical assertion; phase-specific contributor membership, mapping, relationship, policy, enrollment and grant references remain separate. Their revisions need not match. Keep legacy prefix/shape rules only as pinned by owning schema; do not infer namespace equality. |
| `submitted_at` | Signed submitter observation, distinct from ingest and server receipt | Freeze across retries. Define ordering relative to phase issuance, without deriving this timestamp from `ingest_ts` or rewriting either format. |
| `review_refs` | Whole-event signature already commits exact ordered record references | Resolve and verify actual local verdict/promotion records under existing roles. They are evidence, not authority; preserve independent signers. An empty fixture list proves no grading/promotion. |

**Finding:** route ID is already authenticated transitively. Membership is already
externally bound. Existing WIP scopes/grants already bind resource authorization. The gap
is the **specified connection** between the ten-member event, exact two-phase chain and
selected operational destination—not “no security exists outside the ten members.” An
unchanged ten-member format could instead define a typed retained submission-proof record
reachable from an existing commitment, but that changes that member's semantics and needs
a precise noncircular definition. A single explicit chain member is the less surprising
amendment; not a new hash algorithm and not a general extensions bag.

## Precise self-hash-free chronology

Let `E` be the completed frozen envelope, `C` the raw content, and `M` the production
manifest `{manifest_version:1,evidence_refs:[sorted unique raw evidence digests]}`.
`M` excludes both attribution statements and later verdict/promotion records. Freeze E
before phase signing; adding ingest metadata later would invalidate production binding.
If an actual workflow cannot finalize E then, it must move production signing to the first
trusted capture point with E complete—not sign a future event ID or fabricate early proof.

1. **Retain historical prerequisites.** Independently enrolled records exist with trusted
   installation/observation provenance before phase observations. Retain exact bytes and
   revision/digest identities for membership, mapping, relationship, policy, attester
   enrollment, authority snapshot and both requester/actor complete root-to-leaf grants.
   Equal revision strings across namespaces prove nothing. Caller-supplied “approved” objects
   and newly created backdated snapshots are not evidence of historical permission.
2. **Production proof P.** Use attribution-v1 production JWS with its existing canonical
   encoding and Ed25519 protected header, except rename its old envelope-hash member
   `artifact_id` to **`envelope_digest`** (bare hex consistent with the phase schema).
   It binds E's canonical digest (and hence E.content_hash), M's digest, tenant, operation,
   requester, producer-phase actor, contributor, home team, production audience/attester,
   observed/issued times and each exact reference described above. No parent, no event ID,
   no submission signature. Production action is `produce_contribution`, not `submit` or
   `ingest`. The relationship/scope records must resolve to the exact production space.
3. **Retain P bytes.** `p_ref = SHA256(ASCII(P))`; this includes the production signature,
   which already exists. Root cryptographic validity alone does not prove historical authority.
4. **Submission proof Q.** Existing attribution-v1 submission JWS binds the same envelope,
   production evidence, operation, tenant, requester, contributor and home team, with
   `parent_statement_digest = p_ref`. Its actor, audience, attester and reference revisions
   may differ, each independently verified for canonical action `submit`. Use
   `envelope_digest` here too, not future event ID. Q precedes the final event signature.
   Q is the phase authority/provenance statement; the event signature is the commitment to
   the whole final submission, including later local evidence references. They have distinct
   purposes, not two conflicting event IDs.
5. **Build complete S.** Assemble the ten-member event plus
   `attribution_chain:[P,Q]`, with unchanged byte strings; all review references are now known.
   Check `S.principal.id == Q.actor_id`, tenant/team and evidence equality, exact E digest,
   and independently verified signer-to-submitting-principal relationship. The deployment key
   is not itself the principal. Root/requester/contributor identity is not collapsed into Q.actor.
6. **Hash S then sign S.** Compute B and event ID with PR #23's formula, then its detached
   signature. Including existing P/Q signatures as strings is not a self hash: the excluded
   signature is S's own. Persist exact B, signature, E/C/M, P/Q, evidence/review bytes and
   resolvable immutable reference records before send. No bearer in these objects/outbox.
7. **Receiver checks before effects.** Route ID equals derived ID; verify whole-event signature
   and enrollment/purpose, both JWS signatures, exact parent, resource/principal equality,
   evidence/promotions, historical references and chronology. For each phase:
   `trusted_record_installation <= membership_observed_at <= issued_at <= observed_receipt`;
   `P.issued_at <= Q.issued_at <= S.submitted_at` (compare instants across explicit units).
   Define allowed clock tolerance in the owning verifier, not a claim that clocks prove order.
   Fresh destination token and gate-owned current authorization validate operation/action,
   actor/requester/contributor, exact space/audience, all parent chains and current policy.
   Historical verification never grants current permission; unavailable/stale/ambiguous proof holds.
8. **Retain receipt; never rewrite S.** Journal exact bindings under current authority in the
   existing transaction. Revalidate on send, ingress, lease and publication as already required.
   Receipt is `queued`, not review or ratification. Token renewal produces a fresh current
   observation linked to the original receipt; it does not refresh signed membership/time.

### Space and destination without a top-level field explosion

Q directly signs destination audience and operation. P and Q each already sign exact
`identity_refs.*` and authority/grant references. Existing retained identity records include
`tenant_id` and `scope`; those bytes are digested. **For this proposed profile**, specify one
canonical destination-space resolver: the selected outer space maps uniquely to the enrolled
scope committed by each phase relationship/policy record, and verifier compares them. If the
mapping itself is nontrivial/versioned, retain and bind its exact mapping record too. A broad
allowed-space list alone is permission, not selection: it cannot bind which authorized space
this event selected. Ambiguous/multi-space selection is a refusal, not “pick any allowed one.”

If existing scope semantics cannot express that exact selection, the minimal fallback is one
explicit `space` binding in the signed phase document(s) or event, with equality/current grant
checks—not a demand that every historical transport member be signed. This packet's executable
fixture exercises the **exact retained-scope binding** choice; it does not establish the mapping
for the real product. Destination `audience` means the enrolled receiving service, not recall
preference `audience`, team, URL spelling or collection name.

Raw content/base64 `encoding`, network headers, fresh access token, socket time and routing
aliases remain transport context where authenticated digest/equality/policy checks already
supply binding. Operation action is fixed by the enrolled phase/action registry; no duplicate
arbitrary `action` top-level member is necessary. A gate must derive/check the effective action,
not accept a caller's interpretation of a phase string.

### Operation uniqueness is not event hashing

Proposed gate conflict domain:
`(tenant, canonical_space_scope, destination_audience, operation_id, submission_phase)`.
The operation handle is allocated/authorized by the workflow and immutable per attempt.
Do **not** include content hash, chain digest, event ID, changeable submitter or grant revision
in that conflict key. They are compared **values**, not lookup dimensions. This fixes the WIP
key's old envelope/artifact term, which otherwise lets a changed event evade conflict lookup.

After fresh authorization, an identical complete commitment returns the original receipt and
appends a token-free authorization observation. A different signed member under the same
operation conflicts (409 in identity semantics), even with a newly computed valid event ID and
signature. A genuinely new authorized operation is a new event. Grant replacement is a separately
linked reauthorization decision preserving old attempts, not silent refresh of historical Q.
This model does not add a global operation-ID authority service or globally unique UUID scheme.

## Exact minimal owning-document amendments (proposed, do not apply from this packet)

1. **ADR-0068 dated clarification only:** append a short scope note: “Complete submission
   bytes include the accepted two-phase attribution commitment. Neither phase references the
   future event ID; pre-submission resource references use `envelope_digest`. The existing
   whole-document derivation, domain tag, raw-content identity and route cross-check stand.”
   Do not allocate another ADR, reopen shape (b), or edit the historical decision body.
2. **P1-A/01 §§1–4:** table ten→eleven with one required `attribution_chain` row, exact two JWS
   strings under amended identity schema. Add principal=Q.actor, tenant/team/E/M equalities,
   phase-reference and chronology checks, exact selected-scope/audience mapping, and independent
   operation conflict check. Replace “space is not carried” with “space may be transport-carried
   but is checked against the signed exact scope binding.” Signature remains outside the signed
   object; content/encoding stay outside. Preserve envelope and JCS boundaries. Pin refusal order
   and map new phase/history/current-authority failures coherently with identity's 401/403/409/503
   versus P1-A's structural 422; this packet does not claim that mapping already settled.
3. **Identity §5/5.1, schema and conceptual binding interfaces:** rename only old
   envelope-target `artifact_id` in P/Q to `envelope_digest`; add the acyclic chronology and
   event chain inclusion above. Replace retry-key tuple with event-independent scoped operation
   uniqueness. Do not reinterpret every ledger/verdict `artifact_id` by search/replace: each
   pre-event local review/promotion target must remain the resource it actually signed, linked
   by S.review_refs; post-event admission references use event ID. Keep independent roles,
   historical refs, per-phase actions, current grant/revocation checks and existing genuine
   promotion validation. Choose one authoritative closed wire schema/outer-body spelling and
   reconcile `contract`, `submission_version:2`, null/NFC/seconds/ms scopes before SDK work;
   no second accepted route, opportunistic version fallback or renumbered envelope.
4. **P1-A acceptance ledger/vector notes and cutover plan + identity §5 reset prose:** record
   this remaining reconciliation as a wire/binding proposal, not reopened derivation or a new
   semantic-owner bureaucracy. Restamp stale inventory and regenerate accepted fixtures only
   after the owner integrates the exact member/role/scope contract. Replace live legacy conversion,
   retention/re-attribution/migration work with the CLEAN RESET requirement below.

These are four grouped edit locations, not four new design records. Follow the current owner
`e8kor` on the merged PR's successor amendment; do not try to push into the now-merged branch.
Then hand the approved exact profile to SDK/client/Org/crawler executors for coordinated,
installed-artifact verification. They own producer/consumer byte agreement, scope resolver,
transactional authority/race checks and actual pipeline execution. This task authorizes none
of that cutover, nor remote posting. Keep all current runtimes untouched.

## CLEAN RESET and actual Crawler re-crawl

Latest user direction supersedes migration engineering in the WIP handoff: future cutover
assumes clean stores. No data migration, legacy conversion, dual read, compatibility codec,
retention bridge or fabricated re-attribution of old records. Old pre-release data will be
explicitly discarded in the separately authorized reset and recreated by **the actual Crawler
product pipeline**. This is consistent with ADR-0068 D4; do not revive old queues merely to
preserve their old identifier meaning.

No deletion is authorized now. Before a later reset, identify exact target stores/queues and
obtain explicit destructive scope authorization. Then the cutover executor, not this packet,
proves actual Crawler acquisition → genuine client processing and signed production/submission
→ exact-byte outbox → authorized Org queued receipt/lease, with independently authorized real
review/publication only if those steps are claimed. Direct HTTP synthetic fixtures, renamed
stub outputs and old envelope-ID pipeline logs are not recrawl proof. Report dedup/change
suppression using raw `content_digest`; assert distinct principals yield distinct events,
unchanged retry remains stable under fresh bearer, conflicts have zero new effects, and
unsupported review/publication holds with zero countersigns.

## Executable evidence and limits

Files in this standalone packet:
- `vectors.py`: deterministic fixture producer and twenty mutation/state tests.
- `consumer.mjs`: independent Node canonical-byte/digest/Ed25519/JWS verifier for exported fixture.
- `generated/`: exact event bytes, wire document/signature, hashes/public test keys, retained
  record fixtures, raw content/evidence encodings. The keys are deterministic **public test seeds**
  declared in source, not live credentials. Production proof and Q use PyJWT JWS + cryptography;
  event uses real rfc8785 JCS and Ed25519. No bespoke hash or crypto algorithm.
- `test-results.txt`, `verification.json`: executed results and final scope checks.
- `sources/`, PR/source manifests and before-state snapshots: review provenance.

Run from this packet directory:

```
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python vectors.py
node consumer.mjs
```

Observed first Python run: **20 tests passed, 0.052s, exit 0**. Cases include distinct per-phase
actors; route/audience/space/operation mutation; validly signed cross-operation root substitution;
re-signed child laundering attempt; principal and retained-reference/ancestor substitution;
chronology/backdated fresh snapshot; content/evidence/signature tampering; self-ID/signature
refusal; changed-event same-operation conflict; two submitters/same raw bytes; same receipt and
ID under refreshed credential; and revoked retry refusal. Canonical refusal probes are bounded.

**Measured:** these cryptographic commitments and state-machine fixture checks execute.
**Structural proposal:** the minimal member bridge, principal mapping, exact scope resolver,
chronology and operation conflict key.
**Not established:** accepted complete schema conformance; actual JWT token renewal (the test
passes fresh *modeled verified credential contexts*, not minted/verifiable bearer tokens);
real issuer/membership/grant authority, full grant attenuation/revocation policy, full canonical
parser rejection coverage, database concurrency/durability, real local promotion/review, SDK
compatibility, pipeline cutover, Crawler recrawl or enterprise identity. Fixture history records
are deliberately simplified bindings, not replacements for existing kind-specific authority
schemas. Node checks independent bytes/crypto for this bounded proposal, not closure of all
P1-A O-3 cross-language fixtures. Green tests do not approve the proposal.
