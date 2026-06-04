# Genset Call Analyzer — Deployment Plan

**Audience:** Varun + Kala Genset stakeholders. Anyone making scope or timeline decisions about the inbound AI sales agent.

**Owner:** Varun Bhilare (KALA Platform).

**Status:** Living document — amend at the end of every week with progress, slips, scope changes.

**Companion docs:**
- [`README.md`](./README.md) — project overview & local dev setup
- [`deployment-runbook.md`](../deployment-runbook.md) — KALA platform Windows deployment patterns (NSSM / IIS / ARR / Waitress / Uvicorn)
- [`backend/agent/prompt.py`](./backend/agent/prompt.py) — current Shivangi system prompt
- [`backend/agent/bot.py`](./backend/agent/bot.py) — Pipecat pipeline definition

---

## 1. Purpose & summary

We are building a production AI inbound-sales agent for Kala Genset Pvt Ltd, delivered in two phases:

- **Phase 1 (W1–W8):** New-enquiry capture. Customer dials Kala Genset's DID → "Shivangi" (AI agent) talks in Marathi/Hindi/English → captures 18 enquiry fields → writes the lead into the existing ERP enquiries table. Sales team picks up warm leads from there.
- **Phase 2 (W8–W12):** Follow-up dynamic Q&A. Customers who already enquired (or who ask complex technical/commercial questions like *"why prefer Kirloskar over Mahindra & Mahindra?"*) get answered by the agent against a structured knowledge base. Escalates to human when out of confidence.

**Realistic timeline:** **12 weeks (3 months)** end-to-end, single developer (Varun), assuming the three risks in §8 don't bite.

---

## 2. Current state (as of 2026-05-08)

### 2.1 What works today

- ✅ Pipecat agent infrastructure end-to-end: Plivo Media Stream → FastAPI WebSocket → Sarvam STT → Sarvam LLM (sarvam-30b) → Sarvam TTS → Plivo. Verified with real test calls today.
- ✅ Audio path correct: 8 kHz mu-law, code-mixed Devanagari + Latin, "कला Genset" pronounced correctly, `के व्ही ए` (kVA) read as letter names.
- ✅ Greeting + first 7-8 fields captured cleanly on a happy-path call.
- ✅ `DEV_SKIP_IVR=mr` env var bypasses the IVR menu so calls drop straight into the agent.
- ✅ The 18-field enquiry contract defined in [`prompt.py`](./backend/agent/prompt.py) and [`bot.py:save_lead_schema`](./backend/agent/bot.py).

### 2.2 What's still fragile (open issues from today's call testing)

- ⚠ Sarvam-30b LLM occasionally hangs mid-call (saw 58s with no streaming chunks). Mitigated today with `timeout=12.0`; needs validation across 10+ calls.
- ⚠ Sarvam Saarika STT occasionally drops short utterances ("200 kVA", "मला माहित नाही") — mitigated by pinning `language="mr-IN"`; needs validation.
- ⚠ LLM occasionally leaks parenthetical option lists and internal field names ("(new_or_replacement)") into spoken text. Mitigated by switching the field list to prose; not 100% solved.
- ⚠ `save_lead` tool call has produced malformed JSON under field-pressure. Mitigated by "OMIT unknown fields" rule in the schema description.
- ⚠ Plivo DID briefly went into "invalid number" state earlier today — likely DLT/KYC churn. Needs proper resolution.

### 2.3 What's not started yet

- ❌ Lead summary frontend (current React app is the legacy call-recording analyzer).
- ❌ ERP integration (`save_lead` handler currently just logs; no write to ERP enquiries table).
- ❌ Production deployment (still running on dev laptop + ngrok tunnel).
- ❌ Phase 2 (knowledge base, RAG, follow-up flow) — not scoped beyond what's in this plan.

---

## 3. Phase 1 — New-enquiry agent + ERP

### 3.1 Goal

Inbound calls to Kala Genset's DID land on the AI agent. The agent (Shivangi) captures up to 18 enquiry fields conversationally in Marathi/Hindi/English. On call end, structured lead lands in the ERP `enquiries` table; sales team works it through their existing ERP workflow.

### 3.2 In scope

