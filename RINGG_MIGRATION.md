# Migration Plan: Replace Sarvam + OpenRouter + Pipecat with Ringg AI

A complete reference for switching the Kala Genset voice agent from the current self-hosted Pipecat pipeline to the **Ringg AI** managed voice-AI platform.

> **Status:** Plan only. No code changes have been made. Do not execute any step in this document without explicit authorisation.

---

## 1. Executive Summary

### What we have today

A self-hosted, code-controlled voice agent built from individually-managed cloud APIs:

- **Telephony:** Plivo (inbound + outbound, Media Streams over WebSocket)
- **STT:** Sarvam Saarika v2.5 (`mr-IN` / `hi-IN` / `en-IN`)
- **LLM:** Claude Haiku 4.5 via OpenRouter
- **TTS:** Sarvam Bulbul v2 (voice `manisha`)
- **Orchestration:** Pipecat 1.1.0 (Python framework that wires STT->LLM->TTS in a streaming pipeline)
- **Backend:** FastAPI + uvicorn on Windows, exposed via ngrok
- **Persistence:** SQL Server (`ERPAI` for read; `call_analyzer.dbo.AICallSummary` for write)
- **Frontend:** React + Vite

### What we'd have after Ringg AI

A managed-platform agent. Ringg AI takes over STT, LLM, TTS, and conversation orchestration. We keep telephony (Plivo) only if Ringg cannot ingest from Plivo directly. Our backend's role shrinks to:

1. Webhook receiver for Ringg events (call.started, transcript.update, call.ended)
2. Persistence layer (SQL Server stays exactly as-is)
3. ERP read API (Call Customers tab continues to work unchanged)
4. Frontend (no change needed for the Summary/Call Customers tabs)

The biggest architectural shift: **we no longer own the call loop.** Ringg's infrastructure runs the conversation; we observe via webhooks.

---

## 2. Architecture: Before vs After

### Current (today)

```
Customer phone
  -> Plivo carrier network
    -> Plivo Media Stream (mu-law 8kHz over WebSocket)
      -> our FastAPI backend (via ngrok)
        -> Pipecat pipeline:
             - SarvamSTTService (mr/hi/en-IN)
             - OpenAILLMService routed via OpenRouter to anthropic/claude-haiku-4.5
             - SarvamTTSService (manisha voice, 8kHz mu-law)
        -> save_lead tool callback -> erp.insert_or_update_summary -> dbo.AICallSummary
        -> post-call extractor (LLM-based, fallback path)
      -> WebSocket back to Plivo
    -> Plivo
  -> Customer phone speaker
```

Our code orchestrates every microsecond. We pay per-token / per-second for each of Sarvam, OpenRouter, Plivo separately.

### Target (Ringg AI)

```
Customer phone
  -> Telephony layer (Ringg-native OR Plivo handing off to Ringg)
    -> Ringg AI managed infrastructure:
         - Their STT
         - Their LLM (likely OpenAI / Anthropic on their account)
         - Their TTS
         - Conversation state machine + barge-in + interruption handling
         - Custom-tool dispatcher (our save_lead, transfer_to_human become Ringg "actions")
    -> Ringg fires webhooks to our backend on key events:
         - call.started        -> we create a conv entry, optionally pre-load ERP context
         - tool.invoked         -> we run save_lead / transfer_to_human logic
         - call.ended           -> we trigger post-call extractor (or use Ringg's own summary)
         - transcript.available -> we persist final transcript + structured summary to SQL
  -> Plivo / Ringg
-> Customer phone speaker
```

The conversation loop lives **inside Ringg's platform**, not in our process.

---

## 3. What Stays vs What Goes vs What Changes

### 3.1 Components that STAY (no work)

| Component | Why it stays |
|---|---|
| **SQL Server schema** (`dbo.AICallSummary` in `call_analyzer`) | Ringg outputs structured summaries; we keep the schema and write to it via webhook |
| **ERP read path** (`backend/erp.py::fetch_enquiries`, `/api/erp/enquiries`) | Frontend Call Customers tab still needs this. No change. |
| **Frontend `Summary` tab** | Reads from `/api/agent/leads` which still serves `dbo.AICallSummary` rows. Zero changes unless the schema gains new fields. |
| **Frontend `Call Customers (ERP)` tab** | Outbound dial trigger; the URL it POSTs to may change (see 3.3 below) |
| **`KNOWLEDGE_BASE.md`** | The agent's knowledge is platform-agnostic. Ringg's agent designer will consume the same content. |
| **`.env` file structure** | Adds Ringg keys; existing keys can be removed once cut over |

