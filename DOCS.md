# Genset Call Analyzer — Project Documentation

End-to-end system for capturing genset sales enquiries by phone, transcribing and summarizing them, and routing leads into a structured ERP-ready format.

---

## 1. What this project does

Three independent (but related) workflows:

1. **Upload flow** — drop an audio file (recording from a manual phone, voice memo, etc.) and get a Marathi/Hindi/English transcript plus a JSON summary with the ERP fields filled in.
2. **Inbound calls (Plivo phone number)** — a customer dials your Plivo number, hears an IVR ("Press 1 for sales, Press 2 for AI assistant"), and either:
   - Gets connected to a real salesperson (with the call recorded and auto-summarized after), or
   - Talks to an AI agent that asks a structured 12-question script and produces the same summary.
3. **Outbound calls (Dial tab)** — your team enters a customer's number in the UI; the backend dials them via Plivo, plays the same IVR menu, records the conversation, and produces the same summary.

All three flows produce the same artifact: a structured JSON record of the customer's genset enquiry.

---

## 2. Architecture at a glance

```
┌─────────────────────┐
│   Browser (5178)    │   Vite + React UI (Federation remote)
│  - Upload tab       │
│  - Recorded Calls   │
│  - Dial tab         │
└──────────┬──────────┘
           │  /api/* (proxied)
           ▼
┌─────────────────────────────────────────────────────────┐
│            FastAPI backend (8006)                       │
│  ┌──────────────────────────────────────────────────┐  │
│  │  Upload pipeline  (Sarvam STT → Gemini summary)  │  │
│  │  Inbound webhooks (/api/plivo/answer, /menu, ..) │  │
│  │  Outbound dialer  (/api/plivo/dial)              │  │
│  │  TTS cache + stitched-recording serve            │  │
│  │  In-memory state: _plivo_calls, _conversations   │  │
│  └──────────────────────────────────────────────────┘  │
└──────┬───────────────┬─────────────────┬─────────────────┘
       │               │                 │
       ▼               ▼                 ▼
   Sarvam AI       OpenRouter         Plivo
   (Saarika STT,   (Gemini 2.0       (PSTN voice,
    Bulbul TTS)     Flash)            recordings)
                                          ▲
                                          │ webhooks via ngrok
                                          │
                          ┌────────────────────────────┐
                          │  ngrok tunnel              │
                          │  https://ocelot-boggle-... │
                          └────────────────────────────┘
                                          │
                                  customer's phone (PSTN)
```

---

## 3. Tech stack

| Layer | Component | Notes |
|---|---|---|
| Frontend | React 18 + Vite | Single page, three tabs |
| Backend | FastAPI (Python 3.13) | uvicorn server on `:8006` |
| STT | Sarvam Saarika v2.5 | Indian-language-first; handles Marathi/Hindi/English code-mix |
| TTS | Sarvam Bulbul v2, speaker `anushka` | Used for AI questions in 3 languages (cached on disk) |
| LLM | Gemini 2.0 Flash via OpenRouter | Translation (mr/hi → en) + structured summarization |
| Telephony | Plivo (India region) | Inbound DID + outbound REST API |
| Audio toolkit | ffmpeg via `imageio_ffmpeg` | Duration probing + concat for stitching |
| Public tunnel | ngrok (reserved domain) | Plivo → your localhost |

---

## 4. Repository layout

```
genset-call-analyzer/
├── backend/
│   ├── main.py            ← all backend logic (~1600 lines, single file)
│   ├── requirements.txt
│   ├── .env.example       ← template
│   └── .env               ← real keys (gitignored)
├── frontend/
│   ├── src/
│   │   ├── App.jsx        ← all React code (Upload + Calls + Dial views)
│   │   ├── index.css
│   │   └── main.jsx
│   ├── vite.config.js     ← /api proxy → 127.0.0.1:8006
│   ├── package.json
│   └── index.html
├── README.md              ← quickstart
├── DOCS.md                ← this file
└── .gitignore
```