- Inbound voice agent on one Plivo DID
- All 18 enquiry fields (name, designation, company, business type, location, purpose, new vs replacement, kVA, load calculation status, phase, fuel, AMF, canopy, timeline, site-visit interest, competitor brands, budget, callback number, email, other questions)
- Lead summary dashboard for sales team (read-only, web)
- ERP write on call end with idempotency
- Call recording stored and linked to the lead row
- Production deployment on KALA Windows stack (NSSM + IIS + Plivo Media Streams)

### 3.3 Out of scope (deferred to later phases)

- Outbound calls
- Dynamic Q&A / knowledge base (Phase 2)
- Multi-call context / personalisation (each call is independent in Phase 1)
- Live human-takeover mid-call (`transfer_to_human` is logged as intent only, no real dial-out)
- Multi-tenancy or per-region routing

### 3.4 Success criteria

- 10 consecutive test calls capture ≥15 of 18 fields without hangs, mid-call silence, or repeated questions
- Captured lead lands in ERP within 30 seconds of call end
- Sales team can view + filter + listen-to + export leads from the dashboard
- Production agent uptime ≥99% over a 1-week soft-launch period
- Per-call cost <₹X/call (X to be set in Block 1 telemetry, before Block 7 soft launch)

---

## 4. Phase 2 — Follow-up dynamic Q&A

### 4.1 Goal

Returning callers — and new callers asking complex questions — get accurate answers from a structured knowledge base, not a Q&A capture form. Examples:

- *"Why should I prefer Kirloskar over Mahindra & Mahindra?"*
- *"What's your delivery lead time for 250 kVA in Goa?"*
- *"Do you do AMC contracts? What's covered?"*
- *"Is the silent canopy CPCB IV+ certified for residential use?"*
- *"Will the genset run on bio-diesel?"*

The agent answers within knowledge bounds, escalates to a human when it can't, and logs the Q&A on the existing lead record (not as a new enquiry).

### 4.2 In scope

- Knowledge base: product specs, competitor comparisons, AMC info, lead times, regulatory (CPCB IV+), financing options, common objections
- Retrieval-Augmented Generation (RAG): vector search exposed to the LLM as a tool
- New conversational flow: less interrogation, more explanation
- Intent detection at call start: new enquiry → Phase 1 flow vs follow-up → Phase 2 flow
- Escalation to human salesperson via existing `transfer_to_human` plumbing
- Q&A transcript logged on the existing lead record

### 4.3 Out of scope

- Personalisation across multiple call sessions
- Outbound call-back automation
- Voice cloning / multi-voice
- Real-time CRM lookup mid-call

### 4.4 Success criteria

- 80% of factual Q&A answered correctly on a 50-question evaluation set, validated by sales SME
- Escalation rate ≤20% (otherwise the agent is just a switchboard)
- Customer satisfaction score ≥4/5 (post-call SMS survey)

---

## 5. Gantt chart — 12-week realistic plan

```
                                         W1   W2   W3   W4   W5   W6   W7   W8   W9   W10  W11  W12
══ PHASE 1 — New-enquiry agent + ERP ═══════════════════════════════════════════════════════════════
 1. Agent reliability hardening          ████ ████ ██░░
 2. Lead summary dashboard                    ░░██ ████ ██░░
 3. ERP write integration                          ░░░░ ████ ████ ██░░
 4. Telephony hardening (DID/DLT/TLS)                        ████ ████
 5. Production deploy (NSSM + IIS + WS)                            ░░░░ ████
 6. Internal UAT + iteration                                            ░░░░ ████
 7. Soft launch (gated rollout)                                                ░░██

══ PHASE 2 — Follow-up dynamic Q&A ══════════════════════════════════════════════════════════════════
 8. Knowledge base content build                                                 ████ ████
 9. RAG retrieval plumbing                                                            ████ ██░░
10. Follow-up agent flow                                                              ░░██ ████
11. Phase 2 UAT + production rollout                                                            ████

Legend:  ████ = active work    ░░░░ = expected slip / parallel work
```

### 5.1 Key milestones

| Milestone | Date target |
|---|---|
| **M1.** Agent stably captures all 18 fields on a clean call | End of W3 |
| **M2.** ERP integration writing real lead rows; dashboard live | End of W6 |
| **M3.** Agent running on production NSSM service, accessible via prod DID | End of W7 |
| **M4.** Phase 1 soft launch begins | End of W8 |
| **M5.** Knowledge base + RAG functional in dev | End of W10 |
| **M6.** Phase 2 production rollout | End of W12 |