### 3.2 Components that GO (full removal)

| File / Module | Why it goes |
|---|---|
| **`backend/agent/bot.py`** | Pipecat pipeline build (transport, STT, LLM, TTS, filler processor). Ringg owns this entire layer. |
| **`backend/agent/prompt.py`** | Goes from a runtime constant to **prompt content pasted into Ringg's agent-designer UI**. The text moves; the file is deleted from the codebase. |
| **`backend/agent/products.py`** | Same as `prompt.py` - moves into Ringg's knowledge-base UI as static reference. |
| **Pipecat dependencies in `requirements.txt`**: `pipecat-ai`, `sarvam-ai`, `openai` (if only used for OpenRouter routing), `silero-vad`, plus the smart-turn ONNX model directory | Not needed once Pipecat is gone |
| **Sarvam env vars** (`SARVAM_API_KEY`) | Sarvam is no longer called from our backend |
| **OpenRouter env vars** (`OPENROUTER_API_KEY`) | OpenRouter is no longer called from our backend (Ringg uses its own LLM credits) |
| **`/api/agent/plivo/ws` WebSocket endpoint** | Plivo no longer streams audio to us; Ringg handles audio |
| **`/api/plivo/answer`, `/api/plivo/outbound_lang`** | Replaced by Ringg's call-routing logic. May still need a stripped-down `/api/plivo/answer` if Plivo telephony is retained (see 3.4). |
| **`_intro_lang_menu_xml()`, IVR helpers** | The IVR (intro + language menu) becomes Ringg's responsibility |
| **`FillerAcknowledgmentProcessor` class** (currently disabled) | Pipecat-specific. Gone. |
| **`_extract_lead_from_transcript()` (post-call extractor)** | Ringg provides its own end-of-call summary - we adapt their output instead of running our own LLM pass. (If their summary quality is poor, we can keep this as a layered enhancement - see 3.5.) |
| **`SarvamSTTService`, `SarvamTTSService`, `OpenAILLMService` imports** | All gone |

### 3.3 Components that CHANGE (rewrite, not delete)

| File / Module | What changes |
|---|---|
| **`backend/main.py`** | Removes ~1500 lines of Pipecat orchestration. Adds new Ringg webhook handler endpoints. The file shrinks substantially. |
| **`/api/agent/call`** (Call Customers outbound trigger) | POSTs to Ringg's `start_call` API instead of Plivo REST. Body shape changes from `{phone, customer_name, enquiry_no}` to Ringg's expected payload. |
| **`_save_agent_lead()`** | Trigger changes from Pipecat callback to Ringg webhook. Same database write at the end. |
| **`backend/erp.py::insert_or_update_summary`** | No change to function signature. Called from a different upstream source. |
| **`requirements.txt`** | Strip Pipecat / Sarvam / Silero / OpenAI; add `httpx` (if not present), Ringg's SDK if they ship one |
| **Frontend `App.jsx` `CallCustomersView`** | The POST URL may change if `/api/agent/call` moves to a new path; otherwise unchanged |

### 3.4 Telephony: Keep Plivo or Switch?

Ringg AI may or may not require Plivo to stay in the picture. There are two patterns:

#### Pattern A: Plivo as carrier, Ringg as call handler

- Our Plivo number remains the customer-facing number
- Plivo Answer URL points to Ringg AI's webhook (instead of our backend)
- Ringg ingests the call via Plivo's standard SIP / Media Stream protocol
- We retain the Plivo number, billing, and Indian carrier compliance
- **Our backend keeps `/api/plivo/answer` only as a thin redirector** (or removes it entirely if Plivo points directly at Ringg)

#### Pattern B: Ringg handles everything

- Ringg provides its own phone number (Indian DID)
- Plivo is dropped entirely
- All carrier billing is consolidated with Ringg
- Faster latency potentially - one less hop
- Risk: changing the customer-facing number may require updating ERP records, marketing collateral, etc.

**Recommended:** Start with Pattern A. Verify the full integration end-to-end. Migrate to Pattern B only if Ringg's number is competitive on cost and Indian compliance is solved.

### 3.5 Post-call summary: Ringg's vs Ours