---

## 5. Configuration (.env)

All config lives in `backend/.env`. Copy from `.env.example` and fill in:

| Key | Purpose |
|---|---|
| `SARVAM_API_KEY` | Sarvam STT + TTS |
| `OPENROUTER_API_KEY` | Gemini translation + summarization |
| `PLIVO_AUTH_ID` / `PLIVO_AUTH_TOKEN` | Plivo REST API + recording downloads |
| `SERVER_BASE_URL` | Public URL of THIS backend (your ngrok tunnel) |
| `PLIVO_CALL_LANGUAGE` | Default Sarvam STT language for forwarded recordings (e.g. `mr-IN`) |
| `AGENT_MAPPING` | Which DID forwards to which agent number(s). Format: `<did>:<num1>[\|num2]:<name1>[\|name2]` (multiple agents per DID ring in parallel) |
| `TELEPHONY_PROVIDER` | Currently `plivo` (informational only) |

Example agent mapping with two parallel agents:

```
AGENT_MAPPING=+918035303414:+918010486224|+918806415150:Varun|Arpita
```

---

## 6. The four flows in detail

### 6.1 Upload flow

```
[user picks .mp3/.wav]
        │
        ▼
POST /api/analyze   (multipart, fields: audio, language)
        │
        ▼
ffmpeg duration check
        │
        ├─ if > 30s → split into 25s chunks
        │
        ▼
Sarvam STT (Saarika v2.5) per chunk → join transcript
        │
        ▼
Gemini translate (if mr/hi → en)
        │
        ▼
Gemini extract structured ERP JSON (system prompt in main.py:48)
        │
        ▼
Returns { transcript, transcript_english, summary }
```

UI tabs:
- **ERP Summary** — pretty-printed structured fields (customer/genset/deal/follow-up sections)
- **Transcript** — both original + English translation
- **Raw JSON** — the structured object, copyable

### 6.2 Inbound call → forward to salesperson (press 1)

```
Customer dials +918035303414 (Plivo DID)
        │
        ▼
Plivo POSTs /api/plivo/answer (alias: /plivo/incoming)
        │  saves _conversations[call_uuid]
        │
        ▼ XML: GetDigits → Speak menu
"Press 1 for sales team. Press 2 for AI assistant."
        │
        │  (caller presses 1)
        ▼
Plivo POSTs /api/plivo/menu?call_uuid=X with Digits=1
        │  picks Plivo DID as callerId
        │  picks all configured agents from AGENT_MAPPING
        │  spawns _start_session_recording (REST API call to record session)
        │
        ▼ XML: <Dial> with multiple <Number> (parallel ring)
        │
   Varun's phone rings    Arpita's phone rings
        │                       │
        └──── first to answer wins ────┐
                                       ▼
                         Conversation happens (recorded by Plivo session)
                                       │
                              call ends (hangup)
                                       │
                                       ▼
                    /api/plivo/dial_action fires
                    sets status = awaiting_recording
                    spawns _poll_and_attach_recording
                                       │
                                       ▼
                    polls Plivo's Recording API (with retries)
                    until the MP3 URL appears
                                       │
                                       ▼
                    _process_plivo_recording downloads MP3
                    Sarvam STT → Gemini translate → Gemini summarize
                                       │
                                       ▼
                       Entry shows up in Recorded Calls tab
```

**Why we poll Plivo's Recording API instead of relying on Plivo's recording callback:** Plivo's automatic post-call callback is flaky for our account. Polling by `call_uuid` always works.

**Why session recording via REST API (not `<Dial record="...">`):** Plivo's `<Dial>` element does NOT support inline recording attributes (we tried — silently ignored). The REST API `POST /v1/Account/{auth_id}/Call/{call_uuid}/Record/` is the correct way.

### 6.3 Inbound call → AI conversational flow (press 2)