### 5.2 Critical-path dependencies

- Block 5 (deploy) depends on Block 4 (telephony) AND Block 1 (agent stability) being complete
- Block 7 (soft launch) cannot start until Block 6 (UAT) signs off
- Block 8 (knowledge base) is content-bound, can start in parallel with Phase 1 soft launch
- Block 10 (follow-up flow) depends on Block 9 (RAG) — sequential

---

## 6. Phase 1 — detailed breakdown

### 6.1 Block 1 — Agent reliability hardening (W1–W3)

**Owner:** Varun
**Effort:** ~12 working days
**Dependencies:** none — start immediately

#### Tasks

- [ ] LLM hang behaviour: timeout configured (done, 12s); add a fallback "क्षमा करा सर, एक मिनिट…" filler if a turn takes >5s; log per-turn latency
- [ ] STT validation pass: 20+ test calls with `language="mr-IN"` pinned, measure transcript-miss rate; if >5%, evaluate Saarika v3 or alternate provider
- [ ] Prompt iteration: stop parenthetical option leaks, stop field-name leaks, stop re-greeting, stop bundling fields
- [ ] `save_lead` robustness: validate "OMIT unknown fields" rule under malformed-input pressure; add a fallback close-call handler if tool call fails to parse
- [ ] Watchdog: if no STT transcript within 8 seconds of `user_turn_started`, prompt user to repeat ("क्षमा करा सर, परत बोलाल का?")
- [ ] Test all "I don't know" branches (kVA, phase, AMF, budget) — agent must move on gracefully and set `site_visit_ok=true` where applicable
- [ ] Telemetry: log per-turn latency for STT, LLM, TTS; per-call cost (token count + ₹)
- [ ] Resilience drill: kill Sarvam connection mid-call, kill internet briefly, verify pipeline doesn't crash hard

#### Exit criterion

10 consecutive end-to-end test calls capturing ≥15 of 18 fields without hangs, silence, or repeated questions. Average per-turn latency <5s. Per-call cost recorded.

---

### 6.2 Block 2 — Lead summary dashboard (W2–W4)

**Owner:** Varun
**Effort:** ~7 working days
**Dependencies:** Block 1 partially in flight (need lead data structure stable)

#### Tasks

- [ ] Backend: persist captured leads to MongoDB (per [`deployment-runbook.md §9`](../deployment-runbook.md))
- [ ] Backend: `GET /api/leads` (paginated list with filters: date, hot_lead, business_type, location)
- [ ] Backend: `GET /api/leads/:id` (detail: all 18 fields + transcript + recording URL)
- [ ] Backend: `GET /api/leads/:id/recording` (signed URL or proxy stream)
- [ ] Backend: `GET /api/leads/export?from=&to=` (CSV)
- [ ] Frontend: leads list view with filters
- [ ] Frontend: lead detail page (fields + audio player + scrollable transcript)
- [ ] Frontend: CSV export button
- [ ] Auth: integrate with existing KALA auth contract ([`auth-contract.md`](../auth-contract.md)); restrict to sales role

#### Exit criterion

Sales lead can log in, see today's leads, filter by hot-lead flag, click into a row, listen to the recording, read the transcript, export the day's leads as CSV.

---

### 6.3 Block 3 — ERP write integration (W3–W6)

**Owner:** Varun (engineering) + Sales lead (ERP discovery)
**Effort:** ~12 working days *if* ERP has an API; **double if not**
**Dependencies:** ERP discovery spike (W1, see §9)

#### Pre-requisite: 1-day spike in W1

Find out, definitively:

1. Does the ERP expose a documented REST API for writing enquiries?
2. If no API: can we get DB-direct write access (read-only would block automation)?
3. What's the field schema for an enquiry row? Map our 18 fields to it.
4. Is there an `enquiry_source` field? If yes, value `AI-Agent` so sales can filter.
5. Is there an idempotency key field (e.g., `external_id`) we can write to? We'll use `call_uuid`.

#### Tasks (assuming ERP API path)