Ringg AI's end-of-call summary may be:
- **Good enough** - just adapt it into our `dbo.AICallSummary` columns
- **Generic / not domain-specific** - we keep `_extract_lead_from_transcript()` as a second-pass enrichment over Ringg's transcript

Decide by inspecting one of Ringg's actual end-of-call payloads during the trial. Plan A: trust theirs. Plan B (fallback): re-run our extractor over their transcript.

---

## 4. Step-by-Step Migration Plan

### Phase 0: Vendor due diligence (1-2 days)

Before any code change, verify with Ringg AI sales / docs:

1. **Marathi support quality** - their STT and TTS for `mr-IN`. Place at least 5 test calls in Marathi.
2. **Custom tool calls / function calling** - can their agent call `save_lead({...})` and `transfer_to_human()` mid-conversation, or do they only emit a final summary?
3. **Webhook event surface** - what events fire and when? At minimum we need: call.started, call.ended, tool.invoked (if 2 is yes), transcript.available, recording.available.
4. **Webhook auth / security** - HMAC signing, IP whitelist, mTLS?
5. **DTMF support** - can their agent receive press-digit input (for the language menu)?
6. **Multi-lingual within a single call** - can the agent switch language mid-conversation?
7. **Custom system prompt / agent designer** - how much of our 4 KB SYSTEM_PROMPT can we paste? Are there length limits?
8. **Knowledge base ingestion** - can we upload our PDFs (Kirloskar catalogs) or markdown KB so the agent can answer product questions?
9. **Plivo integration model** - which of Pattern A or B do they recommend? Setup steps?
10. **Pricing** - per-minute vs per-conversation; included LLM tokens or pass-through?
11. **Latency promises** - end-to-end response time SLA in India?
12. **Region / data residency** - where does the conversation data live? India / Singapore / US?
13. **SDK / API surface** - REST / gRPC / WebSocket / Python SDK?
14. **Concurrent call limits** - what's the cap on our plan?

These answers shape every subsequent step.

### Phase 1: Account setup + agent configuration in Ringg console (0.5 day)

1. Create Ringg AI account (varun.bhilare@kalabiz.com or shared team email)
2. Generate API key, sandbox secret, webhook signing key
3. In Ringg's agent designer:
   - Create a new agent named `Shivangi (Kala Genset)`
   - **Paste `prompt.py::SYSTEM_PROMPT` content** into the agent's "Instructions" / "System Prompt" field
   - **Upload `KNOWLEDGE_BASE.md`** as the agent's knowledge base, if their RAG supports it
   - Configure voice (closest match to Sarvam Bulbul `manisha` - female Indic voice)
   - Configure default language: Marathi (mr-IN); fallbacks: Hindi, English
   - Set turn-taking / VAD settings to match what we have today (Smart Turn equivalent if available)
4. Define **custom actions / tools** in Ringg's UI:
   - `save_lead(fields)` - HTTP POST to our new `/api/agent/ringg/save_lead` endpoint
   - `transfer_to_human()` - HTTP POST to `/api/agent/ringg/transfer`
5. Define **webhooks**:
   - `call.started` -> `POST /api/agent/ringg/call_started`
   - `call.ended` -> `POST /api/agent/ringg/call_ended`
   - `recording.available` -> `POST /api/agent/ringg/recording`

### Phase 2: Backend - add Ringg webhook handlers (1-2 days)

Create a new module `backend/ringg.py` (parallel to `erp.py`).

```python
# backend/ringg.py
# Skeleton — actual signatures depend on Ringg's docs.

import os
import hmac
import hashlib
from fastapi import APIRouter, Request, HTTPException

router = APIRouter()
WEBHOOK_SECRET = os.getenv("RINGG_WEBHOOK_SECRET", "")

def _verify_signature(body: bytes, signature: str) -> bool:
    expected = hmac.new(
        WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)

@router.post("/api/agent/ringg/call_started")
async def ringg_call_started(request: Request):
    body = await request.body()
    if not _verify_signature(body, request.headers.get("X-Ringg-Signature", "")):
        raise HTTPException(401, "Bad signature")
    payload = await request.json()
    # payload contains: call_id, from_number, to_number, direction, ringg_agent_id
    # Pre-load ERP context if outbound (customer_name, enquiry_no)
    ...

@router.post("/api/agent/ringg/save_lead")
async def ringg_save_lead(request: Request):
    body = await request.body()
    if not _verify_signature(body, request.headers.get("X-Ringg-Signature", "")):
        raise HTTPException(401, "Bad signature")
    payload = await request.json()
    # Call existing erp.insert_or_update_summary unchanged
    ...

@router.post("/api/agent/ringg/call_ended")
async def ringg_call_ended(request: Request):
    body = await request.body()
    if not _verify_signature(body, request.headers.get("X-Ringg-Signature", "")):
        raise HTTPException(401, "Bad signature")
    payload = await request.json()
    # payload contains: call_id, duration, transcript, ringg_summary, tool_calls
    # Translate Ringg's summary shape into our save_lead fields, then persist
    ...
```