```
Customer dials, presses 2 from main menu
        │
        ▼
/api/plivo/menu  →  GetDigits asking for language
"For Hindi press 1, Marathi press 2, English press 3"
        │
        │  (if no input → Redirect with default_lang=2 → Marathi)
        ▼
/api/plivo/turn?call_uuid=X with Digits=2
        │  conv["status"] = "questioning"
        │  conv["language"] = "mr"
        │
        ▼ XML: <Play> Q1 (TTS) + <Record>
caller hears Sarvam TTS asking the question
        │
        ▼  (records 60s max, 2s silence cutoff, # to skip)
        │
Plivo POSTs RecordUrl back to /api/plivo/turn
        │  saves to conv["history"]
        │  schedules _transcribe_turn (background)
        │  advances conv["question_index"]
        │
        └─── loop through 12 questions ───┘
                       │
                       ▼ (after Q12)
              <Play> closing pitch (TTS)
              "Thank you. Kala Genset is OEM for Kirloskar..."
                       │
                       ▼
              <Hangup/>
                       │
                       ▼
        _finalize_conversation runs:
          - waits for all transcriptions
          - builds Q&A transcript
          - Gemini translate to English
          - Gemini extract structured summary
          - _stitch_conversation_audio: ffmpeg-concat
            [TTS Q1, user A1, TTS Q2, user A2, ..., closing]
            → single MP3 in _TTS_DIR/stitched_<uuid>_<ts>.mp3
          - _save_plivo_call writes the entry into _plivo_calls
                       │
                       ▼
        Recorded Calls tab shows the call with playable
        whole-conversation audio + Q/A transcript + summary
```