- [ ] Build `backend/erp_client.py`: `create_enquiry(lead_dict) -> erp_enquiry_id`
- [ ] Field mapping: our 18 fields → ERP enquiry schema (handle missing fields gracefully)
- [ ] Idempotency: include our `call_uuid` so retries don't double-create
- [ ] `save_lead_handler` in [`bot.py`](./backend/agent/bot.py): persist locally first, then push to ERP, then ack
- [ ] Retry logic: 3 attempts with exponential backoff, then mark `sync_failed` for manual retry
- [ ] Sync status on each lead: `pending_erp` / `synced` / `sync_failed`
- [ ] Dashboard "sync failed" view + manual retry button

#### Tasks (if no API — DB-direct path, +2 weeks)

Same as above, plus:
- [ ] Get production ERP DB credentials (write access scoped to enquiries table only)
- [ ] ORM model matching ERP schema (Mongoengine/SQLAlchemy depending on ERP tech)
- [ ] Whitelist our prod server IP in ERP DB firewall
- [ ] Coordinate schema-change communication so ERP migrations don't break us silently

#### Exit criterion

Test call ends → lead appears in ERP within 30s with `enquiry_source=AI-Agent` → sales rep can see it in their normal ERP enquiries view.

---

### 6.4 Block 4 — Telephony hardening (W5–W6)

**Owner:** Varun
**Effort:** ~5 working days
**Dependencies:** none

#### Tasks