Wire into `main.py`:

```python
from ringg import router as ringg_router
app.include_router(ringg_router)
```

### Phase 3: Backend - rewire `/api/agent/call` (outbound dial) (0.5 day)

Replace the Plivo REST call with Ringg's start-call API:

```python
@app.post("/api/agent/call")
async def agent_call(data: AgentCallInput):
    # OLD: Plivo REST + outbound_pending stash + answer_url with token
    # NEW: Ringg API call
    body = {
        "agent_id": os.getenv("RINGG_AGENT_ID"),
        "to": data.phone,
        "from": os.getenv("RINGG_FROM_NUMBER"),  # Plivo DID or Ringg-issued
        "metadata": {
            "customer_name": data.customer_name,
            "enquiry_no": data.enquiry_no,
        },
    }
    resp = httpx.post(
        "https://api.ringg.ai/v1/calls",
        json=body,
        headers={"Authorization": f"Bearer {os.getenv('RINGG_API_KEY')}"},
        timeout=15,
    )
    if resp.status_code >= 400:
        raise HTTPException(502, f"Ringg call failed: {resp.text[:300]}")
    return resp.json()
```

The customer's name and enquiry_no flow through Ringg's `metadata` and come back in webhook payloads so the agent greets correctly.

### Phase 4: Backend - delete Pipecat code (0.5 day)

In a feature branch, delete:
- `backend/agent/bot.py`
- `backend/agent/products.py`
- The entire `plivo_agent_ws` function in `main.py`
- `_intro_lang_menu_xml()`, `_outbound_intro_lang_xml()`, `_agent_stream_xml()`
- `/api/plivo/answer`, `/api/plivo/outbound_lang`, `/api/plivo/menu`
- `FillerAcknowledgmentProcessor` class
- `_extract_lead_from_transcript()` (or keep as a fallback for Ringg's summary - see 3.5)

Strip from `requirements.txt`:
```
pipecat-ai (and all its sub-deps: silero, smart-turn, etc.)
sarvam-ai
openai (if only used for OpenRouter routing)
```

### Phase 5: Plivo console reconfiguration (15 min)

If Pattern A:
- Plivo console -> Numbers -> +91 8035303414 -> Answer URL -> change from our ngrok URL to Ringg's webhook URL (per Ringg's docs)

If Pattern B:
- Release the Plivo number (or keep for backup), provision Ringg's number, update CRM / website / marketing collateral with the new number

### Phase 6: End-to-end testing (1 day)

