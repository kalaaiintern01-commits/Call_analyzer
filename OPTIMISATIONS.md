# Optimisations — Performance & Reliability Wins

This document is a running log of the performance and reliability fixes
shipped to Hire Sense. Each entry follows the same shape:

- **Problem** — the symptom the user / dev saw
- **Diagnosis** — what was actually causing it
- **Fix** — the code change with file references
- **Win** — measured impact, in numbers where possible

If you're chasing a "why is X slow / flaky" question, scan the Problem
column first. If you're refactoring one of these subsystems, read the
matching Diagnosis + Fix so you don't accidentally regress the work.

> Companion docs: [ARCHITECTURE.md](./ARCHITECTURE.md) for system overview,
> [ENGINEERING_NOTES.md](./ENGINEERING_NOTES.md) for algorithms, this doc
> for *why the code looks the way it does in the hot paths*.

---

## Table of Contents

1. [Role catalog payload — `/admin/roles` 30–60% size cut](#1-role-catalog-payload----adminroles-30-60-size-cut)
2. [Lean dropdown endpoint — `/admin/roles/dropdown`](#2-lean-dropdown-endpoint--adminrolesdropdown)
3. [On-demand full-role fetch — `/admin/roles/{role_id}`](#3-on-demand-full-role-fetch--adminrolesrole_id)
4. [Question-count aggregate — kills N+1 round-trips](#4-question-count-aggregate--kills-n1-round-trips)
5. [MongoDB Atlas pool tuning](#5-mongodb-atlas-pool-tuning)
6. [Compare-by-role / by-opportunity indexes](#6-compare-by-role--by-opportunity-indexes)
7. [Audio-chunk collection split + lookup index](#7-audio-chunk-collection-split--lookup-index)
8. [TTL indexes for staged data](#8-ttl-indexes-for-staged-data)
9. [Deterministic LLM scoring (temperature + jitter)](#9-deterministic-llm-scoring-temperature--jitter)
10. [Role-fit retrieval — gamma bonus + same-dept floor](#10-role-fit-retrieval--gamma-bonus--same-dept-floor)
11. [Legacy duplicate-eval handling (`sort=created_at desc`)](#11-legacy-duplicate-eval-handling)
12. [Batch-fetch joins on compare endpoints](#12-batch-fetch-joins-on-compare-endpoints)
13. [E2E test harness — mongomock + dep override](#13-e2e-test-harness--mongomock--dep-override)
14. [Open optimisations (not yet shipped)](#14-open-optimisations-not-yet-shipped)

---

## 1. Role catalog payload — `/admin/roles` 30–60% size cut

**Problem.** Opening any admin tab that fetched roles (Create Invitation,
Compare, Bulk Invite preview, Test Interview) took 2–5 seconds and shipped
1–3 MB over the wire for ~600 roles. The frontend hung visibly.

**Diagnosis.** Each role doc carries an `index_entry` field built by
`services/role_index.py::build_role_index_entry()` — a token bag of 50–200
strings per role used by the prefilter at finalize time. Grep proved the
frontend reads it zero times. Pure server-side bloat.

**Fix.** Project it out in [`backend/routes/admin.py:61`](../backend/routes/admin.py#L61):

```python
roles = await job_roles_collection.find({}, {"index_entry": 0}).collation(
    {"locale": "en", "strength": 2}
).sort("title", 1).to_list(10000)
```

The prefilter at [`backend/routes/interview.py:~1480`](../backend/routes/interview.py)
does its own `find()` with `index_entry` included — untouched by the projection.

**Win.** Payload drops 30–60% with zero behavioural change. Combined with §2
below, the Create Invitation dropdown went from 2–5 s to under 1 s.

---

## 2. Lean dropdown endpoint — `/admin/roles/dropdown`

**Problem.** Even after stripping `index_entry`, `/admin/roles` still ships
~600 KB of R&R prose, KPIs, ideal_scores, and scoring_weights to render
what is, in most UIs, a `<select>` showing just `title + department`.

**Diagnosis.** Most admin contexts (invitation form, compare tab, bulk-invite
preview, test-interview modal, quick-invite modal, resume-pool tab) only need
six fields per role plus `question_count` for the readiness chip. Returning
the full doc is wasted bandwidth on every page open.

**Fix.** New endpoint at [`backend/routes/admin.py:86`](../backend/routes/admin.py#L86):

```python
@router.get("/roles/dropdown")
async def list_roles_for_dropdown(user=Depends(require_admin)):
    projection = {
        "_id": 1, "title": 1, "department": 1, "seniority_level": 1,
        "grade_level": 1, "grade_category": 1, "is_active": 1,
    }
    roles = await job_roles_collection.find({}, projection).collation(
        {"locale": "en", "strength": 2}
    ).sort("title", 1).to_list(10000)
    # question_count via single aggregate — see §4
    ...
```

**Six call sites** migrated in [`frontend/src/pages/AdminDashboard.jsx`](../frontend/src/pages/AdminDashboard.jsx):
- `CompareTab` (line ~128)
- `CreateInvitationTab` initial load (~1177) + post-Q&A-gen refresh (~1411)
- `UploadQATab` (~2024)
- `ResumePoolTab` (~2853)
- `QuickInviteModal` (~3307)
- `TestInterviewModal` (~4180)

Kept on full `/admin/roles`:
- `RolesTab` refresh (~497) — needs R&R, KPIs, scoring_weights for inline edit
- `OpportunitiesTab` (~2109) — same reason

**Win.** Dropdown payload: **~1–3 MB → ~30–60 KB (~20× smaller).** Cold-pool
Create Invitation dropdown opens in **<1 s** (was 2–5 s). Saved bandwidth
compounds across every admin session.

> **Route ordering trap.** `/roles/dropdown` MUST be declared before
> `/roles/{role_id}` in [`admin.py`](../backend/routes/admin.py) so FastAPI's
> literal route wins precedence over the path-parameter route. Otherwise
> `dropdown` is captured as a `role_id`, the route fires `ObjectId("dropdown")`,
> and returns 400.

---

## 3. On-demand full-role fetch — `/admin/roles/{role_id}`

**Problem.** Migrating the invitation form to `/admin/roles/dropdown`
broke the "Auto-fill from role R&R" button — it needed `description`,
`roles_and_responsibilities`, `required_skills`, which the lean payload
no longer carries.

**Diagnosis.** Re-fetching the full catalog just to populate one role's
auto-fill is the same problem the dropdown was solving. The correct shape
is a single-role fetch.

**Fix.** New endpoint at [`backend/routes/admin.py:123`](../backend/routes/admin.py#L123):

```python
@router.get("/roles/{role_id}")
async def get_role_detail(role_id: str, user=Depends(require_admin)):
    try:
        oid = ObjectId(role_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid role_id")
    role = await job_roles_collection.find_one({"_id": oid}, {"index_entry": 0})
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    role["_id"] = str(role["_id"])
    return {"role": role}
```

Frontend Auto-fill button rewritten to `await api.get('/admin/roles/${form.role_id}')`.

**Win.** Architecturally clean: list-views fetch a thin index, detail
views fetch one role on demand. Same shape as the role-retrieval prefilter
(index entry = "key", full doc = "decoded value").

---

## 4. Question-count aggregate — kills N+1 round-trips

**Problem.** `/admin/roles` previously did one `count_documents()` per role
to populate the `🟢/🟡/🔴` readiness chip. With ~600 roles that's 600
sequential round-trips to Atlas (~30–50 ms each) — **15–30 seconds** of
network wait, and frequent endpoint timeouts.

**Diagnosis.** A trivial group-by `role_id` aggregation does the same work
in one round-trip.

**Fix.** Replaced the per-role loop with a single aggregate
([`admin.py:69-77`](../backend/routes/admin.py#L69-L77)):

```python
counts_by_role = {}
if roles:
    cursor = question_banks_collection.aggregate([
        {"$match": {"is_active": True}},
        {"$group": {"_id": "$role_id", "count": {"$sum": 1}}},
    ])
    async for row in cursor:
        counts_by_role[row["_id"]] = row["count"]

for r in roles:
    r["question_count"] = counts_by_role.get(r["_id"], 0)
```

Same pattern reused inside `/admin/roles/dropdown` (§2).

**Win.** 600 round-trips → 1. The `/admin/roles` endpoint went from
periodic timeouts to **sub-second** on the warm pool.

---

## 5. MongoDB Atlas pool tuning

**Problem.** After running the app for a few minutes, the first request
after any idle gap consistently paid a **~200–500 ms** TLS-handshake stall.
Worse, occasional `AutoReconnect: connection closed` 500s appeared with
no obvious code-side cause.

**Diagnosis.** MongoDB Atlas closes idle TCP connections at ~10 minutes
server-side. If motor's pool hands a dead socket to the next request, it
fails with `AutoReconnect`. The default motor pool keeps connections
indefinitely, so dead sockets stay in the pool until they bite.

A first fix shipping `maxIdleTimeMS=60_000` (recycle every minute) fixed
the AutoReconnect errors but introduced **per-minute** TLS handshakes on
casual admin navigation — slow first request after every minute of idle.

**Fix.** Tuned values in [`backend/database.py`](../backend/database.py):

```python
client = AsyncIOMotorClient(
    MONGODB_URI,
    maxIdleTimeMS=300_000,         # recycle pool conns every 5 min
    serverSelectionTimeoutMS=10_000,  # fail fast (10 s) vs 30 s default
    retryReads=True,
    retryWrites=True,
)
```

Reasoning:

| Setting | Value | Why |
|---|---|---|
| `maxIdleTimeMS` | 300 000 (5 min) | Beats Atlas's ~10 min idle-disconnect, but 5× fewer handshakes than the 1-min setting |
| `serverSelectionTimeoutMS` | 10 000 | FE gets a clear error in 10 s instead of hanging for 30 |
| `retryReads` / `retryWrites` | True | Pymongo defaults — restated explicitly so AutoReconnect on a stale socket auto-retries on a fresh one |

**Win.** AutoReconnect 500s eliminated. TLS-handshake frequency dropped
from "every minute of casual navigation" to "every 5 min" — invisible to
the user.

---

## 6. Compare-by-role / by-opportunity indexes

**Problem.** The admin Compare tab stalls noticeably once a role has
dozens of evaluations. `compare-by-opportunity` similarly slow.

**Diagnosis.** Without indexes, MongoDB does a full collection scan on
`interview_evaluations_collection` per request. Sort-by-`overall_score`
inside a `find()` only helps if there's an index it can walk.

**Fix.** Boot-time index creation in [`database.py::ensure_indexes()`](../backend/database.py):

```python
await interview_evaluations_collection.create_index([("role_id", 1), ("overall_score", -1)])
await interview_evaluations_collection.create_index("session_id")
await applications_collection.create_index("opportunity_id")
await interview_invitations_collection.create_index("linked_application_id")
```

Compound `(role_id, overall_score desc)` lets the compare-by-role query
hit the index for both filter and sort. `session_id` index speeds up
the duplicate-eval-dedupe path (see §11).

**Win.** Compare tab stays sub-second even with hundreds of evaluations.

---

## 7. Audio-chunk collection split + lookup index

**Problem.** Long interviews (>9 min of audio) crashed at finalize with
`document exceeds maximum allowed bson document size of 16777216 bytes`.
The session doc was carrying every audio segment inline.

**Diagnosis.** MongoDB's 16 MB document limit is a hard ceiling. Audio
data scales linearly with interview duration, so a long-running session
will hit the cap. Embedding all audio chunks in the session doc was a
design that worked for short demos and failed silently at scale.

**Fix.** New collection `interview_audio_chunks_collection` keyed by
`(session_id, seq)`, one doc per ~15 s webm chunk
([`database.py:46`](../backend/database.py#L46)). Plus a lookup index
for the finalize task which scans by session and sorts by seq
([`database.py:117`](../backend/database.py#L117)):

```python
await interview_audio_chunks_collection.create_index([("session_id", 1), ("seq", 1)])
```

Chunks are deleted after the session's full transcript is computed.

**Win.** No more 16 MB ceiling. Finalize transcript stitching uses the
index instead of full-collection scans.

---

## 8. TTL indexes for staged data

**Problem.** Two collections accumulate temporary docs that nobody cleans up:
- `email_attachments_collection` — invitation attachments staged during compose
- `pending_registrations_collection` — email+password signups awaiting OTP

Without a TTL these grow forever, slowing queries and bloating Atlas usage.

**Fix.** TTL indexes in [`database.py::ensure_indexes()`](../backend/database.py):

```python
await email_attachments_collection.create_index(
    "uploaded_at", expireAfterSeconds=60 * 60 * 24,  # 24 h
)
await pending_registrations_collection.create_index(
    "created_at", expireAfterSeconds=60 * 15,  # 15 min
)
await pending_registrations_collection.create_index("email", unique=True)
```

The `email` unique index also enforces "one pending registration per email
at a time" — re-initiating signup overwrites the prior pending row instead
of stacking duplicates.

**Win.** Self-cleaning collections. Zero ops cost for either staging path.

---

## 9. Deterministic LLM scoring (temperature + jitter)

**Problem.** Re-running role-fit recommendation on the same eval doc
returned different top-3 roles each time. Two interviews scored within
hours of each other gave wildly different recommendations.

**Diagnosis.** Two non-determinism sources stacked:
1. LLM evaluator + role-fit prompt ran with default `temperature` (~1.0)
2. Prefilter score had a `_JITTER` term injected for tie-breaking

Both are fine for creative tasks; neither is acceptable for a hiring
decision that needs to be reproducible across re-runs.

**Fix.** In [`backend/services/ai_service.py`](../backend/services/ai_service.py):
- Evaluator LLM call: `temperature=0.1`
- Role-fit LLM call: `temperature=0.1`

In [`backend/services/role_index.py`](../backend/services/role_index.py):
- `_JITTER = 0.0` (was a small random nudge)

**Win.** Re-running the same eval through `refresh_role_fit` produces
identical top-3 roles. Audit + recovery tooling now meaningful.

---

## 10. Role-fit retrieval — gamma bonus + same-dept floor

**Problem.** Kedar Ambikar (a CHRO candidate) was getting role-fit
recommendations like "HR Head (BU)" and two "Senior Manager" roles from
unrelated departments. The obviously correct match — Group CHRO — wasn't
even in the top-10.

**Diagnosis.** The prefilter is a two-stage retrieval: hash-set + nearest-K
on the role_index token bag. Its scoring summed token-overlap minus
similarity, with a small same-department bonus
(`_GAMMA_DEPT_BONUS = 1.5`). For tight semantic clusters like HR or
Leadership, 1.5 isn't enough — cross-department roles with overlapping
generic tokens ("management", "stakeholder") drown out the obvious in-dept
match.

**Fix.** Two changes in [`backend/services/role_index.py`](../backend/services/role_index.py):

1. `_GAMMA_DEPT_BONUS = 4.0` (was 1.5) — same-dept matches surface more
   aggressively.
2. **Same-department floor**: in `prefilter_roles`, reserve `k // 4` slots
   for same-department roles before falling back to cross-dept candidates.
   Guarantees that even if the global top-K is all cross-dept noise,
   at least 25% of the candidate pool is from the right department.

Also bumped `k` from 50 → 80 in the call site at
[`routes/interview.py`](../backend/routes/interview.py) so the LLM
re-ranker sees a larger candidate pool.

**Win.** Group CHRO now surfaces as the top role-fit for CHRO candidates.
Same-cluster recommendations stay stable across re-runs (combined with §9).

---

## 11. Legacy duplicate-eval handling

**Problem.** Some candidates had two `interview_evaluations` docs for the
same `session_id`. The "Hire vs Maybe" mismatch on Kedar's report — header
showed "Hire", inside showed "Maybe" — was the symptom. Different endpoints
were reading different copies of the duplicate.

**Diagnosis.** The legacy `/end` endpoint was non-idempotent. A double
click on "End Interview", or `is_complete` arriving before the End button
fired, inserted two eval docs. The first read of any endpoint returned
whichever Mongo happened to find first.

**Fix.** Two-pronged:

**(a) Read path** — every endpoint that resolves an eval by `session_id`
now pins to the latest:

```python
target = await interview_evaluations_collection.find_one(
    {"session_id": sid},
    sort=[("created_at", -1)],
    projection={"_id": 1},
)
```

Applied across 5 endpoints in
[`routes/evaluation.py`](../backend/routes/evaluation.py) and
[`routes/interview.py`](../backend/routes/interview.py).

**(b) Write path** — `/end` is now idempotent: if an eval exists for
the session, return it instead of inserting a new one.

**(c) One-shot cleanup** — [`backend/scripts/dedupe_evaluations.py`](../backend/scripts/dedupe_evaluations.py)
walks the collection, finds duplicate `session_id` groups, keeps the
latest, deletes the rest. Idempotent — safe to re-run.

**Win.** "Hire vs Maybe" mismatch gone. New interviews can never produce
duplicates. Compare-tab dedupe code can simplify once all legacy dupes
are flushed.

---

## 12. Batch-fetch joins on compare endpoints

**Problem.** `compare-by-opportunity` issued **3 × N** round-trips:
one per evaluation to fetch its session, candidate, and role. With 50
evaluations that's 150 sequential round-trips before any data is shaped.

**Fix.** Replaced with three `$in` queries in
[`routes/evaluation.py:579-591`](../backend/routes/evaluation.py#L579-L591):

```python
sessions = await interview_sessions_collection.find(
    {"_id": {"$in": eval_session_ids}}, {...projection}
).to_list(len(eval_session_ids) or 1)
cands = await users_collection.find(
    {"_id": {"$in": candidate_ids}}, {...}
).to_list(len(candidate_ids) or 1)
role_docs = await job_roles_collection.find(
    {"_id": {"$in": role_ids}}, {...}
).to_list(len(role_ids) or 1)
```

Then build `sessions_by_id`, `cands_by_id`, `roles_by_id` dicts and join
in Python.

**Win.** 3 × N round-trips → 3. For 50-candidate opportunities the
endpoint went from multi-second to sub-second.

---

## 13. E2E test harness — mongomock + dep override

**Problem.** No tests existed. Every change was verified by manually
clicking through the admin UI. Cross-cutting refactors (the optimisations
above, the 11-attribute extension, the HR Final panel changes) had no
regression net.

**Fix.** Added a pytest suite at [`backend/tests/`](../backend/tests/)
that runs the real FastAPI app against `mongomock-motor`'s in-memory
Mongo plus `app.dependency_overrides` to bypass JWT minting in test code.
Three files:

- [`backend/pytest.ini`](../backend/pytest.ini) — pytest config, asyncio auto-mode
- [`backend/tests/conftest.py`](../backend/tests/conftest.py) — fixtures:
  patches `motor.AsyncIOMotorClient` → `mongomock_motor.AsyncMongoMockClient`
  BEFORE `database.py` imports; overrides `require_admin` / `get_current_user`;
  seeds a canonical 2-role / 1-eval test dataset before every test
- [`backend/tests/test_e2e.py`](../backend/tests/test_e2e.py) — 11 tests
  covering: OpenAPI surface, `/admin/roles` payload + projection invariants,
  `/admin/roles/dropdown` lean shape, `/admin/roles/{role_id}` happy +
  404 + 400 paths, compare-by-role join, HR-override PATCH round-trip

Two notable conftest tricks:

1. **Patch order**. `database.py` builds the motor client at import time,
   so the motor swap MUST happen before any backend import — done at
   module top of conftest.
2. **mongomock collation shim**. `mongomock-motor`'s cursor implements
   `.collation()` as a no-op returning `None`, breaking the
   `.find().collation().sort()` chain used by `/admin/roles*`. Patch the
   probe cursor class once so `.collation()` returns `self` (chainable).

**Run:**

```powershell
cd ai-interviewer-bot\backend
& "..\venv\Scripts\python.exe" -m pytest -v
```

**Win.** 11 tests, **0.12 s** full-suite runtime. CI-ready. Lock-in
covers the index_entry projection, the dropdown payload shape, and the
HR override authoritative write path — exactly the routes most at risk
of accidental regression.

---

## 14. Open optimisations (not yet shipped)

Listed here so they aren't forgotten:

- **Frontend role map cache.** Storing `{role_id → role}` map locally
  in `AdminDashboard` after the dropdown fetch means most picker
  selections wouldn't need any backend call at all. Today each picker
  refetches the lean catalog on tab change.
- **Question-bank pagination.** `/admin/roles` returns 10 000 roles max;
  if the catalog grows past a few thousand we should paginate the full
  endpoint too. The dropdown endpoint is fine — it's 30–60 KB at 600
  roles, scales linearly.
- **LLM streaming for evaluator output.** Currently the evaluator waits
  for the full JSON before parsing. Streaming with incremental parse
  would shave ~2–5 s off the user-visible "finalising" spinner.
- **Mongo connection warm-up at boot.** `recover_unfinalized_sessions`
  already runs at startup and hits Mongo — so the first user request
  doesn't pay TLS-handshake cost. But if the lifespan task finishes
  before a user shows up, the warm pool goes idle. Could keep a periodic
  no-op ping.
- **CDN for static assets.** Vite builds inlined fonts and icon SVGs.
  Moving the top-N assets to a CDN would cut admin-dashboard cold load.

---

## Appendix — Files most affected by the optimisations above

| File | Sections |
|---|---|
| [`backend/database.py`](../backend/database.py) | §5, §6, §7, §8 |
| [`backend/routes/admin.py`](../backend/routes/admin.py) | §1, §2, §3, §4 |
| [`backend/routes/evaluation.py`](../backend/routes/evaluation.py) | §11, §12 |
| [`backend/routes/interview.py`](../backend/routes/interview.py) | §10, §11 |
| [`backend/services/ai_service.py`](../backend/services/ai_service.py) | §9 |
| [`backend/services/role_index.py`](../backend/services/role_index.py) | §9, §10 |
| [`backend/scripts/dedupe_evaluations.py`](../backend/scripts/dedupe_evaluations.py) | §11 |
| [`backend/tests/conftest.py`](../backend/tests/conftest.py) | §13 |
| [`backend/tests/test_e2e.py`](../backend/tests/test_e2e.py) | §13 |
| [`frontend/src/pages/AdminDashboard.jsx`](../frontend/src/pages/AdminDashboard.jsx) | §2, §3 |