- [ ] Replace ngrok with a stable public URL on the production server (proper subdomain + TLS cert through IIS, OR direct cert on port 8443 — see Risk #1 in §8)
- [ ] Resolve the DLT/KYC issue that caused "invalid number" episodes; document Plivo support contact
- [ ] Lock in production Plivo DID; have a backup DID on standby
- [ ] Verify Plivo Media Stream config: `bidirectional="true"`, `keepCallAlive="true"`, `contentType="audio/x-mulaw;rate=8000"`, `streamTimeout="1800"`
- [ ] Set up call recording via Plivo REST API; storage on S3 OR Windows file share
- [ ] Hangup webhook (`/api/plivo/hangup`) writes call duration and final status to the lead row
- [ ] Plivo Application properly configured (Answer URL = our prod endpoint; no PHLO indirection)
- [ ] Failover: if our agent service is down, fall back to a static Plivo XML that takes a voicemail and notifies sales

#### Exit criterion

Calling the production DID from a real handset reaches the agent in <5s, audio is clean both ways, recordings land in storage with `<call_uuid>.mp3` filenames, hangup webhook fires reliably.

---

### 6.5 Block 5 — Production deploy on KALA stack (W6–W7)

**Owner:** Varun
**Effort:** ~5 working days
**Dependencies:** Block 4 done; KALA Windows server access

#### Tasks

- [ ] NSSM service `KalaGensetAgentBackend` running `uvicorn main:app --host 127.0.0.1 --port 8200`
  - Per [`deployment-runbook.md §6.3`](../deployment-runbook.md). NEVER `python main.py` — that triggers reload-loop CPU spikes (see §6.2 of the runbook).
- [ ] HTTP routes (`/api/plivo/answer`, `/api/leads/*`, `/api/health`, etc.) exposed via IIS `web.config` rewrite to `:8200`
- [ ] **WebSocket route (`/api/agent/plivo/ws`)** — see Risk #1 in §8. Either ARR-with-very-long-timeout or dedicated port 8443 bypass-IIS path.
- [ ] ARR `proxyTimeout = 600s` (`%windir%\system32\inetsrv\appcmd.exe set config -section:system.webServer/proxy /timeout:00:10:00 /commit:apphost`)
- [ ] `/api/health` and `/api/ready` endpoints + IIS Application Initialization warmup config
- [ ] Log rotation via NSSM (`AppRotateBytes 5242880`)
- [ ] `.env` deployed with prod credentials (Sarvam, Plivo, MongoDB, ERP); NOT in git
- [ ] Validation checklist from [`deployment-runbook.md §14`](../deployment-runbook.md)

#### Exit criterion

Plivo DID rings → agent answers → call completes → lead appears in ERP and on dashboard. All on production hardware, no laptop / no ngrok involved.

---

### 6.6 Block 6 — Internal UAT + iteration (W7–W8)

**Owner:** Varun + sales team (Arpita + 2-3 others)
**Effort:** ~5 working days

#### Tasks

- [ ] 30 internal test calls covering: small-shop, factory, hotel, hospital, IT-park, residential
- [ ] Edge cases: customer hangs up mid-call, customer asks for human, customer doesn't know any technical specs, customer talks fast / quietly / over background noise
- [ ] Bug log + fix loop (target: <3 days from bug raise to fix-in-prod)
- [ ] Prompt regression — verify earlier fixes haven't been undone by new prompt edits
- [ ] Cost validation: actual ₹/call vs the ceiling set in Block 1
- [ ] Lead-quality review: sales team rates each captured lead 1-5 for usability

#### Exit criterion

Sales team signs off (written) that the agent is production-ready for soft launch.

---

### 6.7 Block 7 — Soft launch (W8)

**Owner:** Varun + Sales lead
**Effort:** ongoing through W8

#### Tasks

- [ ] Route a fraction of inbound calls to the agent (e.g., after-hours only, or one DID out of two)
- [ ] Monitor `stdout.log` + dashboard daily
- [ ] Daily 15-min standup with sales team on lead quality and missed cases
- [ ] Iterate prompt based on real-call patterns (real customers ≠ test customers)
- [ ] After 1 clean week, expand to 100% of inbound traffic

#### Exit criterion

7 consecutive days of soft launch with zero P1 incidents (call drops, no-lead-captured, ERP-write failure).

---

## 7. Phase 2 — detailed breakdown

### 7.1 Block 8 — Knowledge base content build (W8–W10)

**Owner:** Sales SME (Arpita?) + Varun (structuring)
**Effort:** ~10 working days; mostly content not engineering

#### Content categories

| # | Category | Source | Format |
|---|---|---|---|
| 1 | Product specs | Existing PDF catalogs in [`catalogs/`](./catalogs/) | Markdown, one file per kVA range |
| 2 | Competitor comparisons | Sales SME interviews | Markdown table per competitor (Cummins, Mahindra Powerol, Cooper, Greaves, Eicher) |
| 3 | Service & AMC | Service team docs | Markdown — what's covered, response times, parts availability per state |
| 4 | Lead times | Operations team | Markdown — standard models vs custom, foundation prerequisites |
| 5 | Regulatory | CPCB filings + industry sources | Markdown — CPCB IV+ explanation, residential/commercial constraints |
| 6 | Financing | Finance partners | Markdown — EMI, leasing, GST input credit |
| 7 | Common objections | Sales SME interviews | Q→A pairs |
| 8 | Pricing guidance | Sales lead | Range-only (NEVER exact prices) — what drives cost up/down |

#### Format & location

All content in `backend/agent/knowledge/`. One markdown file per category. Each fact tagged for retrieval (e.g., `<!-- @tag:competitor:mahindra -->`).

#### Exit criterion

Sales SME signs off that the knowledge base accurately reflects current company positioning. Dry-run 50 sample Q&A pairs against the docs (manual evaluation, not RAG yet).

---

### 7.2 Block 9 — RAG retrieval plumbing (W9–W10)

**Owner:** Varun
**Effort:** ~5 working days

#### Tasks

- [ ] Embed knowledge base markdown chunks (Sarvam embeddings if available, else OpenAI `text-embedding-3-small`)
- [ ] Vector store: start simple — `sqlite-vss` or `pgvector`. **Do not** introduce Pinecone / Weaviate / etc. for v1.
- [ ] `knowledge_search(query: str, top_k: int = 3) -> list[passage]` tool exposed to the LLM
- [ ] Tune top-K, similarity threshold, max-context-length per call
- [ ] Cache hot queries (small in-memory LRU is enough for v1)
- [ ] Eval harness: 50-question evaluation set with expected answers; run nightly

#### Exit criterion

Standalone test (no telephony): agent answers ≥40 of the 50 evaluation questions correctly using RAG.

---

### 7.3 Block 10 — Follow-up agent flow (W10–W11)

**Owner:** Varun
**Effort:** ~7 working days

#### Tasks

- [ ] New system prompt for follow-up flow in `prompt_followup.py`: less interrogation, more explanation, knowledge_search-first
- [ ] Intent detection at call start: new enquiry → Phase 1 flow vs follow-up → Phase 2 flow
  - Heuristic v1: caller ID has prior lead + caller's first utterance contains question words (का/कशी/why/how/what) → Phase 2
  - Heuristic v2: explicit IVR option ("press 1 for new enquiry, 2 for question on existing")
  - Initial implementation: v1 only; v2 if v1 misroutes
- [ ] Confidence escalation: when LLM has low confidence, agent says "मला नक्की माहीत नाही, मी आमच्या team कडून confirm करून तुम्हाला call करते." + sets `escalation_required=true` on the lead
- [ ] Q&A logging on the existing lead's `interaction_log` array (not as a new enquiry row)

#### Exit criterion

Test case: returning customer asks "why Kirloskar over Mahindra?" → agent answers from knowledge base → captures the Q&A in the lead's history.

---

### 7.4 Block 11 — Phase 2 UAT + production rollout (W11–W12)

**Owner:** Varun + sales team
**Effort:** ~7 working days

Same pattern as Block 6 (UAT) and Block 7 (soft launch). Specific evaluation focus: factual correctness vs hallucination rate.

#### Exit criterion

Phase 2 has run in production for ≥1 week with no critical incidents. Hallucination rate ≤5% on sampled call recordings.

---

## 8. Risks & mitigations

### Risk 1 — WebSocket-through-IIS is not paved road on KALA stack 🟥 HIGH

**What:** Plivo Media Streams is a persistent bidirectional WebSocket that can last 30+ minutes per call. The KALA stack reverse-proxies HTTP through IIS+ARR. ARR's WebSocket support exists but has historical quirks — idle disconnects, timeout enforcement, header rewrites that break frame ordering.

**Impact if it bites:** Block 5 slips by 1-2 weeks. Customer calls drop mid-conversation in production.

**Mitigations:**
- **Plan A:** Configure IIS for WebSocket (`<webSocket enabled="true" />` in `applicationHost.config`), bump ARR `proxyTimeout` to 600s+, test under real load before go-live.
- **Plan B (fallback):** Open a dedicated TLS port (e.g., 8443) for the agent WebSocket only, bypass IIS for that path. HTTP routes (dashboard, ERP webhooks) still go through IIS as normal. Means another firewall rule and another cert renewal cycle.
- **Action this week:** Spike Plan A on the production server, time-box to 2 days. If it doesn't work cleanly, switch to Plan B.

---

### Risk 2 — ERP integration scope is unknown 🟥 HIGH

**What:** No documented evidence today on whether Kala Genset's ERP exposes a write API for enquiries. If only UI exists, we'd need DB-direct writes or screen-scraping — both materially harder.

**Impact if no API:** Block 3 doubles from 12 days to ~24 days. Pushes Phase 1 launch from W8 to W10.

**Mitigation:** **1-day spike in W1.** Answer the question definitively before committing to W6 milestone date. Update this plan immediately based on the answer.

---

### Risk 3 — Sarvam-30b instability 🟧 MEDIUM

**What:** Today the agent occasionally hangs (LLM API not responding for 60+ seconds) or leaks formatting (option lists, field names). Even with timeouts and prompt fixes, the model is small for the instruction-following load we're putting on it.

**Impact if it persists:** Customer experience suffers. UAT signoff blocked.

**Mitigations:**
- LLM `timeout=12.0` configured today — needs validation
- Watchdog message on slow LLM ("क्षमा करा सर, एक मिनिट...") — to be implemented in Block 1
- **Fallback:** Switch the LLM to GPT-4o-mini or Claude Haiku via OpenRouter. ~3× cost per call but materially more reliable on instructions and tool calls. 2-3 days to swap.

---

### Risk 4 — Telephony / DLT compliance 🟧 MEDIUM

**What:** Earlier today the Plivo DID returned "invalid number" to callers — likely DLT/KYC compliance issue with TRAI rules.

**Impact if it recurs in soft launch:** Soft launch blocked. Customer-trust damage if real customers can't reach us.

**Mitigations:**
- Get DLT compliance fully documented; lock in the contact at Plivo support
- Have a backup DID on standby (different number, same agent)
- Health check that periodically calls the DID and verifies it routes — alert on failure

---

### Risk 5 — Per-call cost surprise 🟨 LOW–MEDIUM

**What:** Sarvam STT + LLM + TTS for a 5-minute call: cost not yet measured. At 1000 calls/day, monthly bill multiplies fast.

**Impact if too high:** Business model questioned, potentially de-scope features.

**Mitigations:**
- Measure per-call cost in Block 1 telemetry (token count + ₹) → set hard ceiling in Block 6
- Compare to manual sales-rep cost per call (loaded cost ~₹X/min in India) — agent should be 3-10× cheaper to be worth it
- Lock budget with finance before soft launch

---

## 9. Open questions — W1 spikes

These need definitive answers before the plan firms up. Each is a 0.5-1 day spike.

1. **Does the ERP expose a write API for enquiries?** Determines whether Block 3 is 12 or 24 days.
2. **Is the production Windows server provisioned?** Or do we need to procure one? Affects W6 deploy date.
3. **Is the DLT/KYC issue resolved on the production DID?** Or do we need a new DID?
4. **What's the recording storage decision?** S3 bucket vs Windows file share. Retention policy (90 days? 1 year? indefinite)?
5. **Auth on the leads dashboard:** integrate with existing KALA `auth-contract.md`, or simple shared-password for v1?
6. **Call routing during transition:** all calls to agent on day 1, or co-existence with the manual line for X weeks?
7. **SMS feedback survey:** Phase 1 (cost-quality tracking) or Phase 2?
8. **Multi-DID future:** will Phase 2 need separate DIDs per region/language, or stay single-DID?

---

## 10. Architecture notes

### 10.1 How this fits the KALA platform

Per [`deployment-runbook.md`](../deployment-runbook.md), KALA backends follow a standard pattern:

```
[client] → IIS:443 → [URL rewrite → ARR proxy] → NSSM-wrapped Waitress/Uvicorn → Python app → external services
```

The Genset Call Analyzer follows this pattern with **one wrinkle: persistent WebSockets** for Plivo Media Streams. See Risk 1 for how we handle that.

### 10.2 Production topology

```
                                        Plivo (telco)
                                              │
                                              │ WSS (long-lived, 5-30 min/call)
                                              ▼
                                   ┌──────────────────────┐
                                   │ Windows Server (prod)│
                                   │                      │
                                   │ ┌──────────────────┐ │
                                   │ │ IIS  :443        │ │  ← HTTP routes (/api/leads/*, /api/health)
                                   │ │  ↓ ARR proxy     │ │  ← timeout bumped to 600s
                                   │ │  → :8200         │ │
                                   │ └──────────────────┘ │
                                   │                      │
                                   │ ┌──────────────────┐ │
                                   │ │ Direct :8443     │ │  ← OR — WebSocket-only port
                                   │ │  → :8200         │ │     (Plan B for Risk 1)
                                   │ └──────────────────┘ │
                                   │           │          │
                                   │           ▼          │
                                   │ ┌──────────────────┐ │
                                   │ │ NSSM:            │ │
                                   │ │  uvicorn         │ │
                                   │ │  main:app        │ │
                                   │ │  127.0.0.1:8200  │ │
                                   │ └──────────────────┘ │
                                   │           │          │
                                   │           ▼          │
                                   │ ┌──────────────────┐ │
                                   │ │ FastAPI app:     │ │
                                   │ │  /api/plivo/*    │ │
                                   │ │  /api/agent/ws   │ │
                                   │ │  /api/leads/*    │ │
                                   │ │  /api/health     │ │
                                   │ └──────────────────┘ │
                                   └──────────┬───────────┘
                                              │
                ┌─────────────────────────────┼──────────────────────────────┐
                ▼                             ▼                              ▼
          Sarvam AI                     Kala Genset ERP                S3 / file share
       (STT + LLM + TTS)              (enquiries write)              (call recordings)
```

### 10.3 What's the same as other KALA apps

- NSSM-wrapped Python service (per runbook §6)
- Bound to localhost only
- IIS reverse-proxy for HTTP (per runbook §5)
- `web.config` rewrite rules (specific before generic)
- Health-check endpoints `/api/health` + `/api/ready`
- JSON-Lines logging (for tail+jq workflow)
- MongoDB Atlas pattern with `serverSelectionTimeoutMS=10000` (per runbook §9)

### 10.4 What's different

- **Persistent WebSocket** for telephony — needs IIS WS support OR a dedicated port (Risk 1)
- **Higher backend timeouts** — calls run 5-10 minutes. ARR `proxyTimeout=600s` minimum.
- **Heavy dependency footprint** — Pipecat pulls in `torch`, `silero`, `onnxruntime`. First cold-start of NSSM service takes ~15s.
- **External-cost-per-request is materially higher** than text-only services (Sarvam audio APIs cost more per minute than text APIs).
- **Plivo recording webhook** — separate POST endpoint to receive recording URL after call ends, not in the WebSocket path.

---

## 11. Definition of done — Phase 1

The plan is "done" for Phase 1 when **ALL** of these are true:

- [ ] Agent reliably captures all 18 enquiry fields on a clean call (≥15 of 18 on edge cases)
- [ ] Captured leads land in ERP within 30s of call end, marked `enquiry_source=AI-Agent`
- [ ] Sales team can view, filter, listen to, and export leads from the dashboard
- [ ] Production agent runs as a Windows NSSM service with auto-restart on crash
- [ ] Plivo DID is stable; DLT compliance verified and documented
- [ ] Soft launch has run for ≥1 week with zero P1 incidents
- [ ] Per-call cost is documented and within the budget ceiling set in Block 1
- [ ] Sales lead has signed off (written)

## 12. Definition of done — Phase 2

- [ ] Knowledge base covers all 8 content categories with SME sign-off
- [ ] Agent answers ≥80% of factual evaluation-set questions correctly
- [ ] Escalation rate ≤20% in real calls
- [ ] Returning callers automatically routed to follow-up flow (intent detection works)
- [ ] Phase 2 has run in production for ≥1 week with no critical incidents
- [ ] Hallucination rate ≤5% on sampled call recordings (manual review by SME)

---

## 13. Operational runbook (post-launch)

Once Phase 1 is deployed, day-to-day ops follows the standard KALA pattern in [`deployment-runbook.md`](../deployment-runbook.md). Specific to this app:

### 13.1 Daily checks

```powershell
# 1. Service status
nssm status KalaGensetAgentBackend     # expect SERVICE_RUNNING

# 2. Health
Invoke-WebRequest http://localhost:8200/api/health -UseBasicParsing
Invoke-WebRequest http://localhost:8200/api/ready -UseBasicParsing

# 3. Lead count today vs yesterday (sanity check)
Invoke-WebRequest "http://localhost:8200/api/leads?from=$(Get-Date -Format 'yyyy-MM-dd')&count=true"

# 4. Sarvam connectivity
Invoke-WebRequest http://localhost:8200/api/debug/sarvam

# 5. ERP sync queue
Invoke-WebRequest "http://localhost:8200/api/leads?status=sync_failed"
```

### 13.2 Common incidents

| Symptom | Likely cause | Fix |
|---|---|---|
| Calls not connecting | Plivo DID issue | Check Plivo console, contact Plivo support if number is suspended |
| Calls connect but agent silent | Sarvam API down | Check `stdout.log` for `timeout` errors; switch to fallback LLM if Sarvam outage prolonged |
| Leads captured but not in ERP | ERP API down or auth expired | Check `/api/leads?status=sync_failed`; manual retry button on dashboard |
| Calls drop mid-conversation | ARR/IIS WebSocket timeout (Risk 1) | Verify ARR `proxyTimeout`; check IIS WebSocket module enabled |
| 502 from dashboard | NSSM service crashed | `nssm restart KalaGensetAgentBackend`; check `stderr.log` for crash |

### 13.3 Escalation path

- **L1 (Varun):** All software issues
- **L2 (Plivo support):** DID issues, DLT compliance
- **L2 (Sarvam support):** STT/LLM/TTS quality regressions
- **L2 (ERP team):** ERP write failures, schema changes

---

## 14. Document history

| Date | Change | By |
|---|---|---|
| 2026-05-08 | Initial deployment plan. Phase 1 + Phase 2 with 12-week realistic Gantt. Captures current agent issues (LLM hangs, STT misses, DID DLT churn) and their mitigations. | Varun + AI assistance |