1. Place inbound call -> verify intro + language menu -> verify conversation -> verify Summary row appears
2. Place outbound call via Call Customers tab -> verify Ringg dials -> verify agent greets by name -> verify Summary row
3. Hang up mid-conversation -> verify a partial Summary row is created
4. Try all three languages (Marathi, Hindi, English) -> verify TTS quality
5. Try difficult cases: customer asks for price, asks for AMC, asks for competitor comparison
6. Verify webhook signing rejects unsigned requests
7. Test concurrent calls - hit Ringg's reported concurrent-call cap and verify graceful failure
8. Verify the `_conversations` in-memory dict and `/api/agent/active-calls` endpoint still work (these now reflect Ringg's calls, not our Pipecat sessions)

### Phase 7: Production cutover (0.5 day)

- Deploy backend changes to production VPS (assuming edge deployment has been done by this point)
- Switch Plivo Answer URL in production
- Monitor for 24-48 hours
- Keep the old branch ready for rollback

**Total effort: 5-7 working days** assuming a clean Ringg AI account and no surprises in their API.

---

## 5. Effort & Cost Breakdown

### Engineering effort

| Phase | Time | Difficulty |
|---|---|---|
| Vendor due diligence (Phase 0) | 1-2 days | Low - mostly emails, demos, test calls |
| Account + agent designer setup (Phase 1) | 0.5 day | Low - UI-driven |
| Webhook handlers (Phase 2) | 1-2 days | Medium - depends on Ringg's API quality |
| `/api/agent/call` rewrite (Phase 3) | 0.5 day | Low |
| Pipecat code deletion (Phase 4) | 0.5 day | Low - mechanical |
| Plivo console change (Phase 5) | 15 min | Trivial |
| E2E testing (Phase 6) | 1 day | Medium - depends on issues found |
| Production cutover (Phase 7) | 0.5 day | Low |
| **Total** | **5-7 days** | |

### Cost shift

| Current line item | Approx. monthly cost (today, low volume) | After Ringg |
|---|---|---|
| Sarvam STT/TTS | ~Rs. 500-2000 (pay-per-use) | $0 |
| OpenRouter LLM (Claude Haiku) | ~Rs. 100-500 | $0 (Ringg includes LLM in their pricing) |
| Plivo (Pattern A retained) | ~Rs. 1500-5000 | Same (number rental + per-minute) |
| Plivo (Pattern B) | Same range | $0 |
| **Ringg AI subscription** | $0 | **Per-minute or monthly subscription** (need quote) |
| **VPS hosting** (if edge-deployed) | $10-15 | $10-15 (lower compute - no STT/LLM models loaded locally) |

Net cost: depends entirely on Ringg's pricing. For small volumes (<1000 calls/month), managed platforms are typically more expensive per call than self-hosted. For larger volumes (>10,000 calls/month) they become cheaper.

---

## 6. Risks and Mitigations

| Risk | Probability | Mitigation |
|---|---|---|
| **Marathi quality regresses** vs. our Sarvam-tuned setup | Medium | Phase 0 testing - 5+ real calls in Marathi before any code change |
| **Custom tool calls aren't supported** the way we need (mid-call `save_lead`) | Medium | Adapt to a "summary at end" model and re-run our extractor on Ringg's transcript |
| **Webhook reliability** - lost events mean lost summaries | Low | Idempotent SQL upserts (we already have this); add a periodic reconciliation job that polls Ringg for any missed calls |
| **Pricing surprise** at scale | Medium | Get a sales quote with explicit per-call cost at our expected volumes (e.g., 100, 500, 5000 calls/month) before committing |
| **Vendor lock-in** | High | Keep the SYSTEM_PROMPT, KNOWLEDGE_BASE.md, and SQL schema as source-of-truth. If Ringg falls over, we can rebuild a Pipecat agent in ~1 day from these artifacts |
| **Indian compliance / data residency** | Low-Medium | Verify with Ringg's legal team. Ask where conversation recordings are stored. |
| **Plivo + Ringg integration friction** | Medium | Choose Pattern B (Ringg's own number) if Pattern A integration is painful |
| **Latency doesn't actually improve** | Low | Run a head-to-head test in Phase 6 with stopwatched calls. If latency is the same, the migration isn't worth doing - cancel and stay on current stack. |

---

## 7. Rollback Plan

The current Pipecat stack stays on a `main` branch backup until the migration is proven stable.

**To roll back:**

1. Revert `backend/main.py` to the pre-migration commit
2. Restore `backend/agent/bot.py` and `backend/agent/products.py` from git history
3. Restore Pipecat dependencies in `requirements.txt`, `pip install -r requirements.txt`
4. Plivo console -> Numbers -> Answer URL -> change back to our ngrok / VPS URL
5. Restart backend
6. Place one test call to confirm

**Time to rollback: ~30 minutes** if you've kept the dependencies cached.

---

## 8. Open Questions to Send to Ringg AI

Send this list to their sales / support before signing anything:

1. Do you support Marathi (mr-IN) end-to-end? Both STT and TTS? Can you send a sample of your Marathi TTS voice?
2. Does your agent support **custom function calls / tools** mid-conversation, like Pipecat's `save_lead(fields)`? Or only end-of-call summaries?
3. Can the agent receive **DTMF digits** for an IVR-style language menu, or do you handle language detection automatically?
4. Can the agent **switch language mid-call** if the customer code-switches Marathi <-> Hindi <-> English?
5. What's the **complete webhook event list** and per-event payload shape? Send us your API docs.
6. Do webhooks include **HMAC signing** or some other authenticity guarantee?
7. What's your **concurrent call cap** at our expected volume (start with 5-10 concurrent, target 50+ concurrent)?
8. What's the **end-to-end perceived latency** on a phone call in India? (We're currently at 2.0-3.5 seconds per turn on Plivo + Pipecat self-hosted.)
9. Can we **upload our PDFs** (Kirloskar product catalogs) or markdown KB so the agent can answer product questions from them?
10. Can we **paste our existing 4 KB system prompt** verbatim into your agent designer, or is there a length limit / structure constraint?
11. **Pricing model:** per-minute? per-conversation? Per-token LLM passthrough or included? What's the quote at 100 / 500 / 5,000 / 50,000 calls/month?
12. **Data residency:** where are conversations, transcripts, and recordings stored? India? US? EU?
13. **Integration model with Plivo:** do you ingest Plivo's SIP / Media Streams directly, or do we hand off via webhook? Can you provide an Indian DID directly?
14. **Trial:** do you offer a sandbox / free trial we can use for the Phase 0 due diligence (real Marathi calls, 5-10 of them)?
15. **SLA / uptime:** what's the platform uptime promise? MTTR on incidents?
16. **Multi-tenant isolation:** is our conversation data segregated from other Ringg customers' data?
17. **Audio recording:** are calls recorded? Where stored? Are recordings retrievable via API?
18. **Migration support:** do you have engineering / professional-services help for the cutover, or are we on our own?
19. **API stability:** how often do webhook payload shapes change? Do you version your API?
20. **Lock-in:** if we leave, can we **export** all our agent configuration, transcripts, and summaries?

---

## 9. Files Touched in This Migration

### To be deleted

- `backend/agent/bot.py` (~440 lines)
- `backend/agent/products.py` (~40 lines)
- `backend/agent/__init__.py`
- The Pipecat-related portion of `backend/main.py` (~1500 lines):
  - `plivo_agent_ws()` WebSocket handler
  - `_intro_lang_menu_xml()`, `_outbound_intro_lang_xml()`, `_agent_stream_xml()`
  - `/api/plivo/answer`, `/api/plivo/outbound_lang`, `/api/plivo/menu`, `/api/plivo/dial_action`, `/api/plivo/turn`
  - `_extract_lead_from_transcript()` (potentially - see 3.5)

### To be added

- `backend/ringg.py` (new module, ~200 lines) - webhook handlers + Ringg API client
- A few new env vars: `RINGG_API_KEY`, `RINGG_AGENT_ID`, `RINGG_WEBHOOK_SECRET`, `RINGG_FROM_NUMBER`

### To be modified

- `backend/main.py` - shrink dramatically; add `ringg_router` include
- `backend/.env` - add Ringg keys, remove Sarvam / OpenRouter keys
- `backend/requirements.txt` - strip Pipecat / Sarvam / Silero / OpenAI; add `httpx` if absent
- `frontend/src/App.jsx::CallCustomersView` - may need URL change if `/api/agent/call` moves

### Unchanged

- `backend/erp.py` - all SQL functions untouched
- `frontend/src/App.jsx::SummaryView` - reads from `/api/agent/leads`, no change
- `frontend/src/App.jsx::CallCustomersView` - logic identical, only POST target URL may change
- `KNOWLEDGE_BASE.md` - the source of truth, content moves into Ringg console
- SQL Server schema - `dbo.AICallSummary` is platform-agnostic

---

## 10. Decision Gate

**Recommend doing this migration if** all four are true:

1. Ringg's Phase 0 trial calls in Marathi pass quality bar (vs. current Sarvam Bulbul)
2. Ringg supports custom function calls so `save_lead` / `transfer_to_human` work mid-conversation
3. Ringg's per-call cost at our expected volume is competitive with our current Sarvam + OpenRouter + Plivo blended cost
4. Ringg's webhook contract is stable and documented (not a moving target)

**Recommend NOT doing this migration if** any of these are true:

- Marathi quality is worse - we lose the months of pronunciation engineering we've done
- Custom tools aren't supported - we lose the structured save_lead and would have to re-do extraction
- Pricing is much higher than current at our volume - the operational simplicity isn't worth 2-3x cost
- The latency improvement we'd get from Ringg is smaller than the latency improvement we'd get from **edge deployment alone** (see ongoing edge-deploy track)

The simplest comparison: **before migrating, do edge deployment** of the current stack first. If that closes the latency gap satisfactorily, the Ringg migration is no longer urgent and we keep our control + customisation advantage.

---

*Last updated: 2026-05-20. Plan author: AI/ML Engineering, Kalabiz.*