The 12 questions (asked in caller's chosen language):
1. Name + company/firm
2. Business type (factory/hotel/office/other)
3. Location
4. kVA load + has it been calculated
5. Purpose (backup/prime/industrial)
6. Single-phase or three-phase
7. AMF panel needed
8. Diesel / gas / petrol
9. Purchase timeline
10. Quotes from other suppliers + brands considered
11. Approximate budget
12. Any other questions

Closing pitch covers: Kala Genset Pvt Ltd, OEM for Kirloskar Oil Engines, plants in Chakan/Bangalore, HO in Pimpri-Chinchwad, supplies across MH/MP/Goa/Karnataka, 9000+ gensets/year.

### 6.4 Outbound call (Dial tab)

```
User opens Dial tab → types "+919545556151" → clicks Dial
        │
        ▼
POST /api/plivo/dial  body: {number}
        │
        │  Backend normalizes to E.164 (+91...)
        │  Picks first DID from AGENT_MAPPING as `from`
        │
        ▼
POST https://api.plivo.com/v1/Account/{auth_id}/Call/
     { from: +918035303414, to: +91..., answer_url: ngrok/api/plivo/answer }
        │
        ▼
Plivo dials customer's number from our DID
        │
   ▼ (when customer picks up)
        │
Plivo hits /api/plivo/answer (just like an inbound call)
        │
        ▼
Same IVR menu plays — customer presses 1 (sales) or 2 (AI)
The flow from here is identical to inbound (sections 6.2 / 6.3)
```

**Important:** on outbound calls, Plivo's webhook fields swap meaning:
- Inbound: `From` = caller, `To` = our DID
- Outbound: `From` = our DID, `To` = customer

The `plivo_menu` handler detects which is our DID by matching against `AGENT_MAPPING` keys, and uses that as the outbound `callerId` (Plivo's India rules require callerId to be a Plivo-owned number).

---

## 7. Backend API endpoints

### General

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Status of all integrations (Sarvam, OpenRouter, Plivo, ngrok URL) |
| POST | `/api/analyze` | Upload audio → transcript + summary (multipart: `audio`, `language`) |
| POST | `/api/summarize` | Pasted-transcript → summary only (JSON body) |

### Plivo webhooks (called by Plivo, not by frontend)

| Method | Path | Triggered when |
|---|---|---|
| POST | `/api/plivo/answer` | Inbound call answered, OR outbound call answered (same handler) |
| POST | `/plivo/incoming` | Alias for `/api/plivo/answer` (some setup guides use this URL) |
| POST | `/api/plivo/menu?call_uuid=X` | Caller pressed a digit on the main IVR menu |
| POST | `/api/plivo/turn?call_uuid=X` | AI flow: caller pressed a language digit OR finished recording an answer |
| POST | `/api/plivo/dial_action?call_id=X` | Forward flow: the `<Dial>` leg ended (answered/no-answer/busy/etc.) |
| POST | `/api/plivo/recording?call_id=X` | XML `<Record>` finished, Plivo posts the recording URL |
| POST | `/api/plivo/session_recording?call_uuid=X` | REST-API session recording finished (often missed by Plivo, hence we also poll) |
| POST | `/api/plivo/hangup` | Optional hangup notification |

### Frontend-facing

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/plivo/calls` | Processed calls (Q/A summaries + forwarded-call recordings) |
| GET | `/api/plivo/recordings` | Raw recordings list pulled from Plivo's REST API |
| GET | `/api/plivo/audio/{filename}` | Serves cached TTS WAVs and stitched MP3s. Honors HTTP Range so the player can scrub. |
| POST | `/api/plivo/import` | Process an arbitrary Plivo recording URL (manual recovery) |
| POST | `/api/plivo/calls/{call_id}/retry` | Re-poll Plivo for the recording when an entry is stuck at `awaiting_recording` |
| POST | `/api/plivo/dial` | Initiate an outbound call to a phone number |

---

## 8. Audio processing details

### 8.1 TTS cache

For the AI conversational flow, every question (and the closing pitch) needs to be spoken in 3 languages. We cache the generated audio on disk:

- Cache dir: `<TEMP>/genset_tts/`
- Filename: `<lang>_<md5(lang+text)[:16]>.wav`
- First call in a given language: ~1-3s per Sarvam TTS request
- All subsequent calls: instant (file exists, atomic replace via `os.replace`)

### 8.2 Per-turn user recordings

Plivo's `<Record>` element creates one MP3 per turn. These are short (3-30s each). We use them ONLY for transcription — they don't show up directly in the UI list (filtered out as turns of a completed conversation).

### 8.3 Stitched whole-call audio (AI flow)

After the call ends, `_stitch_conversation_audio` builds a single MP3 representing the full conversation:

```
ffmpeg -i tts_q1.wav -i user_a1.mp3 -i tts_q2.wav -i user_a2.mp3 ... \
       -filter_complex "[0:a][1:a][2:a]...concat=n=N:v=0:a=1[out]" \
       -map "[out]" -b:a 96k stitched_<uuid>_<ts>.mp3
```

This is what plays in the Recorded Calls audio player. Telephony quality (8kHz callee leg + 22kHz TTS resampled) — clear enough for review.

### 8.4 Forward-call recording (sales handoff)

For the "press 1" flow we don't stitch — we use the raw Plivo session recording (full call, both sides) directly. Polled by call_uuid via `_poll_and_attach_recording`.

---

## 9. State management

Everything lives in process memory. Two stores:

| Variable | Purpose | Lifetime |
|---|---|---|
| `_conversations: dict[call_uuid → dict]` | In-flight call state (current question, language, accumulated turn recordings) | From answer until call ends |
| `_plivo_calls: list[dict]` | Processed call records (transcripts, summaries, recording URLs) | Until process restart |

Both protected by their own `threading.Lock`.

**Limitation: a backend restart wipes everything.** Recordings still exist on Plivo's side, so for forwarded-flow calls a manual "Retry" button rebuilds the entry. For AI flow calls that never got finalized, the data is lost — Plivo only has the per-turn fragments.

For production-grade reliability, persist these to SQLite/Postgres. Not done yet.

---

## 10. Frontend tabs

### 10.1 Upload Recording
The original flow. Drop a file, pick a language, click Analyze. Status pipeline shows progress. Three result tabs (Summary / Transcript / Raw JSON).

### 10.2 Recorded Calls
Lists everything from `/api/plivo/calls` and `/api/plivo/recordings`, deduped by `call_uuid`. Each entry shows status badge, from/to numbers, agent name(s), call summary preview. Click to see full transcript + summary + audio player. Auto-polls every 5s.

For raw Plivo recordings that haven't been processed yet, a "▸ Transcribe & Summarize" button kicks off processing. For entries stuck at `awaiting_recording`, a "↻ Retry fetch recording" button re-polls Plivo.

### 10.3 Dial
Phone number input + Dial button. Hits `/api/plivo/dial`. Shows "Call queued" with the request UUID. The actual call result lands in Recorded Calls a moment later.

---

## 11. Running locally

Three terminals:

```bash
# Terminal 1 — backend
cd backend
python main.py
# → http://localhost:8006

# Terminal 2 — frontend
cd frontend
npm run dev
# → http://localhost:5178

# Terminal 3 — ngrok (only needed for Plivo calls)
ngrok http --domain=ocelot-boggle-retool.ngrok-free.dev 8006
```

Plivo's Application's Answer URL must be set to:
```
https://ocelot-boggle-retool.ngrok-free.dev/plivo/incoming
```
(or `/api/plivo/answer` — both routes resolve to the same handler).

---

## 12. Plivo specifics that bit us

A laundry list of gotchas we discovered the hard way:

| Issue | Lesson |
|---|---|
| `record="record-from-answer"` on `<Dial>` is silently ignored | Use Plivo REST API to start recording, NOT the Dial XML attributes |
| `OUTGOING_CALL_BARRED` / "Violates Media Anchoring" | Indian outbound voice has TRAI restrictions; trial accounts blocked from dialing arbitrary mobiles |
| `callerId` must be a Plivo-owned number | Don't use the caller's own number on the outbound leg, or the call dies mid-transfer |
| Plivo's post-recording callback is unreliable | Poll the Recording API by call_uuid instead |
| `&` in XML element bodies must be `&amp;` | Otherwise Plivo's parser drops the whole response and the call cuts off silently |
| Free ngrok shows interstitial warning page | Frontend audio URLs go through Vite proxy, not the ngrok URL directly |
| `recording_type` field is always `"call"` | No way to distinguish session vs per-turn recordings server-side; we group by call_uuid in the UI |

---

## 13. Known limitations & future work

| Issue | Severity | Suggested fix |
|---|---|---|
| In-memory state lost on restart | High | Persist to SQLite |
| `_plivo_calls` and `_conversations` grow unboundedly | Medium | Bound to last N (e.g. 200) entries |
| Async webhook handlers do blocking I/O on event loop | Medium | Convert to `def` so FastAPI runs them in threadpool |
| TTS file writes not atomic | Low | Write to `.tmp` then `os.replace` |
| 2-second silence timeout may cut callers off mid-thought | Low | Increase to 3s if complaints |
| Plivo trial restricts arbitrary outbound | Blocker for prod | Upgrade Plivo + KYC + DLT registration, or switch to Exotel/Ozonetel |
| No customer database | Blocker for outbound campaigns | Add SQLite table + CSV import |
| No DLT compliance | Legal blocker for outbound | Register as Telemarketer with TRAI |
| `datetime.utcnow()` deprecation warnings | Low | Switch to `datetime.now(timezone.utc)` |

---

## 14. Cost ballpark (per 100 calls of ~5 min each)

| Service | Estimated |
|---|---|
| Plivo voice (India region, paid tier) | ~₹150-300 |
| Sarvam STT (Saarika) | ~₹250-500 |
| Sarvam TTS (Bulbul) | minimal — cached |
| OpenRouter (Gemini 2.0 Flash) | ~₹50-100 |
| **Total** | **~₹450-900 per 100 calls** |

Trial Plivo is currently free for the inbound flow (within trial limits). Sarvam and OpenRouter both have generous free tiers that should cover initial development.





