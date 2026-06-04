"""
Genset Call Analyzer — Backend
FastAPI server that handles:
  1. Audio upload
  2. Transcription via Sarvam AI (Saarika)
  3. Structured summarization via OpenRouter (Gemini)
"""

import os
import sys
import json
import mimetypes
import subprocess
import tempfile
import threading
import time
import uuid
import base64
import hashlib
from datetime import datetime
import requests
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Form, BackgroundTasks, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

try:
    from plivo import plivoxml
    PLIVO_AVAILABLE = True
except ImportError:
    PLIVO_AVAILABLE = False

load_dotenv(Path(__file__).parent / ".env")

# ── Platform standardization env vars (backend-standardizations §7.1) ──
# Defaults make `python main.py` work standalone without any env set; CI/WinSW
# override these in production. APP_ENV gates production-only behavior below
# (see exception handler in §5.2 — dev keeps FastAPI defaults so the frontend's
# data.detail error parsing keeps working unchanged).
APP_ENV      = os.getenv("APP_ENV", "dev").lower()
SERVICE_NAME = os.getenv("SERVICE_NAME", "call-center")
GIT_SHA      = os.getenv("GIT_SHA", "dev")
LOG_LEVEL    = os.getenv("LOG_LEVEL", "INFO").upper()
PORT         = int(os.getenv("PORT", "8006"))
HOST         = os.getenv("HOST", "0.0.0.0")

# Structured JSON-Lines logging to stdout (backend-standardizations §6). WinSW
# captures stdout natively and rolls daily files; the app must not manage log
# files itself. Existing print() calls are intentionally left alone — migration
# is opportunistic. New code should `from loguru import logger`.
from loguru import logger
logger.remove()
logger.add(
    sys.stdout,
    level=LOG_LEVEL,
    serialize=True,      # one JSON object per line
    backtrace=False,
    diagnose=False,
)

# Locate ffmpeg binary
try:
    import imageio_ffmpeg
    FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
    FFMPEG_BIN = "ffmpeg"

app = FastAPI(title="Genset Call Analyzer", version="1.0.0")

# Shared aiohttp session — for future HTTP-backed pipecat services.
_aiohttp_session = None

async def _get_aiohttp_session():
    """Lazy-init a single shared aiohttp ClientSession for the app lifetime."""
    global _aiohttp_session
    if _aiohttp_session is None or _aiohttp_session.closed:
        import aiohttp as _aiohttp
        _aiohttp_session = _aiohttp.ClientSession()
    return _aiohttp_session


@app.on_event("startup")
async def _prewarm_agent_imports():
    """Eagerly import the heavy Pipecat/Silero stack at startup.

    Without this, the FIRST call after a server restart pays 1-3s for
    `pipecat.audio.vad.silero` to import (which pulls in torch). That delay
    sits between the caller pressing a digit on the IVR and hearing Shivangi
    speak, so eat it once at boot instead of on the first real conversation.
    """
    try:
        import pipecat.audio.vad.silero  # noqa: F401 — loads torch
        import pipecat.transports.websocket.fastapi  # noqa: F401
        import pipecat.serializers.plivo  # noqa: F401
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        # Instantiating once forces the Silero ONNX/torch model weights to
        # load into memory; subsequent per-call instantiations reuse the
        # already-loaded weights.
        SileroVADAnalyzer()
        from agent import bot as _bot  # noqa: F401 — loads sarvam services
        print("[startup] agent stack pre-warmed (pipecat + silero + sarvam)")
    except Exception as e:
        print(f"[startup] pre-warm failed (will lazy-load on first call): {e!r}")


# ── Honorific (sir/madam) inference ───────────────────────────────────────
# Drives both the hardcoded greeting AND a flag passed through customer_context
# so the LLM uses the matching word ("madam" / "मॅडम" / "मैडम") throughout the
# call instead of always defaulting to "sir". Heuristic is biased toward
# "False = use sir" — Indian phone etiquette finds "sir" for a woman bad,
# but "madam" for a man worse, so we'd rather under-detect than mis-detect.

_FEMALE_FIRST_NAMES = frozenset([
    # Common Indian female first names, Latin script (ERP stores names
    # transliterated). Kept lowercase for case-insensitive lookup.
    "priya", "pooja", "puja", "neha", "anita", "sunita", "kavita", "sangita",
    "sangeeta", "geeta", "gita", "sita", "radha", "lakshmi", "saraswati",
    "durga", "parvati", "shruti", "smriti", "aditi", "anjali", "asha", "usha",
    "aarti", "arti", "vidya", "meena", "reena", "rina", "heena", "hina",
    "tina", "mona", "swati", "trupti", "snehal", "sneha", "mrunal", "maitreyi",
    "madhuri", "jyoti", "jaya", "yamini", "hema", "lalita", "lata", "leena",
    "sayli", "sayali", "sandhya", "savita", "suvarna", "sushila", "sushma",
    "suman", "sumitra", "suchitra", "bhavna", "bhavana", "bhakti", "bharti",
    "bharati", "kalpana", "rupali", "ruchi", "megha", "manasi", "manjusha",
    "vandana", "veena", "beena", "rashmi", "reshma", "mansi", "manju",
    "shobha", "babita", "sapna", "pranita", "pranjali", "tripti", "mrunalini",
    "mridula", "madhavi", "jyotsna", "rekha", "renuka", "renu", "rita",
    "rohini", "rukmini", "sadhana", "sanjana", "seema", "sejal", "shaila",
    "shalini", "sharda", "sharmila", "shilpa", "shraddha", "shrushti",
    "shweta", "smita", "sonali", "sonia", "subhadra", "sujata", "sunaina",
    "sunanda", "surabhi", "surekha", "susmita", "tanvi", "tara", "tejal",
    "tejaswini", "uma", "ujjwala", "urmila", "varsha", "vasanti", "vibha",
    "vijaya", "vimala", "yashoda", "ankita", "anushka", "ananya", "diya",
    "ishita", "khushi", "kiara", "navya", "nidhi", "nikita", "pari",
    "preeti", "priti", "radhika", "riddhi", "siddhi", "tanya", "vaishali",
    "anupama", "deepa", "deepika", "divya", "namrata", "neelima", "nirmala",
    "pallavi", "poonam", "pratima", "rachana", "richa", "rupa", "saroja",
    "shailaja", "sheela", "shreya", "supriya", "vasudha", "anu", "binita",
    "bina", "vinita", "vineeta", "neelam", "nilam", "nita", "neeta",
    "shivangi", "arpita", "ashwini", "kshitija", "akshita", "ipsita",
    "yamuna", "ganga", "smruti", "mrudula",
])

# Names that look female by suffix/vowel-ending but are actually male in
# Indian usage. Keeps the suffix heuristic from misfiring on these.
_MALE_OVERRIDES = frozenset([
    "ravi", "shiv", "shivam", "satya", "surya", "krishna", "siddh",
    "rohit", "manish", "ashish", "harsh", "naveen", "amit", "sumit",
    "mohit", "lalit", "vivek", "nilesh", "rajesh", "ramesh", "mahesh",
    "ganesh", "dinesh", "umesh", "rakesh", "lokesh", "naresh", "mukesh",
    "hitesh", "ritesh", "yogesh", "kalpesh", "alpesh", "jignesh",
    "abhay", "viraj", "yuvraj", "ranveer", "uday", "udit", "pulkit",
    "ajay", "vijay", "sanjay", "jay", "akshay", "arjun", "varun",
    "tarun", "karan", "rohan", "kunal", "vishal", "kapil", "anil",
    "sunil", "nikhil", "rahul", "rishi", "yash", "neeraj", "shashank",
])

# Suffixes that strongly imply a female Indian name when the name isn't
# already on the male-override list.
_FEMALE_SUFFIXES = ("ika", "ini", "ita", "iya", "shri", "shree", "nika",
                    "nita", "kshi", "nya", "lekha")


def _is_female_name(name: Optional[str]) -> bool:
    """Best-effort gender check from an Indian first name. Returns True if
    likely female, False on uncertainty (so the default address stays "sir")."""
    if not name:
        return False
    parts = name.strip().split()
    if not parts:
        return False
    first = parts[0].lower().strip(".,")
    # Skip an honorific if it accidentally appears as the first token.
    if first in {"mr", "mrs", "ms", "dr", "shri", "smt", "kum", "miss"}:
        if len(parts) < 2:
            return False
        first = parts[1].lower().strip(".,")
    if first in _MALE_OVERRIDES:
        return False
    if first in _FEMALE_FIRST_NAMES:
        return True
    return any(first.endswith(suf) for suf in _FEMALE_SUFFIXES)


def _honorific(language: str, is_female: bool) -> str:
    """Honorific word ('sir'/'madam' or Devanagari equivalent) for the
    caller's chosen language."""
    lang = (language or "mr").lower()
    if is_female:
        return {"mr": "मॅडम", "hi": "मैडम", "en": "madam"}.get(lang, "मॅडम")
    return {"mr": "सर", "hi": "सर", "en": "sir"}.get(lang, "सर")


def _agent_greeting_text(preferred_language: Optional[str], customer_name: Optional[str], is_outbound: bool) -> str:
    """Return the hardcoded first-turn greeting Shivangi speaks the moment the
    WebSocket pipeline is ready, so the caller hears something within ~300ms
    of pressing the language digit instead of waiting ~1-1.5s for Claude.

    The text is pre-added to the LLM context as an assistant message
    (in the WS handler) so the model knows the greeting already happened
    and doesn't repeat it on the user's first reply.

    Picks sir/madam via `_is_female_name(customer_name)`. Inbound calls
    (no name yet) default to "sir"; the system prompt instructs the LLM
    to switch to madam once it has captured a name that reads as female.
    """
    lang = (preferred_language or "mr").lower()
    name = (customer_name or "").strip()
    name_prefix = f" {name}" if name else ""
    hon = _honorific(lang, _is_female_name(name))

    # English greeting INTENTIONALLY OMITS the "Kala Genset" company name —
    # Sarvam Bulbul's English voice (anushka) mispronounces "Kala" as "yala"
    # (no Devanagari anchor available to fix the schwa-A vowel the way the
    # Marathi/Hindi greetings use `कला जेनसेट`). The IVR's earlier
    # Polly.Aditi `<Speak>` already announced the company name correctly,
    # so the caller knows who's calling — Shivangi just introduces herself.
    # We introduce Shivangi as an A-I assistant up front (spelled "ए. आय." /
    # "A-I" so the TTS reads the letters, not a garbled word). This is a
    # short, natural disclosure that the call is automated — required for
    # AI-driven outbound calls and sets the customer's expectations.
    if is_outbound and name:
        if lang == "en":
            return f"Hi {name} {hon}, this is Shivangi, an A-I assistant, calling about your generator enquiry. Is now a good time to talk?"
        if lang == "hi":
            return f"नमस्ते {name} {hon}, मैं शिवांगी, कला जेनसेट की ए. आय. असिस्टंट. आपकी genset enquiry के बारे में बात करनी थी. अभी समय है?"
        return f"नमस्कार {name} {hon}, मी शिवांगी, कला जेनसेटची ए. आय. असिस्टंट. तुमच्या genset enquiry बद्दल थोडी माहिती हवी होती. आत्ता बोलायला वेळ आहे का?"

    if lang == "en":
        return f"Hi{name_prefix} {hon}, this is Shivangi, an A-I assistant, calling about your generator enquiry. May I have your name please?"
    if lang == "hi":
        return f"नमस्ते{name_prefix} {hon}, मैं शिवांगी, कला जेनसेट की ए. आय. असिस्टंट. आपका नाम बताएँगे?"
    return f"नमस्कार{name_prefix} {hon}, मी शिवांगी, कला जेनसेटची ए. आय. असिस्टंट. तुमचं नाव सांगाल का?"


# ── AI agent leads — SQL-backed persistence ───────────────────────────────
# `save_lead` from the Pipecat agent → write to dbo.AICallSummary via erp.py.
# Read path: GET /api/agent/leads also queries SQL (see below).
# Survives backend restarts now (used to be an in-memory list).

def _save_agent_lead(lead_id: str, from_number: str, to_number: str, fields: dict) -> None:
    """Record a captured lead by inserting/updating dbo.AICallSummary.

    Idempotent: if save_lead fires twice on the same call_uuid (the LLM
    occasionally does this), the second call UPDATEs the first row instead
    of inserting a duplicate — see erp.insert_or_update_summary.
    """
    # Pull outbound flag + enq_no from the conversation entry (set by
    # /api/plivo/answer when an outbound call's webhook lands).
    with _conversations_lock:
        conv = _conversations.get(lead_id) or {}
    is_outbound = bool(conv.get("outbound"))
    enq_no = conv.get("enquiry_no")

    try:
        from erp import insert_or_update_summary
        insert_or_update_summary(
            call_uuid=lead_id,
            enq_no=enq_no,
            from_number=from_number,
            to_number=to_number,
            is_outbound=is_outbound,
            fields=fields or {},
        )
    except Exception as e:
        # Don't bring down the call if SQL is momentarily unreachable — log
        # loudly and move on. The agent has already said the closing line.
        print(f"[save-lead] SQL persistence failed: {type(e).__name__}: {e}")


async def _extract_lead_from_transcript(
    context,
    http_session,
    *,
    caller_phone: str = "",
    customer_name: Optional[str] = None,
    preferred_language: Optional[str] = None,
) -> Optional[dict]:
    """Post-call extractor.

    When the in-call `save_lead` tool never fired (customer hung up early, agent
    didn't reach closing step, network blip), pull the conversation transcript
    out of the LLM context and ask the LLM one more time to extract structured
    fields. This guarantees every call produces a Summary row, not just the
    ones that completed the happy-path flow.

    Returns the extracted fields dict (suitable for `_save_agent_lead`), or
    None if the call was too short / extraction failed.
    """
    messages = getattr(context, "messages", None) or []
    transcript_lines = []
    user_turn_count = 0
    for m in messages:
        try:
            role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
        except Exception:
            continue
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            transcript_lines.append(f"{role.upper()}: {content.strip()}")
            if role == "user":
                user_turn_count += 1

    # If the customer never said anything (only the bot's greeting played),
    # we still want a Summary row so the sales team knows the call happened
    # and can decide whether to retry. Return a minimal payload built from
    # whatever metadata we have — no LLM call needed.
    if user_turn_count == 0:
        if not (customer_name or caller_phone):
            return None  # truly nothing to save
        # Convert IVR digit code (mr/hi/en) to the long label our SQL column uses.
        _lang_map = {"mr": "marathi", "hi": "hindi", "en": "english"}
        minimal = {
            "call_summary": (
                "Call ended before the customer engaged — they answered but "
                "hung up before responding. Recommend manual follow-up."
            ),
            "language_used": _lang_map.get((preferred_language or "").lower(), "marathi"),
        }
        if customer_name:
            minimal["customer_name"] = customer_name
        if caller_phone:
            minimal["callback_number"] = caller_phone
        return minimal

    transcript = "\n".join(transcript_lines)

    openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
    if not openrouter_key:
        print("[post-call extract] OPENROUTER_API_KEY missing — skipping extraction")
        return None

    hints = []
    if customer_name:
        hints.append(f"Customer name already known: {customer_name}")
    if caller_phone:
        hints.append(f"Customer's phone number on the call: {caller_phone}")
    hints_block = ("\n".join(hints) + "\n") if hints else ""

    extraction_prompt = (
        "You are a data extractor for Kala Genset enquiry calls. Read the call "
        "transcript below and extract enquiry fields as a JSON object. Return "
        "ONLY the JSON object — no markdown, no commentary, no trailing text.\n\n"
        "Rules:\n"
        "- Use ENGLISH values throughout (transliterate Marathi/Hindi names: "
        "  'गणेश' → 'Ganesh', 'इन्फोसिस' → 'Infosys').\n"
        "- OMIT any field you can't determine from the transcript. Never use "
        "  empty strings or nulls — just leave the key out.\n"
        "- `call_summary` is REQUIRED: 2-3 short English sentences a sales rep "
        "  can scan in 5 seconds (who/what/when/budget if known).\n"
        "- Inference rules: capacity_kva > 58.5 → fuel_type='diesel'. "
        "  canopy_required is always 'silent_canopy' at Kala Genset.\n\n"
        "Fields (omit unknowns):\n"
        "- customer_name (English)\n"
        "- designation: e.g. 'owner', 'purchase manager', 'engineer'\n"
        "- company_name (English)\n"
        "- business_type: factory / hotel / hospital / IT park / retail / residential / agriculture / construction / other\n"
        "- location: city + area\n"
        "- purpose: backup / prime / continuous / industrial\n"
        "- new_or_replacement: 'new' or 'replacement'\n"
        "- capacity_kva: numeric string e.g. '60'\n"
        "- load_calculated: self / consultant / estimated / not_done\n"
        "- phase: single_phase / three_phase\n"
        "- fuel_type: diesel / gas / petrol\n"
        "- amf_required: boolean\n"
        "- canopy_required: silent_canopy / open_frame / indoor\n"
        "- timeline: e.g. 'within 1 month', '3-6 months'\n"
        "- site_visit_ok: boolean\n"
        "- competitor_quotes_received: boolean (true ONLY if customer named at least one brand)\n"
        "- competitor_brands: array of brand name strings\n"
        "- budget_range: ONLY if the customer volunteered a figure (we don't ask). Otherwise omit.\n"
        "- callback_number: phone number\n"
        "- email: email address\n"
        "- language_used: marathi / hindi / english / mix\n"
        "- call_summary: REQUIRED English 2-3 sentences\n\n"
        f"{hints_block}"
        "Transcript:\n"
        f"{transcript}\n\n"
        "JSON object:"
    )

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {openrouter_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": "anthropic/claude-haiku-4.5",
        "messages": [{"role": "user", "content": extraction_prompt}],
        "temperature": 0.1,
        "max_tokens": 1500,
        # NOTE: previously included `response_format: {"type": "json_object"}` —
        # removed because OpenRouter often drops or mishandles this OpenAI-
        # specific param when routing to Anthropic models, returning an
        # empty body. We now ask for JSON in the prompt and parse defensively.
    }
    extracted: Optional[dict] = None
    try:
        async with http_session.post(url, json=body, headers=headers, timeout=30) as resp:
            data = await resp.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        # Anthropic via OpenRouter sometimes wraps JSON in ```json ... ```
        # markdown fences. Strip them before parsing.
        cleaned = (content or "").strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```", 2)[1] if "```" in cleaned[3:] else cleaned[3:]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].lstrip()
            cleaned = cleaned.rsplit("```", 1)[0].strip()
        # Extract just the JSON object — anything before/after a top-level
        # {…} is conversational filler the LLM occasionally adds.
        if "{" in cleaned and "}" in cleaned:
            cleaned = cleaned[cleaned.index("{") : cleaned.rindex("}") + 1]
        if cleaned:
            extracted = json.loads(cleaned)
    except Exception as e:
        print(f"[post-call extract] LLM call / JSON parse failed: {type(e).__name__}: {e}")

    # Strip empty values defensively if we got something
    if extracted:
        extracted = {k: v for k, v in extracted.items() if v not in (None, "", [], {})}
        # If the LLM gave us anything useful, prefer it
        if extracted:
            return extracted

    # Always-save fallback: LLM call failed OR returned nothing usable, but
    # the customer did speak (user_turn_count > 0). Save what we know from
    # call metadata + a snippet of the transcript so the call never silently
    # disappears from the Summary tab.
    _lang_map = {"mr": "marathi", "hi": "hindi", "en": "english"}
    snippet = transcript
    if len(snippet) > 800:
        snippet = snippet[:800] + " …(transcript truncated)"
    fallback = {
        "call_summary": (
            f"Transcript captured but structured extraction failed — see raw "
            f"snippet below. Recommend manual review by sales team.\n\n{snippet}"
        ),
        "language_used": _lang_map.get((preferred_language or "").lower(), "marathi"),
    }
    if customer_name:
        fallback["customer_name"] = customer_name
    if caller_phone:
        fallback["callback_number"] = caller_phone
    print(f"[post-call extract] saved FALLBACK row (LLM extraction failed)")
    return fallback


# CORS — backend-standardizations §9. The previous `allow_origins=["*"]` paired
# with `allow_credentials=True` is silently broken — browsers reject credentialed
# responses with a wildcard origin. Enumerate explicitly. localhost:5178 is this
# app's standalone Vite dev port (port table §8); the rest are the canonical
# platform origins. /api requests in prod come same-origin via the IIS reverse
# proxy, so CORS only really fires on dev + cross-origin previews.
_CORS_ORIGINS = [
    "http://localhost:5178",       # this app, Vite dev (standalone)
    "http://localhost:5173",       # KALA shell, Vite dev
    "http://localhost:5176",       # adjacent remotes in dev (Corelytics et al.)
    "https://ai.kalapms.com",
    "https://staging.kala.com",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Standard error response shape (backend-standardizations §5.2) — PRODUCTION-ONLY.
# The frontend currently parses errors as `data.detail` (frontend/src/App.jsx
# lines 234, 360, 478, 496, 783). Switching the envelope to {error:{...}} in dev
# would break every error toast. In production the shell catches remote errors
# centrally with the standard envelope, so the shape change is safe — and the
# spec mandates it. Gate the handler so dev keeps FastAPI defaults.
if APP_ENV == "production":
    import traceback as _traceback
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as _StarletteHTTPException
    from fastapi.responses import JSONResponse as _JSONResponse

    _ERR_TYPE_BY_STATUS = {
        400: "validation_error", 401: "auth_required", 403: "forbidden",
        404: "not_found",        409: "conflict",      429: "rate_limited",
        502: "ai_error",         503: "db_timeout",
    }

    @app.exception_handler(_StarletteHTTPException)
    async def _http_exc(_request, exc):
        return _JSONResponse({
            "error": {
                "type": _ERR_TYPE_BY_STATUS.get(exc.status_code, "internal_error"),
                "message": str(exc.detail) if exc.detail else exc.__class__.__name__,
            }
        }, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(_request, exc):
        return _JSONResponse({
            "error": {
                "type": "validation_error",
                "message": "Request body failed validation",
                "detail": str(exc.errors()),
            }
        }, status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled_exc(_request, exc):
        return _JSONResponse({
            "error": {
                "type": "internal_error",
                "message": f"{type(exc).__name__}: {exc}",
                "detail": _traceback.format_exc(),
            }
        }, status_code=500)

# ── API keys from .env ──
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
PLIVO_AUTH_ID = os.getenv("PLIVO_AUTH_ID", "")
PLIVO_AUTH_TOKEN = os.getenv("PLIVO_AUTH_TOKEN", "")
SERVER_BASE_URL = os.getenv("SERVER_BASE_URL", "http://localhost:8006").rstrip("/")
PLIVO_CALL_LANGUAGE = os.getenv("PLIVO_CALL_LANGUAGE", "mr-IN")

# ── Agent mapping ──
# Format: "<did>:<num1>[|num2|...]:<name1>[|name2|...]"
# Multiple DIDs separated by commas. Multiple agents per DID separated by `|`.
# Example: "+918035303414:+918010486224|+918806415150:Varun|Arpita"
# When multiple numbers are configured for a DID, they are dialed in parallel —
# first to pick up gets the call (standard Plivo behavior with multiple <Number>).
def _parse_agent_mapping(raw: str) -> dict[str, dict]:
    mapping = {}
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) < 2:
            continue
        did = parts[0].strip()
        numbers = [n.strip() for n in parts[1].split("|") if n.strip()]
        if not numbers:
            continue
        names_raw = parts[2].strip() if len(parts) > 2 else ""
        names = [n.strip() for n in names_raw.split("|") if n.strip()]
        # Pad names to match numbers
        while len(names) < len(numbers):
            names.append(f"Agent {len(names) + 1}")
        mapping[did] = {
            "number": numbers[0],       # primary (for compat)
            "name": names[0],           # primary name (for compat)
            "numbers": numbers,         # full list
            "names": names,             # full list
        }
    return mapping

AGENT_MAPPING = _parse_agent_mapping(os.getenv("AGENT_MAPPING", ""))

# ── In-memory store of Plivo-processed calls ──
# Each entry: {id, call_uuid, from_number, to_number, started_at, status,
#              recording_url, duration, transcript, transcript_english, summary, error}
_plivo_calls: list[dict] = []
_plivo_calls_lock = threading.Lock()


def _plivo_configured() -> bool:
    return bool(
        PLIVO_AUTH_ID and PLIVO_AUTH_ID != "your_plivo_auth_id_here"
        and PLIVO_AUTH_TOKEN and PLIVO_AUTH_TOKEN != "your_plivo_auth_token_here"
    )

# ── Claude prompt for genset call summarization ──
SYSTEM_PROMPT = """You are a sales call analysis assistant for a genset (generator set) company.
You will receive a transcript of a phone call between a sales agent and a customer.
The transcript may be in Marathi, Hindi, English, or a mix of all three.
The transcript may contain ASR (speech recognition) errors — use context to infer the correct meaning.

Extract the following information and return ONLY a valid JSON object with these fields:

{
    "customer_name": "Name of the customer if mentioned, else null",
    "company_name": "Company or business name if mentioned, else null",
    "contact_number": "Phone number if mentioned, else null",
    "location": "City/area/site location discussed, else null",
    "enquiry_type": "new_purchase | rental | service | spare_parts | other",
    "genset_details": {
        "capacity_kva": "Requested kVA rating if mentioned, else null",
        "fuel_type": "diesel | gas | petrol | null",
        "brand_preference": "Any brand mentioned, else null",
        "phase": "single_phase | three_phase | null",
        "usage": "commercial | residential | industrial | construction | agricultural | other | null"
    },
    "budget_range": "Budget or price discussion if any, else null",
    "timeline": "When they need it, else null",
    "competitor_mentions": ["List of competitor brands or dealers mentioned"],
    "existing_genset_info": "Details about any existing genset they have, else null",
    "key_requirements": ["List of specific requirements — silent type, AMF panel, etc."],
    "next_steps": ["Agreed follow-up actions — site visit, quotation, callback, demo, etc."],
    "call_summary": "A concise 3-4 line summary of the call in English",
    "call_sentiment": "positive | neutral | negative",
    "hot_lead": true/false
}

Important:
- Return ONLY valid JSON, no markdown fences, no explanation
- If information is not discussed, use null
- The call_summary should ALWAYS be in English regardless of call language
- Interpret Marathi/Hindi genset terminology correctly (जनरेटर, डीजी सेट, वीज, केव्हीए)"""


# ── Health + readiness (backend-standardizations §3) ──
@app.get("/api/health")
def health():
    """Liveness probe (§3.1). Returns 200 as long as the process is alive;
    does NOT check downstream deps. WinSW pings this to decide whether to
    restart the service. The extra `*_configured` flags are this app's own
    additions for the frontend status bar — purely additive, ignored by the
    platform probe."""
    return {
        # Canonical fields (§3.1)
        "status":  "ok",
        "service": SERVICE_NAME,
        "version": GIT_SHA,
        # App-specific extras consumed by frontend/src/App.jsx status bar
        "sarvam_configured":     bool(SARVAM_API_KEY and SARVAM_API_KEY != "your_sarvam_api_key_here"),
        "openrouter_configured": bool(OPENROUTER_API_KEY and OPENROUTER_API_KEY != "your_openrouter_api_key_here"),
        "plivo_configured":      _plivo_configured(),
        "plivo_sdk_installed":   PLIVO_AVAILABLE,
        "public_base_url":       SERVER_BASE_URL,
    }


@app.get("/api/ready")
def ready():
    """Readiness probe (§3.2). 200 if downstream deps are reachable, 503 if
    not. This app's only hard dependency is SQL Server — ERP enquiries and
    AICallSummary writes both require it. In dev without DB creds set, 503
    is the spec-correct response."""
    from fastapi.responses import JSONResponse
    checks: dict[str, dict] = {}

    try:
        import erp as _erp  # local import — keeps health check independent of erp.py
        _erp._get_erp_conn().cursor().execute("SELECT 1").fetchone()
        checks["sql_server"] = {"status": "ok"}
    except Exception as e:
        checks["sql_server"] = {"status": "fail", "error": f"{type(e).__name__}: {e}"}

    overall_ok = all(c["status"] == "ok" for c in checks.values())
    return JSONResponse(
        {"status": "ready" if overall_ok else "not_ready", "checks": checks},
        status_code=(200 if overall_ok else 503),
    )


# ── Step 1: Transcribe audio via Sarvam AI ──
CHUNK_MS = 25_000  # 25 seconds per chunk (Sarvam limit is 30s)

def _transcribe_chunk(chunk_path: str, filename: str, content_type: str, language: str) -> str:
    """Transcribe a single audio chunk via Sarvam AI."""
    url = "https://api.sarvam.ai/speech-to-text"

    with open(chunk_path, "rb") as audio_file:
        files = {"file": (filename, audio_file, content_type)}
        data = {
            "language_code": language,
            "model": "saarika:v2.5",
            "with_timestamps": "true",
        }
        headers = {"api-subscription-key": SARVAM_API_KEY}
        resp = requests.post(url, files=files, data=data, headers=headers, timeout=120)

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Sarvam API error ({resp.status_code}): {resp.text}"
        )

    result = resp.json()
    return result.get("transcript", result.get("text", ""))


def _get_duration(file_path: str) -> float:
    """Get audio duration in seconds using ffmpeg."""
    cmd = [FFMPEG_BIN, "-i", file_path, "-f", "null", "-"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    # ffmpeg prints duration to stderr
    for line in result.stderr.splitlines():
        if "Duration:" in line:
            # Format: Duration: HH:MM:SS.mm
            time_str = line.split("Duration:")[1].split(",")[0].strip()
            parts = time_str.split(":")
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    return 0.0


def _split_audio(file_path: str, chunk_seconds: int = 25) -> list[str]:
    """Split audio into chunks using ffmpeg, returns list of chunk file paths."""
    duration = _get_duration(file_path)
    if duration <= 30:
        return [file_path]

    chunk_paths = []
    start = 0
    idx = 0
    while start < duration:
        chunk_path = file_path + f".chunk{idx}.wav"
        cmd = [
            FFMPEG_BIN, "-y", "-i", file_path,
            "-ss", str(start), "-t", str(chunk_seconds),
            "-ar", "16000", "-ac", "1",  # 16kHz mono for speech
            chunk_path,
        ]
        subprocess.run(cmd, capture_output=True, timeout=60)
        chunk_paths.append(chunk_path)
        start += chunk_seconds
        idx += 1

    return chunk_paths


def transcribe_audio(file_path: str, filename: str, content_type: str, language: str = "mr-IN") -> str:
    """Send audio to Sarvam AI Saarika — splits into 25s chunks if needed."""

    if not SARVAM_API_KEY or SARVAM_API_KEY == "your_sarvam_api_key_here":
        raise HTTPException(status_code=400, detail="Sarvam API key not configured. Add it to backend/.env")

    chunk_paths = _split_audio(file_path)

    # Single file, no splitting needed
    if chunk_paths == [file_path]:
        return _transcribe_chunk(file_path, filename, content_type, language)

    # Transcribe each chunk
    transcripts = []
    for chunk_path in chunk_paths:
        try:
            text = _transcribe_chunk(chunk_path, "chunk.wav", "audio/wav", language)
            if text and text.strip():
                transcripts.append(text.strip())
        finally:
            if chunk_path != file_path:
                os.unlink(chunk_path)

    return " ".join(transcripts)


# ── Step 1b: Translate transcript to English via LLM ──
def translate_to_english(text: str, source_lang: str = "mr-IN") -> str:
    """Translate transcript to English using OpenRouter/Gemini."""
    if source_lang.startswith("en"):
        return text

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "google/gemini-2.0-flash-001",
        "max_tokens": 4000,
        "messages": [
            {
                "role": "system",
                "content": "You are a translator. Translate the following transcript to English. Keep it natural and conversational. Return ONLY the translated text, nothing else.",
            },
            {
                "role": "user",
                "content": text,
            }
        ],
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=60)
    if resp.status_code == 200:
        result = resp.json()
        return result["choices"][0]["message"]["content"].strip()

    return text  # Fallback to original if translation fails


# ── Step 2: Summarize transcript via OpenRouter (Gemini) ──
def summarize_transcript(transcript: str) -> dict:
    """Send transcript to OpenRouter for structured ERP summary extraction."""

    if not OPENROUTER_API_KEY or OPENROUTER_API_KEY == "your_openrouter_api_key_here":
        raise HTTPException(status_code=400, detail="OpenRouter API key not configured. Add it to backend/.env")

    url = "https://openrouter.ai/api/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "google/gemini-2.0-flash-001",
        "max_tokens": 2000,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": f"Here is the call transcript:\n\n{transcript}\n\nExtract the structured information as JSON.",
            }
        ],
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=60)

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"OpenRouter API error ({resp.status_code}): {resp.text}"
        )

    result = resp.json()
    text = result["choices"][0]["message"]["content"].strip()

    # Clean markdown fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail=f"Failed to parse LLM response as JSON: {text[:500]}")


# ── Main endpoint: Upload audio → Transcript + Summary ──
@app.post("/api/analyze")
async def analyze_call(
    audio: UploadFile = File(...),
    language: str = "mr-IN",
):
    """
    Full pipeline:
    1. Receive audio file upload
    2. Transcribe via Sarvam AI
    3. Summarize via Claude
    4. Return transcript + structured summary
    """

    # Validate file type
    allowed_types = [
        "audio/aac", "audio/mpeg", "audio/wav", "audio/mp4",
        "audio/ogg", "audio/webm", "audio/x-wav", "audio/x-m4a",
        "audio/mp3", "audio/flac", "video/mp4",
        "application/octet-stream",  # fallback for unknown types
    ]

    # Determine MIME type from upload or filename
    upload_filename = audio.filename or "upload.mp3"
    upload_content_type = audio.content_type
    if not upload_content_type or upload_content_type == "application/octet-stream":
        MIME_MAP = {
            ".aac": "audio/aac", ".mp3": "audio/mpeg", ".wav": "audio/wav",
            ".ogg": "audio/ogg", ".flac": "audio/flac", ".m4a": "audio/x-m4a",
            ".mp4": "audio/mp4", ".webm": "audio/webm", ".opus": "audio/opus",
            ".amr": "audio/amr", ".wma": "audio/x-ms-wma", ".aiff": "audio/aiff",
        }
        ext = Path(upload_filename).suffix.lower()
        upload_content_type = MIME_MAP.get(ext, "application/octet-stream")

    # Save uploaded file to temp location
    suffix = Path(upload_filename).suffix or ".mp3"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await audio.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # Step 1: Transcribe
        transcript = transcribe_audio(tmp_path, upload_filename, upload_content_type, language)

        if not transcript or not transcript.strip():
            raise HTTPException(status_code=422, detail="Transcription returned empty. Audio may be too short or unclear.")

        # Step 1b: Translate to English
        transcript_english = translate_to_english(transcript, language)

        # Step 2: Summarize (use English transcript for better results)
        summary = summarize_transcript(transcript_english)

        return {
            "status": "success",
            "transcript": transcript,
            "transcript_english": transcript_english,
            "summary": summary,
            "filename": audio.filename,
            "language": language,
        }

    finally:
        # Cleanup temp file
        os.unlink(tmp_path)


# ── Endpoint: Summarize a pasted transcript directly ──
class TranscriptInput(BaseModel):
    transcript: str
    agent_name: Optional[str] = None
    caller_number: Optional[str] = None

@app.post("/api/summarize")
async def summarize_only(data: TranscriptInput):
    """Skip transcription — just summarize an existing transcript."""

    extra_context = ""
    if data.agent_name:
        extra_context += f"\nAgent: {data.agent_name}"
    if data.caller_number:
        extra_context += f"\nCaller Number: {data.caller_number}"

    full_transcript = extra_context + "\n\n" + data.transcript if extra_context else data.transcript
    summary = summarize_transcript(full_transcript)

    return {
        "status": "success",
        "transcript": data.transcript,
        "summary": summary,
    }


# =========================================================
# PLIVO WEBHOOK INTEGRATION
# =========================================================
# Flow:
#   1. Incoming call hits Plivo number
#   2. Plivo calls POST {SERVER_BASE_URL}/api/plivo/answer
#      → we respond with XML telling Plivo to record the call
#   3. When recording is done, Plivo POSTs to /api/plivo/recording
#      → we download the recording, transcribe + summarize in background
#   4. Frontend polls GET /api/plivo/calls to show results
# =========================================================

def _save_plivo_call(entry: dict) -> None:
    with _plivo_calls_lock:
        _plivo_calls.append(entry)


def _update_plivo_call(call_id: str, **fields) -> None:
    with _plivo_calls_lock:
        for item in _plivo_calls:
            if item["id"] == call_id:
                item.update(fields)
                return


def _process_plivo_recording(call_id: str, recording_url: str) -> None:
    """Background task: download Plivo recording → transcribe → summarize."""
    tmp_path = None
    try:
        _update_plivo_call(call_id, status="downloading")

        # Plivo recording URLs are public but may require basic auth
        auth = (PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN) if _plivo_configured() else None
        resp = requests.get(recording_url, auth=auth, timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to download recording: HTTP {resp.status_code}")

        # Plivo recordings are MP3
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
            tmp.write(resp.content)
            tmp_path = tmp.name

        _update_plivo_call(call_id, status="transcribing")
        transcript = transcribe_audio(tmp_path, "plivo_recording.mp3", "audio/mpeg", PLIVO_CALL_LANGUAGE)
        if not transcript or not transcript.strip():
            raise RuntimeError("Transcription returned empty")

        _update_plivo_call(call_id, status="translating", transcript=transcript)
        transcript_english = translate_to_english(transcript, PLIVO_CALL_LANGUAGE)

        _update_plivo_call(call_id, status="summarizing", transcript_english=transcript_english)
        summary = summarize_transcript(transcript_english)

        _update_plivo_call(call_id, status="done", summary=summary, finished_at=datetime.utcnow().isoformat())

    except Exception as e:
        _update_plivo_call(call_id, status="error", error=str(e), finished_at=datetime.utcnow().isoformat())
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# =========================================================
# CONVERSATIONAL AI FLOW
# =========================================================
LANG_CONFIG = {
    "hi": {"code": "hi-IN", "speaker": "anushka", "label": "Hindi"},
    "mr": {"code": "mr-IN", "speaker": "anushka", "label": "Marathi"},
    "en": {"code": "en-IN", "speaker": "anushka", "label": "English"},
}

CONVERSATION_SCRIPT = [
    # Name + company
    # Hi/Mr use code-mixed Devanagari + Latin (English loanwords kept in Latin script)
    # because Sarvam Bulbul TTS pronounces this register cleanly. Avoid transliterated
    # English (बॅकअप, पॅनेल) — Bulbul mispronounces those.
    {"id": "name_company",
     "en": "Thank you. Please tell me your name and your company or firm name.",
     "hi": "धन्यवाद। आपका नाम और company का नाम बताइए, please.",
     "mr": "धन्यवाद. तुमचं नाव आणि company चं नाव सांगाल का?"},
    # Business type (script point 5)
    {"id": "business_type",
     "en": "What type of business is it — factory, hotel, office, or other?",
     "hi": "आपका business किस तरह का है — factory, hotel, office, या कुछ और?",
     "mr": "तुमचा business कुठल्या प्रकारचा आहे — factory, hotel, office, की दुसरं काही?"},
    # Location (point 2)
    {"id": "location",
     "en": "Which city or location is the genset for?",
     "hi": "Genset किस city या location के लिए चाहिए?",
     "mr": "Genset कुठल्या city साठी आहे — म्हणजे location कोणतं?"},
    # Load checked + kVA (combines points 1 and 4)
    {"id": "capacity_load",
     "en": "What is your exact load requirement in kVA, and have you already calculated or checked the load?",
     "hi": "आपका load कितने kVA का है? और load पहले से calculate किया है क्या?",
     "mr": "तुमचा load किती kVA आहे? आणि load आधीच calculate केला आहे का?"},
    # Purpose (point 6)
    {"id": "purpose",
     "en": "What is the purpose of the DG set — backup power, prime power, or industrial use?",
     "hi": "Genset किसके लिए चाहिए — backup के लिए, prime power के लिए, या industrial use के लिए?",
     "mr": "Genset कशासाठी हवा आहे — backup साठी, prime power साठी, की industrial use साठी?"},
    # Single vs three phase (point 8)
    {"id": "phase",
     "en": "Do you need single-phase or three-phase power?",
     "hi": "आपको single phase चाहिए या three phase?",
     "mr": "तुम्हाला single phase हवा की three phase?"},
    # AMF panel (point 7)
    {"id": "amf_panel",
     "en": "Do you require an AMF panel for automatic start and stop?",
     "hi": "क्या आपको AMF panel चाहिए — auto start और auto stop के लिए?",
     "mr": "तुम्हाला AMF panel हवा आहे का — auto start आणि auto stop साठी?"},
    # Fuel
    {"id": "fuel_type",
     "en": "Do you prefer diesel, gas, or petrol?",
     "hi": "Fuel कौन सा चाहिए — diesel, gas, या petrol?",
     "mr": "Fuel कोणतं हवं — diesel, gas, की petrol?"},
    # Timeline (point 9)
    {"id": "timeline",
     "en": "What is your expected purchase timeline?",
     "hi": "आप कब तक purchase करने का सोच रहे हैं?",
     "mr": "तुम्ही साधारण कधीपर्यंत purchase करायचा विचार करताय?"},
    # Competitor quotes + brands considered (points 10 and 11 combined)
    {"id": "competitor_brands",
     "en": "Have you received quotations from other suppliers? If yes, which brands are you considering?",
     "hi": "किसी और से quotation लिया है क्या? अगर हाँ, तो कौन से brands देख रहे हैं?",
     "mr": "इतर कोणाकडून quotation घेतलंय का? असेल तर कुठल्या brands चा विचार करताय?"},
    # Budget
    {"id": "budget",
     "en": "What is your approximate budget range?",
     "hi": "आपका budget लगभग कितना है?",
     "mr": "तुमचं budget साधारण किती आहे?"},
    # Final open-ended (placed AFTER the closing pitch — see CLOSING)
    {"id": "other_questions",
     "en": "Lastly, do you have any other questions or doubts?",
     "hi": "आखिर में — आपके कोई और सवाल या doubts हैं क्या?",
     "mr": "शेवटी एक विचारतो — तुम्हाला आणखी काही प्रश्न किंवा doubts आहेत का?"},
]

# Closing now includes Kala Genset's company pitch before the goodbye.
# Hi/Mr use code-mixed Devanagari + Latin (English brand/product terms in Latin script)
# because Sarvam Bulbul TTS pronounces this register cleanly. Persona is female (Shivangi),
# so verb conjugations use feminine forms (बताती हूं / सांगते, not बताता हूं / सांगतो).
CLOSING = {
    "en": ("Thank you for sharing your requirement. Let me give you a quick introduction to our company. "
           "Kala Genset Private Limited is an Original Equipment Manufacturer for Kirloskar Oil Engines gensets. "
           "Our manufacturing plants are in Chakan Pune and Bangalore, and our head office is in Pimpri Chinchwad near Auto Cluster. "
           "We supply gensets across Maharashtra, Madhya Pradesh, Goa, and Karnataka, and manufacture more than 9000 gensets every year. "
           "Our team will contact you shortly with a quotation. Please feel free to call us for any query. Thank you and goodbye."),
    "hi": ("अपनी requirement बताने के लिए धन्यवाद। अब मैं आपको हमारी company के बारे में थोड़ा बताती हूं। "
           "Kala Genset Pvt Ltd, Kirloskar Oil Engines का OEM है — यानी हम उनके gensets बनाते हैं। "
           "हमारे manufacturing plants Chakan-Pune और Bangalore में हैं, और head office Pimpri-Chinchwad में Auto Cluster के पास है। "
           "हम Maharashtra, MP, Goa और Karnataka में gensets supply करते हैं, और हर साल 9000 से ज़्यादा gensets बनाते हैं। "
           "हमारी team जल्द ही quotation लेकर आपको call करेगी। कोई भी सवाल हो तो बेझिझक हमें call कीजिए। धन्यवाद, अच्छा दिन रहे!"),
    "mr": ("तुमची requirement सांगितल्याबद्दल धन्यवाद. आता मी थोडक्यात आमच्या company बद्दल सांगते. "
           "Kala Genset Pvt Ltd हे Kirloskar Oil Engines चे OEM आहे — म्हणजे आम्ही त्यांचे gensets बनवतो. "
           "आमचे manufacturing plants Chakan-Pune आणि Bangalore इथे आहेत, आणि head office Pimpri-Chinchwad ला Auto Cluster जवळ आहे. "
           "आम्ही Maharashtra, MP, Goa आणि Karnataka मध्ये gensets supply करतो, आणि दरवर्षी 9000 पेक्षा जास्त gensets बनवतो. "
           "आमची team लवकरच quotation घेऊन तुम्हाला call करेल. काही प्रश्न असतील तर बिनधास्त call करा. धन्यवाद, चांगला दिवस!"),
}

# In-memory conversations, keyed by CallUUID
_conversations: dict[str, dict] = {}
_conversations_lock = threading.Lock()

# TTS cache dir — generated audio served to Plivo via /api/plivo/audio/{filename}
_TTS_DIR = Path(tempfile.gettempdir()) / "genset_tts"
_TTS_DIR.mkdir(exist_ok=True)


def _tts_filename(text: str, lang: str) -> str:
    h = hashlib.md5(f"{lang}:{text}".encode("utf-8")).hexdigest()[:16]
    return f"{lang}_{h}.wav"


def _generate_tts(text: str, lang: str) -> str:
    """Call Sarvam TTS (cached by text+lang). Returns local WAV path."""
    fname = _tts_filename(text, lang)
    path = _TTS_DIR / fname
    if path.exists() and path.stat().st_size > 0:
        return str(path)

    if not SARVAM_API_KEY or SARVAM_API_KEY == "your_sarvam_api_key_here":
        raise RuntimeError("SARVAM_API_KEY not configured")

    cfg = LANG_CONFIG.get(lang, LANG_CONFIG["en"])
    url = "https://api.sarvam.ai/text-to-speech"
    payload = {
        "inputs": [text],
        "target_language_code": cfg["code"],
        "speaker": cfg["speaker"],
        "model": "bulbul:v2",
        "pitch": 0,
        "pace": 1.0,
        "loudness": 1.0,
        "speech_sample_rate": 22050,
        "enable_preprocessing": True,
    }
    headers = {"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"}
    resp = requests.post(url, json=payload, headers=headers, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"Sarvam TTS error {resp.status_code}: {resp.text[:300]}")

    audios = resp.json().get("audios", [])
    if not audios:
        raise RuntimeError("Sarvam TTS returned no audio")
    path.write_bytes(base64.b64decode(audios[0]))
    return str(path)


def _tts_url(text: str, lang: str) -> str:
    path = _generate_tts(text, lang)
    return f"{SERVER_BASE_URL}/api/plivo/audio/{Path(path).name}"


@app.get("/api/plivo/audio/{filename}")
def serve_plivo_audio(filename: str, request: Request):
    """
    Serves generated audio — per-question TTS (WAV) and post-call stitched recordings (MP3).
    Honors HTTP Range requests so the browser audio player can scrub.
    """
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(404, "Invalid filename")
    path = _TTS_DIR / filename
    if not path.exists():
        raise HTTPException(404, "Audio not found")

    ext = path.suffix.lower()
    media_type = "audio/mpeg" if ext == ".mp3" else "audio/wav"
    file_size = path.stat().st_size

    range_header = request.headers.get("range") or request.headers.get("Range")
    if range_header and range_header.startswith("bytes="):
        try:
            start_s, end_s = range_header.replace("bytes=", "").split("-", 1)
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else file_size - 1
        except ValueError:
            start, end = 0, file_size - 1
        end = min(end, file_size - 1)
        if start > end or start >= file_size:
            raise HTTPException(status_code=416, detail="Requested range not satisfiable")

        length = end - start + 1

        def _iter_range(chunk_size: int = 64 * 1024):
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    data = f.read(min(chunk_size, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Cache-Control": "no-cache",
        }
        from fastapi.responses import StreamingResponse
        return StreamingResponse(_iter_range(), status_code=206, media_type=media_type, headers=headers)

    return FileResponse(
        str(path),
        media_type=media_type,
        headers={"Accept-Ranges": "bytes", "Content-Length": str(file_size)},
    )


def _fetch_recording_by_call_uuid(call_uuid: str) -> Optional[dict]:
    """Query Plivo's Recording API for any recording matching this call_uuid. Returns the
    first match (or None). Used to pull the recording URL without relying on Plivo's callback."""
    if not _plivo_configured() or not call_uuid:
        return None
    url = f"https://api.plivo.com/v1/Account/{PLIVO_AUTH_ID}/Recording/"
    try:
        resp = requests.get(
            url,
            auth=(PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN),
            params={"call_uuid": call_uuid, "limit": 5},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        objs = resp.json().get("objects", [])
        if not objs:
            return None
        # Prefer longest — most likely the session recording, not a sub-fragment
        objs.sort(key=lambda r: r.get("recording_duration_ms", 0), reverse=True)
        return objs[0]
    except requests.RequestException:
        return None


def _poll_and_attach_recording(call_uuid: str, call_id: str):
    """Background task: Plivo's post-call recording callback is unreliable, so we poll
    Plivo's Recording API after the call ends and attach whatever we find. Retries with
    backoff to give Plivo time to publish the recording."""
    delays = [5, 10, 15, 20, 30, 30, 60]  # ~170s total, should be plenty
    for delay in delays:
        time.sleep(delay)
        rec = _fetch_recording_by_call_uuid(call_uuid)
        if not rec:
            continue
        recording_url = rec.get("recording_url")
        duration_ms = rec.get("recording_duration_ms")
        if not recording_url:
            continue

        duration_sec = int(duration_ms / 1000) if duration_ms else None
        # Attach URL to the existing call entry and trigger the processing pipeline
        with _plivo_calls_lock:
            for item in _plivo_calls:
                if item["id"] == call_id and not item.get("recording_url"):
                    item["recording_url"] = recording_url
                    item["duration"] = duration_sec
                    item["status"] = "queued"
                    break
        _process_plivo_recording(call_id, recording_url)
        return


def _start_session_recording(call_uuid: str):
    """
    Kick off a full-call session recording via Plivo REST API.
    Called in a background thread shortly after the call is answered.
    Plivo will POST the final recording URL to /api/plivo/session_recording when the call ends.
    """
    if not _plivo_configured() or not call_uuid:
        return

    url = f"https://api.plivo.com/v1/Account/{PLIVO_AUTH_ID}/Call/{call_uuid}/Record/"
    callback = f"{SERVER_BASE_URL}/api/plivo/session_recording?call_uuid={call_uuid}"
    payload = {
        "callback_url": callback,
        "callback_method": "POST",
        "file_format": "mp3",
        "time_limit": 3600,
    }
    # The call may not be fully answered on the first attempt — retry briefly.
    for attempt in range(6):
        time.sleep(1.5)
        try:
            resp = requests.post(
                url, json=payload,
                auth=(PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN),
                timeout=10,
            )
            if resp.status_code < 400:
                return
        except requests.RequestException:
            pass


@app.post("/api/plivo/session_recording")
async def plivo_session_recording(
    request: Request,
    background_tasks: BackgroundTasks,
    call_uuid: str = "",
):
    """
    Plivo posts here with the full-session recording URL after the call ends.
    If the Q/A-based call entry already exists for this call_uuid, attach the recording URL to it.
    Otherwise create a fresh entry and queue full processing.
    """
    form = await request.form()
    recording_url = form.get("RecordUrl") or form.get("RecordingUrl") or ""
    duration = form.get("RecordingDuration") or form.get("Duration")
    if not recording_url:
        return {"status": "no_url"}

    # Try to attach to an existing entry for this call_uuid
    #  - Forwarded flow:  entry was created in /api/plivo/menu with the Q/A transcript absent.
    #                     We want to run the full pipeline on the recording.
    #  - AI flow finalized entry: already has transcript+summary from Q/A turns; we only set the URL.
    attached_call_id: Optional[str] = None
    already_processed: bool = False
    with _plivo_calls_lock:
        for item in _plivo_calls:
            if item.get("call_uuid") == call_uuid and not item.get("recording_url"):
                item["recording_url"] = recording_url
                if not item.get("duration"):
                    item["duration"] = duration
                # If the entry already has a summary (AI flow), don't re-process.
                if item.get("summary"):
                    already_processed = True
                else:
                    item["status"] = "queued"
                attached_call_id = item["id"]
                break

    if attached_call_id:
        if not already_processed:
            background_tasks.add_task(_process_plivo_recording, attached_call_id, recording_url)
        return {"status": "attached", "call_id": attached_call_id}

    # No matching entry — create a new one and run full transcription+summarization
    with _conversations_lock:
        conv = _conversations.get(call_uuid) or {}
        from_number = conv.get("from_number", "")
        to_number = conv.get("to_number", "")
        lang = conv.get("language")

    call_id = str(uuid.uuid4())
    _save_plivo_call({
        "id": call_id,
        "call_uuid": call_uuid,
        "from_number": from_number,
        "to_number": to_number,
        "agent_number": None,
        "agent_name": LANG_CONFIG.get(lang, {}).get("label") if lang else None,
        "started_at": datetime.utcnow().isoformat(),
        "status": "queued",
        "recording_url": recording_url,
        "duration": duration,
        "transcript": None,
        "transcript_english": None,
        "summary": None,
        "error": None,
        "finished_at": None,
    })
    background_tasks.add_task(_process_plivo_recording, call_id, recording_url)
    return {"status": "queued", "call_id": call_id}


def _transcribe_turn(call_uuid: str, turn_idx: int, recording_url: str, lang: str):
    """Background: download + transcribe one turn's recording."""
    try:
        auth = (PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN) if _plivo_configured() else None
        resp = requests.get(recording_url, auth=auth, timeout=120)
        if resp.status_code != 200:
            return
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
            tmp.write(resp.content)
            tmp_path = tmp.name
        try:
            sarvam_lang = LANG_CONFIG.get(lang, LANG_CONFIG["en"])["code"]
            text = transcribe_audio(tmp_path, "turn.mp3", "audio/mpeg", sarvam_lang)
            with _conversations_lock:
                conv = _conversations.get(call_uuid)
                if conv and turn_idx < len(conv["history"]):
                    conv["history"][turn_idx]["user_text"] = (text or "").strip()
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
    except Exception as e:
        with _conversations_lock:
            conv = _conversations.get(call_uuid)
            if conv and turn_idx < len(conv["history"]):
                conv["history"][turn_idx]["user_text"] = f"[transcription error: {e}]"


def _stitch_conversation_audio(call_uuid: str, conv: dict) -> Optional[str]:
    """
    Build a single MP3 of the whole call by concatenating:
      [TTS Q1, user recording A1, TTS Q2, user recording A2, ..., TTS closing]
    Returns a public URL to the stitched file, or None if stitching failed.
    """
    lang = conv.get("language") or "en"
    segments: list[str] = []   # local file paths in playback order
    tmp_downloads: list[str] = []

    try:
        for i, turn in enumerate(conv.get("history", [])):
            # 1) AI question audio (already in TTS cache)
            q_text = turn.get("question_text")
            if q_text:
                try:
                    segments.append(_generate_tts(q_text, lang))
                except Exception:
                    pass
            # 2) User's answer audio (download from Plivo)
            rec_url = turn.get("recording_url")
            if rec_url:
                try:
                    auth = (PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN) if _plivo_configured() else None
                    r = requests.get(rec_url, auth=auth, timeout=120)
                    if r.status_code == 200:
                        fd, path = tempfile.mkstemp(suffix=".mp3", prefix=f"stitch_{call_uuid[:8]}_{i}_")
                        with os.fdopen(fd, "wb") as f:
                            f.write(r.content)
                        segments.append(path)
                        tmp_downloads.append(path)
                except Exception:
                    pass

        # 3) Closing TTS
        try:
            segments.append(_generate_tts(CLOSING.get(lang, CLOSING["en"]), lang))
        except Exception:
            pass

        if not segments:
            return None

        # ffmpeg concat with filter (handles mixed sample rates / formats)
        out_filename = f"stitched_{call_uuid[:12]}_{int(time.time())}.mp3"
        out_path = _TTS_DIR / out_filename
        cmd = [FFMPEG_BIN, "-y"]
        for s in segments:
            cmd += ["-i", s]
        filter_str = "".join(f"[{i}:a]" for i in range(len(segments))) + f"concat=n={len(segments)}:v=0:a=1[out]"
        cmd += ["-filter_complex", filter_str, "-map", "[out]", "-b:a", "96k", str(out_path)]
        result = subprocess.run(cmd, capture_output=True, timeout=180)
        if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            return None
        return f"{SERVER_BASE_URL}/api/plivo/audio/{out_filename}"

    finally:
        for p in tmp_downloads:
            try:
                os.unlink(p)
            except OSError:
                pass


def _finalize_conversation(call_uuid: str):
    """After call ends: wait for all turn transcriptions, then summarize.
    Idempotent — safe to call multiple times (e.g. once from the natural end of script,
    once from the Plivo hangup webhook in case the caller dropped early)."""
    # Idempotency check: claim the finalization slot atomically
    with _conversations_lock:
        conv = _conversations.get(call_uuid)
        if not conv:
            return
        if conv.get("finalized"):
            return
        conv["finalized"] = True

    for _ in range(60):  # wait up to 2 minutes
        with _conversations_lock:
            conv = _conversations.get(call_uuid)
            if not conv:
                return
            done = all(t.get("user_text") is not None for t in conv["history"])
        if done:
            break
        time.sleep(2)

    with _conversations_lock:
        conv = _conversations.get(call_uuid)
        if not conv:
            return
        lang = conv.get("language") or "en"
        lines = []
        for t in conv["history"]:
            lines.append(f"Q: {t['question_text']}")
            lines.append(f"A: {t.get('user_text') or '[no answer]'}")
        transcript = "\n".join(lines)

    try:
        sarvam_lang = LANG_CONFIG.get(lang, LANG_CONFIG["en"])["code"]
        transcript_en = translate_to_english(transcript, sarvam_lang)
        summary = summarize_transcript(transcript_en)
        err = None
    except Exception as e:
        transcript_en = transcript
        summary = None
        err = str(e)

    with _conversations_lock:
        conv = _conversations.get(call_uuid)
        if conv:
            conv["transcript"] = transcript
            conv["transcript_english"] = transcript_en
            conv["summary"] = summary
            conv["finished_at"] = datetime.utcnow().isoformat()
            conv["status"] = "done" if summary else "error"
            snapshot = dict(conv)

    # Stitch the whole-call audio (AI questions + user answers + closing) into one MP3
    stitched_url = None
    try:
        stitched_url = _stitch_conversation_audio(call_uuid, snapshot)
    except Exception as e:
        if err is None:
            err = f"audio stitching failed: {e}"

    # Mirror into the calls list so the existing frontend surfaces it
    _save_plivo_call({
        "id": str(uuid.uuid4()),
        "call_uuid": call_uuid,
        "from_number": snapshot.get("from_number", ""),
        "to_number": snapshot.get("to_number", ""),
        "agent_number": None,
        "agent_name": LANG_CONFIG.get(lang, {}).get("label"),
        "started_at": snapshot.get("started_at"),
        "status": "done" if summary else "error",
        "recording_url": stitched_url,
        "duration": None,
        "transcript": transcript,
        "transcript_english": transcript_en,
        "summary": summary,
        "error": err,
        "finished_at": snapshot.get("finished_at"),
    })


@app.post("/api/plivo/answer")
async def plivo_answer(request: Request):
    """
    First leg of any inbound call: play the top-level IVR menu.
      1 → forward to salesperson (Dial XML)
      2 → AI voice assistant (conversational flow)
    The caller's digit is posted to /api/plivo/menu which branches from there.
    """
    form = await request.form()
    call_uuid = form.get("CallUUID", "") or str(uuid.uuid4())
    from_number = form.get("From", "")
    to_number = form.get("To", "")

    # OUTBOUND path: if this answer webhook carries our `outbound_pending` query
    # param, /api/agent/call initiated this call. Pull the stashed customer
    # context (name, enquiry_no) from _conversations under the pending key and
    # promote it to the real CallUUID so the WebSocket handler can find it.
    outbound_pending = request.query_params.get("outbound_pending")
    if outbound_pending:
        with _conversations_lock:
            pending = _conversations.pop(outbound_pending, None) or {}
            _conversations[call_uuid] = {
                "call_uuid": call_uuid,
                "from_number": from_number,
                "to_number": to_number,
                "started_at": datetime.utcnow().isoformat(),
                # `awaiting_language`: customer hasn't picked yet — we'll
                # promote to "agent" inside /api/plivo/outbound_lang once we
                # know mr / hi / en.
                "status": "awaiting_language",
                "language": None,
                "question_index": -1,
                "history": [],
                "covered_question_ids": [],
                "transcript": None,
                "transcript_english": None,
                "summary": None,
                "finished_at": None,
                # Outbound customer context — read by the WS handler to
                # personalise the agent's greeting.
                "outbound": True,
                "customer_name": pending.get("customer_name"),
                "enquiry_no": pending.get("enquiry_no"),
            }
        # Play intro + language menu first; Stream handoff happens after digit.
        return Response(content=_intro_lang_menu_xml(call_uuid, is_outbound=True), media_type="application/xml")

    # DEV bypass: when DEV_SKIP_IVR is set to "mr" / "hi" / "en", skip the IVR menu
    # and route the call straight to the Pipecat conversational agent (LLM-driven, not
    # the scripted Q&A flow). Unset in production. Empty value = normal IVR.
    skip_lang = (os.getenv("DEV_SKIP_IVR") or "").strip().lower()
    if skip_lang in ("mr", "hi", "en"):
        with _conversations_lock:
            _conversations[call_uuid] = {
                "call_uuid": call_uuid,
                "from_number": from_number,
                "to_number": to_number,
                "started_at": datetime.utcnow().isoformat(),
                "status": "agent",
                "language": skip_lang,
                "question_index": -1,
                "history": [],
                "covered_question_ids": [],
                "transcript": None,
                "transcript_english": None,
                "summary": None,
                "finished_at": None,
            }
        return Response(content=_agent_stream_xml(call_uuid), media_type="application/xml")

    # Detect outbound calls initiated via /api/plivo/dial (the Dial tab in the
    # frontend). Those calls don't carry an `outbound_pending` token, but their
    # answer webhook lands here with From=<our DID> and To=<customer phone>.
    # Without this detection we'd treat them as inbound and the agent would
    # see the Plivo DID as the "customer's phone" — leaking into callback_number.
    def _strip_plus(n: str) -> str:
        return (n or "").lstrip("+")
    configured_dids = {_strip_plus(k) for k in AGENT_MAPPING}
    is_outbound_dial = _strip_plus(from_number) in configured_dids if configured_dids else False

    with _conversations_lock:
        _conversations[call_uuid] = {
            "call_uuid": call_uuid,
            "from_number": from_number,
            "to_number": to_number,
            "started_at": datetime.utcnow().isoformat(),
            "status": "awaiting_language",
            "language": None,
            "question_index": -1,
            "history": [],
            "covered_question_ids": [],
            "transcript": None,
            "transcript_english": None,
            "summary": None,
            "finished_at": None,
            # When True, the WS handler treats this as outbound: caller_phone
            # resolves to to_number (the customer), not from_number (our DID).
            "outbound": is_outbound_dial,
        }
    return Response(
        content=_intro_lang_menu_xml(call_uuid, is_outbound=is_outbound_dial),
        media_type="application/xml",
    )


@app.post("/api/plivo/menu")
async def plivo_menu(request: Request, call_uuid: str):
    """
    Top-level IVR router.
      Digit 1 → forward to salesperson via <Dial> (callerId = our Plivo DID, per Plivo India rules)
      Digit 2 → enter the AI conversational flow: ask caller to pick a language, then hand off to /api/plivo/turn
    """
    form = await request.form()
    digits = form.get("Digits", "")

    with _conversations_lock:
        conv = _conversations.get(call_uuid)

    if not conv:
        xml = ('<?xml version="1.0" encoding="UTF-8"?>'
               '<Response><Speak voice="Polly.Aditi" language="en-IN">Session expired. Goodbye.</Speak><Hangup/></Response>')
        return Response(content=xml, media_type="application/xml")

    to_number = conv.get("to_number", "")
    from_number = conv.get("from_number", "")

    # ── Option 1: Forward to salesperson ──
    if digits == "1":
        agent = AGENT_MAPPING.get(to_number)
        if not agent and to_number and not to_number.startswith("+"):
            agent = AGENT_MAPPING.get("+" + to_number)
        if not agent and len(AGENT_MAPPING) == 1:
            agent = next(iter(AGENT_MAPPING.values()))

        if not agent:
            xml = ('<?xml version="1.0" encoding="UTF-8"?>'
                   '<Response>'
                   '<Speak voice="Polly.Aditi" language="en-IN">'
                   'Sorry, no sales agent is configured for this number. Goodbye.'
                   '</Speak><Hangup/></Response>')
            return Response(content=xml, media_type="application/xml")

        numbers = agent.get("numbers") or [agent["number"]]
        names = agent.get("names") or [agent["name"]]
        display_agents = " / ".join(names)

        # Register this call in the processed-calls list for bookkeeping
        call_id = str(uuid.uuid4())
        _save_plivo_call({
            "id": call_id,
            "call_uuid": call_uuid,
            "from_number": from_number,
            "to_number": to_number,
            "agent_number": ", ".join(numbers),  # all numbers ringing
            "agent_name": display_agents,
            "started_at": datetime.utcnow().isoformat(),
            "status": "ringing",
            "recording_url": None,
            "duration": None,
            "transcript": None,
            "transcript_english": None,
            "summary": None,
            "error": None,
            "finished_at": None,
        })

        with _conversations_lock:
            conv["status"] = "forwarded"
            conv["agent_number"] = ", ".join(numbers)
            conv["agent_name"] = display_agents

        dial_action_cb = f"{SERVER_BASE_URL}/api/plivo/dial_action?call_id={call_id}"
        # IMPORTANT (per Plivo India rules, Suraj @ Plivo Support 2026-04-22):
        # callerId MUST be a Plivo-owned or verified number, not the caller's number.
        # Inbound calls:  to_number is our DID, from_number is the external caller.
        # Outbound calls: from_number is our DID, to_number is the customer.
        # Match against AGENT_MAPPING keys (our configured DIDs) to pick the right one.
        def _strip_plus(n: str) -> str:
            return (n or "").lstrip("+")
        configured_dids = {_strip_plus(k) for k in AGENT_MAPPING}
        if _strip_plus(to_number) in configured_dids:
            plivo_did = to_number
        elif _strip_plus(from_number) in configured_dids:
            plivo_did = from_number
        elif AGENT_MAPPING:
            plivo_did = next(iter(AGENT_MAPPING.keys()))
        else:
            plivo_did = to_number  # last-ditch fallback

        # Kick off full-session recording via Plivo REST API (Dial XML doesn't support recording).
        # Runs in a background thread with retries since the call may not be fully "answered"
        # state the instant we return this XML. Recording URL is delivered to
        # /api/plivo/session_recording when the call ends.
        threading.Thread(target=_start_session_recording, args=(call_uuid,), daemon=True).start()

        # Multiple <Number> elements inside <Dial> ring in parallel — first to pick up wins.
        number_elements = "".join(f"<Number>{n}</Number>" for n in numbers)

        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Response>'
            '<Speak voice="Polly.Aditi" language="en-IN">'
            'Connecting your call. This conversation will be recorded for quality.'
            '</Speak>'
            # Plivo's <Dial> element does NOT support inline recording attributes.
            # `record`, `recordSession`, `recordCallbackUrl` on <Dial> are silently ignored.
            # To record a forwarded call on Plivo, use the REST API Record endpoint (kicked off
            # below in a background thread). The recording URL is delivered to
            # /api/plivo/session_recording when the call ends.
            f'<Dial action="{dial_action_cb}" method="POST" '
            f'callerId="{plivo_did}" '
            f'timeout="30" timeLimit="3600">'
            f'{number_elements}'
            f'</Dial>'
            '</Response>'
        )
        return Response(content=xml, media_type="application/xml")

    # ── Option 2: AI conversational flow (Pipecat LLM agent over Plivo Media Stream) ──
    if digits == "2":
        with _conversations_lock:
            conv["status"] = "agent"
            conv["language"] = "mr"  # default; the agent itself will adapt to the caller
        return Response(content=_agent_stream_xml(call_uuid), media_type="application/xml")

    # Invalid selection
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<Response><Speak voice="Polly.Aditi" language="en-IN">Invalid selection. Goodbye.</Speak><Hangup/></Response>')
    return Response(content=xml, media_type="application/xml")


@app.post("/api/plivo/turn")
async def plivo_turn(request: Request, call_uuid: str, background_tasks: BackgroundTasks, default_lang: Optional[str] = None):
    """
    Every subsequent leg of the conversation.
    - If this request is delivering a language digit, pick the language and ask Q0.
    - If this request is delivering a recording URL, save+queue transcription, then ask the next Q.
    - If GetDigits timed out and we redirected here with `default_lang` query param, fall back to that.
    - After the last Q, play closing and hang up.
    """
    form = await request.form()
    digits = form.get("Digits", "") or (default_lang or "")
    recording_url = form.get("RecordUrl") or form.get("RecordingUrl") or ""

    with _conversations_lock:
        conv = _conversations.get(call_uuid)

    if not conv:
        xml = ('<?xml version="1.0" encoding="UTF-8"?>'
               '<Response><Speak voice="Polly.Aditi" language="en-IN">Session expired. Goodbye.</Speak><Hangup/></Response>')
        return Response(content=xml, media_type="application/xml")

    turn_action = f"{SERVER_BASE_URL}/api/plivo/turn?call_uuid={call_uuid}"

    # Language selection
    if conv["status"] == "language_selection":
        lang_map = {"1": "hi", "2": "mr", "3": "en"}
        lang = lang_map.get(digits)
        if not lang:
            # Default to Marathi if the caller didn't press a digit (and no fallback set).
            lang = "mr"
        with _conversations_lock:
            conv["language"] = lang
            conv["status"] = "questioning"
            conv["question_index"] = 0
        return Response(content=_ask_question_xml(turn_action, conv), media_type="application/xml")

    # Answer to a question
    if conv["status"] == "questioning":
        lang = conv["language"]
        q = CONVERSATION_SCRIPT[conv["question_index"]]
        with _conversations_lock:
            conv["history"].append({
                "question_id": q["id"],
                "question_text": q[lang],
                "recording_url": recording_url or None,
                "user_text": None if recording_url else "",
            })
            turn_idx = len(conv["history"]) - 1
            # The question we just played is now "covered" (we asked it)
            covered = list(conv.get("covered_question_ids") or [])
            if q["id"] not in covered:
                covered.append(q["id"])
            conv["covered_question_ids"] = covered

        # Background-transcribe; we'll briefly wait for the result on the critical path so the
        # smart-skip LLM has the customer's answer to reason about.
        if recording_url:
            background_tasks.add_task(_transcribe_turn, call_uuid, turn_idx, recording_url, lang)
            _wait_for_user_text(call_uuid, turn_idx, timeout_sec=6.0)

        # Smart Path A decision: ask Gemini which question to play next (and which to skip)
        with _conversations_lock:
            conv_snapshot = dict(conv)
            conv_snapshot["history"] = list(conv["history"])

        decision = _decide_next_question(conv_snapshot)

        # Merge the LLM's "now_covered" into our covered set
        if decision.get("now_covered"):
            with _conversations_lock:
                covered = list(conv.get("covered_question_ids") or [])
                for cid in decision["now_covered"]:
                    if cid not in covered:
                        covered.append(cid)
                conv["covered_question_ids"] = covered

        # Decide what to do next:
        #   1) LLM said close → close
        #   2) LLM picked a specific next question → use it
        #   3) LLM gave fallback / unusable answer → next sequential uncovered
        #   4) Nothing left to ask → close
        with _conversations_lock:
            covered_set = set(conv.get("covered_question_ids") or [])
            current_idx = conv["question_index"]

            next_idx = None
            if decision.get("should_close"):
                next_idx = None  # forces close path
                _close = True
            else:
                _close = False
                nxt_id = decision.get("next_question_id")
                if nxt_id:
                    next_idx = next(
                        (i for i, qq in enumerate(CONVERSATION_SCRIPT) if qq["id"] == nxt_id),
                        None,
                    )
                if next_idx is None:
                    next_idx = _next_uncovered_index(conv, current_idx + 1)
                if next_idx is None:
                    _close = True

            if not _close and next_idx is not None:
                conv["question_index"] = next_idx

        if _close:
            with _conversations_lock:
                conv["status"] = "wrap_up"
            background_tasks.add_task(_finalize_conversation, call_uuid)
            return Response(content=_closing_xml(conv), media_type="application/xml")

        return Response(content=_ask_question_xml(turn_action, conv), media_type="application/xml")

    # Default: hang up
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<Response><Hangup/></Response>')
    return Response(content=xml, media_type="application/xml")


def _wait_for_user_text(call_uuid: str, turn_idx: int, timeout_sec: float = 6.0) -> Optional[str]:
    """Block up to `timeout_sec` for the background _transcribe_turn to write user_text."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        with _conversations_lock:
            conv = _conversations.get(call_uuid)
            if conv and turn_idx < len(conv.get("history", [])):
                t = conv["history"][turn_idx].get("user_text")
                if t is not None:
                    return t
        time.sleep(0.3)
    return None


def _decide_next_question(conv: dict) -> dict:
    """
    Path A "smart agent" core: ask Gemini which question to play next.
    Returns a dict like:
      { "next_question_id": "phase" | None,
        "now_covered": ["business_type", "location"],
        "should_close": False }
    On any error, returns {"fallback": True} so the caller falls back to the next sequential.
    """
    if not OPENROUTER_API_KEY or OPENROUTER_API_KEY == "your_openrouter_api_key_here":
        return {"fallback": True}

    history = conv.get("history", [])
    covered = set(conv.get("covered_question_ids") or [])
    remaining = [q for q in CONVERSATION_SCRIPT if q["id"] not in covered]
    if not remaining:
        return {"should_close": True, "now_covered": [], "next_question_id": None}

    # Build a compact transcript for the LLM
    qa_pairs = []
    for h in history:
        ut = (h.get("user_text") or "").strip()
        if not ut:
            ut = "[answer not yet transcribed]"
        qa_pairs.append(f"Q ({h.get('question_id')}): {h.get('question_text','')}\nA: {ut}")
    qa_block = "\n\n".join(qa_pairs) if qa_pairs else "(no questions asked yet)"

    remaining_block = "\n".join(f'- "{q["id"]}": {q["en"]}' for q in remaining)

    prompt = f"""You are routing a sales call on behalf of Kala Genset (a genset / generator company).
The AI has been asking the customer questions one at a time. Look at what the customer has said
and decide which of the REMAINING questions to ask next, and which (if any) are already answered.

Questions already asked (with the customer's transcribed answers):
{qa_block}

Questions still on the script (not yet asked):
{remaining_block}

Your job:
1) Identify any of the remaining questions that the customer has ALREADY answered in their previous responses (so we should skip them).
2) Pick which remaining question to ask NEXT — a logical follow-up given the conversation flow.
3) If we have enough information OR the customer indicated they want to wrap up, set should_close=true.

Respond with ONLY a valid JSON object, no markdown fences. Schema:
{{
  "now_covered": ["question_id", "..."],
  "next_question_id": "question_id" | null,
  "should_close": true | false
}}

Notes:
- "now_covered" should ONLY contain question_ids from the "still on the script" list, not ones already asked.
- If the customer said "no" or "skip" or seems annoyed, prefer should_close=true.
- "next_question_id" must be a question_id that is currently in the "still on the script" list (and not in now_covered).
"""

    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "google/gemini-2.0-flash-001",
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=8,
        )
    except requests.RequestException:
        return {"fallback": True}

    if resp.status_code != 200:
        return {"fallback": True}

    text = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    if text.startswith("```"):
        # strip fence
        try:
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        except IndexError:
            return {"fallback": True}

    try:
        decision = json.loads(text)
    except json.JSONDecodeError:
        return {"fallback": True}

    # Sanitize
    valid_ids = {q["id"] for q in remaining}
    decision["now_covered"] = [x for x in decision.get("now_covered", []) if x in valid_ids]
    nxt = decision.get("next_question_id")
    if nxt and nxt not in valid_ids:
        decision["next_question_id"] = None
    decision["should_close"] = bool(decision.get("should_close"))
    return decision


def _next_uncovered_index(conv: dict, after_idx: int) -> Optional[int]:
    """Sequential fallback: first index >= after_idx whose question_id is not in covered."""
    covered = set(conv.get("covered_question_ids") or [])
    for i in range(after_idx, len(CONVERSATION_SCRIPT)):
        if CONVERSATION_SCRIPT[i]["id"] not in covered:
            return i
    return None


def _ask_question_xml(turn_action: str, conv: dict) -> str:
    lang = conv["language"]
    q = CONVERSATION_SCRIPT[conv["question_index"]]
    try:
        audio_url = _tts_url(q[lang], lang)
    except Exception as e:
        # Fallback: use Plivo <Speak> if TTS fails (English only fallback)
        return ('<?xml version="1.0" encoding="UTF-8"?>'
                '<Response>'
                f'<Speak voice="Polly.Aditi" language="en-IN">TTS error. {str(e)[:100]}. Goodbye.</Speak>'
                '<Hangup/></Response>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        f'<Play>{audio_url}</Play>'
        f'<Record action="{turn_action}" method="POST" '
        'maxLength="60" finishOnKey="#" playBeep="true" timeout="2"/>'
        '</Response>'
    )


def _closing_xml(conv: dict) -> str:
    lang = conv.get("language", "en")
    try:
        audio_url = _tts_url(CLOSING[lang], lang)
        play = f'<Play>{audio_url}</Play>'
    except Exception:
        play = '<Speak voice="Polly.Aditi" language="en-IN">Thank you. Goodbye.</Speak>'
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            f'<Response>{play}<Hangup/></Response>')


# Aliases — match the URLs some Plivo setup guides use
app.add_api_route("/plivo/incoming", plivo_answer, methods=["POST"])


@app.post("/api/plivo/dial_action")
async def plivo_dial_action(request: Request, call_id: Optional[str] = None):
    """Plivo posts here after the dial finishes (agent hung up, no-answer, busy, etc.)."""
    form = await request.form()
    dial_status = (form.get("DialStatus") or "").lower()
    dial_hangup_cause = form.get("DialHangupCause", "")

    # Map Plivo DialStatus to our top-level status. "completed" means Varun answered and
    # talked — recording is expected to arrive shortly at /api/plivo/recording, which will
    # transition status through downloading → transcribing → ... → done.
    # Anything else is terminal: no recording will come, and the UI should stop saying "ringing".
    status_map = {
        "completed": "awaiting_recording",
        "answer":    "awaiting_recording",
        "no-answer": "no_answer",
        "busy":      "busy",
        "failed":    "failed",
        "timeout":   "no_answer",
        "cancel":    "cancelled",
    }
    new_status = status_map.get(dial_status, f"ended ({dial_status or 'unknown'})")

    if call_id:
        _update_plivo_call(
            call_id,
            status=new_status,
            dial_status=dial_status,
            dial_hangup_cause=dial_hangup_cause,
            finished_at=datetime.utcnow().isoformat(),
        )
        # If the call was answered, poll Plivo's Recording API to retrieve the MP3 URL —
        # Plivo's automatic post-recording callback doesn't fire reliably for us.
        if new_status == "awaiting_recording":
            call_uuid_for_lookup = form.get("CallUUID") or form.get("ALegUUID") or ""
            if call_uuid_for_lookup:
                threading.Thread(
                    target=_poll_and_attach_recording,
                    args=(call_uuid_for_lookup, call_id),
                    daemon=True,
                ).start()
    # Empty response → Plivo hangs up the call
    return Response(content='<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>',
                    media_type="application/xml")


@app.post("/api/plivo/recording")
async def plivo_recording(
    background_tasks: BackgroundTasks,
    request: Request,
    call_id: Optional[str] = None,
):
    """
    Plivo POSTs here after a recording is finished.
    Common form fields: RecordUrl, RecordingDuration, CallUUID, From, To.
    We queue the actual heavy work (download + transcribe + summarize) as a background task
    so Plivo gets an immediate 200 OK.
    """
    form = await request.form()
    recording_url = form.get("RecordUrl") or form.get("RecordingUrl") or ""
    duration = form.get("RecordingDuration") or form.get("Duration")
    call_uuid = form.get("CallUUID", "")

    if not recording_url:
        raise HTTPException(status_code=400, detail="Missing RecordUrl in Plivo callback")

    # If no call_id in query (edge case), create a new entry
    if not call_id:
        call_id = str(uuid.uuid4())
        _save_plivo_call({
            "id": call_id,
            "call_uuid": call_uuid,
            "from_number": form.get("From", ""),
            "to_number": form.get("To", ""),
            "started_at": datetime.utcnow().isoformat(),
            "status": "queued",
            "recording_url": recording_url,
            "duration": duration,
            "transcript": None,
            "transcript_english": None,
            "summary": None,
            "error": None,
            "finished_at": None,
        })
    else:
        _update_plivo_call(
            call_id,
            recording_url=recording_url,
            duration=duration,
            status="queued",
            call_uuid=call_uuid or None,
        )

    background_tasks.add_task(_process_plivo_recording, call_id, recording_url)

    # Return empty XML so Plivo hangs up cleanly
    if PLIVO_AVAILABLE:
        response = plivoxml.ResponseElement()
        response.add_speak("Thank you. Goodbye.", voice="Polly.Aditi", language="en-IN")
        return Response(content=response.to_string(), media_type="application/xml")
    return Response(content="<Response/>", media_type="application/xml")


@app.post("/api/plivo/hangup")
async def plivo_hangup(request: Request, background_tasks: BackgroundTasks):
    """
    Plivo's hangup callback fires when a call ends.
    For an AI-flow conversation that's still mid-script (caller dropped early),
    we kick off `_finalize_conversation` so the partial answers still produce
    a transcript + summary in the Recorded Calls tab.
    """
    form = await request.form()
    call_uuid = form.get("CallUUID", "")

    # Mark any old "recording"-state plivo_calls as hung up (legacy voicemail flow)
    with _plivo_calls_lock:
        for item in _plivo_calls:
            if item.get("call_uuid") == call_uuid and item["status"] == "recording":
                item["status"] = "hungup_no_recording"
                item["finished_at"] = datetime.utcnow().isoformat()

    # AI conversational flow: if the conversation is still in-progress, finalize it now
    # so we get whatever answers were captured before the caller hung up.
    with _conversations_lock:
        conv = _conversations.get(call_uuid)
        in_progress = (
            conv is not None
            and not conv.get("finalized")
            and conv.get("status") in ("language_selection", "questioning", "wrap_up")
            and len(conv.get("history") or []) > 0
        )
    if in_progress:
        background_tasks.add_task(_finalize_conversation, call_uuid)

    return {"status": "ok"}


class PlivoImportInput(BaseModel):
    recording_url: str
    from_number: Optional[str] = None
    to_number: Optional[str] = None


class PlivoDialInput(BaseModel):
    number: str
    note: Optional[str] = None


def _normalize_indian_e164(raw: str) -> str:
    """Coerce a phone number to E.164 (+91 default). Returns empty string if invalid."""
    s = (raw or "").strip()
    s = s.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not s:
        return ""
    if s.startswith("+"):
        rest = "".join(c for c in s[1:] if c.isdigit())
        return "+" + rest if rest else ""
    digits = "".join(c for c in s if c.isdigit()).lstrip("0")
    if not digits:
        return ""
    if digits.startswith("91") and len(digits) >= 12:
        return "+" + digits
    if len(digits) == 10:
        return "+91" + digits
    return "+" + digits


@app.post("/api/plivo/dial")
def plivo_dial(data: PlivoDialInput):
    """
    Initiate an outbound call to `data.number`. When the callee picks up,
    Plivo hits our /api/plivo/answer URL and the standard IVR menu plays
    (press 1 for sales agent, press 2 for AI assistant).
    """
    if not _plivo_configured():
        raise HTTPException(400, "Plivo credentials not configured in .env")
    if not AGENT_MAPPING:
        raise HTTPException(400, "AGENT_MAPPING not configured in .env (need at least one DID)")

    target = _normalize_indian_e164(data.number)
    if not target or len(target) < 11:
        raise HTTPException(400, f"Invalid phone number: {data.number!r}")

    from_did = next(iter(AGENT_MAPPING.keys()))
    answer_url = f"{SERVER_BASE_URL}/api/plivo/answer"
    url = f"https://api.plivo.com/v1/Account/{PLIVO_AUTH_ID}/Call/"
    payload = {
        "from": from_did,
        "to": target,
        "answer_url": answer_url,
        "answer_method": "POST",
    }
    try:
        resp = requests.post(url, json=payload, auth=(PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN), timeout=15)
    except requests.RequestException as e:
        raise HTTPException(502, f"Plivo unreachable: {e}")
    if resp.status_code >= 400:
        raise HTTPException(502, f"Plivo rejected request ({resp.status_code}): {resp.text[:300]}")

    body = resp.json()
    return {
        "status": "queued",
        "from": from_did,
        "to": target,
        "request_uuid": body.get("request_uuid"),
        "api_id": body.get("api_id"),
    }


@app.post("/api/plivo/import")
async def plivo_import(data: PlivoImportInput, background_tasks: BackgroundTasks):
    """
    Manually queue an existing Plivo recording for transcription + summarization.
    Useful for recovering calls that failed to process earlier.
    Paste the recording URL from Plivo → Logs → Recordings.
    """
    if not data.recording_url.strip():
        raise HTTPException(status_code=400, detail="recording_url is required")

    call_id = str(uuid.uuid4())
    _save_plivo_call({
        "id": call_id,
        "call_uuid": None,
        "from_number": data.from_number or "imported",
        "to_number": data.to_number or "",
        "agent_number": None,
        "agent_name": None,
        "started_at": datetime.utcnow().isoformat(),
        "status": "queued",
        "recording_url": data.recording_url,
        "duration": None,
        "transcript": None,
        "transcript_english": None,
        "summary": None,
        "error": None,
        "finished_at": None,
    })
    background_tasks.add_task(_process_plivo_recording, call_id, data.recording_url)
    return {"status": "queued", "call_id": call_id}


@app.get("/api/plivo/recordings")
def plivo_recordings(limit: int = 50, offset: int = 0):
    """Fetch all recordings on the Plivo account."""
    if not _plivo_configured():
        raise HTTPException(status_code=400, detail="Plivo credentials not configured in .env")

    url = f"https://api.plivo.com/v1/Account/{PLIVO_AUTH_ID}/Recording/"
    params = {"limit": limit, "offset": offset}
    try:
        resp = requests.get(url, auth=(PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN), params=params, timeout=30)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Plivo API unreachable: {e}")

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Plivo API error ({resp.status_code}): {resp.text}")

    data = resp.json()
    return {
        "recordings": data.get("objects", []),
        "total": data.get("meta", {}).get("total_count", 0),
    }


@app.post("/api/plivo/calls/{call_id}/retry")
def plivo_retry_recording(call_id: str):
    """
    Re-run Plivo's Recording API lookup for a stuck call (status=awaiting_recording).
    Useful when the original poller thread died (backend restart) or exhausted its retries.
    """
    with _plivo_calls_lock:
        target = next((c for c in _plivo_calls if c["id"] == call_id), None)

    if not target:
        raise HTTPException(status_code=404, detail="Call not found")
    if target.get("recording_url"):
        raise HTTPException(status_code=400, detail="Recording already attached")
    call_uuid = target.get("call_uuid")
    if not call_uuid:
        raise HTTPException(status_code=400, detail="No call_uuid on this entry to look up")

    rec = _fetch_recording_by_call_uuid(call_uuid)
    if not rec or not rec.get("recording_url"):
        raise HTTPException(status_code=404, detail=f"No recording found on Plivo for call_uuid {call_uuid}")

    recording_url = rec["recording_url"]
    duration_ms = rec.get("recording_duration_ms")
    duration_sec = int(duration_ms / 1000) if duration_ms else None

    with _plivo_calls_lock:
        target["recording_url"] = recording_url
        target["duration"] = duration_sec
        target["status"] = "queued"

    threading.Thread(
        target=_process_plivo_recording,
        args=(call_id, recording_url),
        daemon=True,
    ).start()
    return {"status": "queued", "recording_url": recording_url, "duration": duration_sec}


@app.get("/api/plivo/calls")
def list_plivo_calls():
    """List all Plivo calls processed by this backend (most recent first)."""
    with _plivo_calls_lock:
        return {"calls": list(reversed(_plivo_calls))}


@app.get("/api/plivo/calls/{call_id}")
def get_plivo_call(call_id: str):
    with _plivo_calls_lock:
        for item in _plivo_calls:
            if item["id"] == call_id:
                return item
    raise HTTPException(status_code=404, detail="Call not found")


# =========================================================
# PIPECAT CONVERSATIONAL AGENT — Plivo Media Streams integration
# =========================================================
# Architecture:
#   /api/plivo/answer   → returns <Stream> XML pointing at /api/agent/plivo/ws
#   /api/agent/plivo/ws → bidirectional WebSocket; runs Pipecat pipeline
#                         (Sarvam STT → sarvam-30b LLM → Sarvam TTS) against
#                         the call's audio stream.
#
# This replaces the legacy CONVERSATION_SCRIPT-based <Record> Q&A loop for the
# AI-assistant path (press 2). Press 1 (forward to salesperson) is unchanged.

def _intro_lang_menu_xml(call_uuid: str, *, is_outbound: bool) -> str:
    """Play an intro + DTMF language menu before handing off to the AI agent.

    Used on BOTH inbound and outbound calls so the customer:
      1. Hears "this is Kala Genset" up front.
      2. Picks their preferred language via DTMF (1=Marathi, 2=Hindi, 3=English).
    The selected digit is captured by /api/plivo/outbound_lang (legacy name —
    handles both directions) which then streams to the Pipecat agent with the
    chosen language pre-set.
    """
    action = f"{SERVER_BASE_URL}/api/plivo/outbound_lang?call_uuid={call_uuid}"
    # IMPORTANT: `&` in URLs must be `&amp;` when emitted inside XML or Plivo's
    # parser rejects the response and hangs up the call immediately on pickup.
    redirect_url = (
        f"{SERVER_BASE_URL}/api/plivo/outbound_lang?call_uuid={call_uuid}&amp;default=mr"
    )
    # We split the intro into three <Speak> tags so the company name is read
    # by Polly.Aditi in hi-IN mode with Devanagari script — this fixes the
    # "Kala" pronunciation (sounds like *art*, not *black*). The English
    # wrapper text uses en-IN. Plivo plays sequential <Speak>s back-to-back
    # so the customer hears one continuous intro.
    if is_outbound:
        intro_en_pre = 'Hello. This call is from'
        intro_en_post = ', regarding your generator enquiry.'
    else:
        intro_en_pre = 'Hello. Thank you for calling'
        intro_en_post = '.'
    # Devanagari company name — Polly.Aditi hi-IN reads this with proper
    # Indic pronunciation. The "ा" in कला is the short SCHWA-A, not the
    # long आ — that's what makes it "Kala" (art) instead of "Kaala" (black).
    intro_brand_devanagari = 'कला जेनसेट प्राइवेट लिमिटेड'

    # CRITICAL: wrap the ENTIRE intro inside <GetDigits> so digits pressed
    # while the company name is still being announced are captured. The
    # earlier shape played the intro OUTSIDE GetDigits — Plivo wasn't
    # listening yet, so early digit presses were dropped silently and the
    # call fell through to the `default=mr` Redirect (logs showed
    # `lang=mr voice=manisha` even when the caller pressed 3 for English).
    #
    # Also bumped timeout 6 → 10s so callers who wait until the menu
    # finishes have a realistic window to react. `firstDigitTimeout="20"`
    # gives a generous early window across the intro itself.
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        f'<GetDigits action="{action}" method="POST" numDigits="1" '
        f'timeout="10" retries="1" firstDigitTimeout="20">'
        f'<Speak voice="Polly.Aditi" language="en-IN">{intro_en_pre}</Speak>'
        f'<Speak voice="Polly.Aditi" language="hi-IN">{intro_brand_devanagari}</Speak>'
        f'<Speak voice="Polly.Aditi" language="en-IN">{intro_en_post}</Speak>'
        # Plivo uses <Wait>, not Twilio's <Pause>. Using the wrong tag
        # causes Plivo's XML parser to reject the entire response and
        # hang up the call on pickup.
        '<Wait length="1"/>'
        '<Speak voice="Polly.Aditi" language="en-IN">'
        'To continue in Marathi, press 1. For Hindi, press 2. For English, press 3.'
        '</Speak>'
        '</GetDigits>'
        # No digit pressed within firstDigitTimeout → fall back to Marathi.
        f'<Redirect method="POST">{redirect_url}</Redirect>'
        '</Response>'
    )


def _agent_stream_xml(call_uuid: str) -> str:
    """Plivo XML that hands the call audio to our Pipecat agent over WebSocket.

    Plivo opens a WebSocket to the URL inside <Stream>; the first JSON message it
    sends is "connected", then "start" with streamId/callId. We use those to
    initialise PlivoFrameSerializer.
    """
    base = SERVER_BASE_URL
    if base.startswith("https://"):
        ws_base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        ws_base = "ws://" + base[len("http://"):]
    else:
        ws_base = base
    ws_url = f"{ws_base}/api/agent/plivo/ws?call_uuid={call_uuid}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        f'<Stream bidirectional="true" keepCallAlive="true" '
        f'contentType="audio/x-mulaw;rate=8000" '
        f'streamTimeout="1800">{ws_url}</Stream>'
        '</Response>'
    )


@app.post("/api/plivo/outbound_lang")
async def plivo_outbound_lang(request: Request, call_uuid: str, default: Optional[str] = None):
    """Handle the language digit chosen on an outbound call's intro menu.

    Receives DTMF digit from /api/plivo/answer's GetDigits, sets the language
    on the conversation, and then hands off to the Pipecat agent stream.
    Falls back to Marathi if no digit was pressed (via the `default` query
    param set by the <Redirect> in `_outbound_intro_lang_xml`).
    """
    form = await request.form()
    digits = (form.get("Digits") or "").strip()
    lang_map = {"1": "mr", "2": "hi", "3": "en"}
    lang = lang_map.get(digits) or (default or "mr")

    with _conversations_lock:
        conv = _conversations.get(call_uuid)
        if conv is not None:
            conv["language"] = lang
            conv["status"] = "agent"

    return Response(content=_agent_stream_xml(call_uuid), media_type="application/xml")


@app.websocket("/api/agent/plivo/ws")
async def plivo_agent_ws(websocket: WebSocket, call_uuid: Optional[str] = None):
    """Run the Pipecat conversational agent against this Plivo Media Stream WebSocket."""
    # Lazy-imported so module load doesn't drag in pipecat (and torch/silero) for
    # non-agent endpoints. Keeps cold-start of the analyzer endpoints light.
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.frames.frames import EndFrame, TTSSpeakFrame
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineTask, PipelineParams
    from pipecat.transports.websocket.fastapi import (
        FastAPIWebsocketTransport,
        FastAPIWebsocketParams,
    )
    from pipecat.serializers.plivo import PlivoFrameSerializer

    from agent.bot import build_pipeline as build_agent_pipeline

    await websocket.accept()
    print(f"[agent-ws] accepted ws for call_uuid={call_uuid}")

    # Plivo's first messages on the stream:
    #   {"event":"connected", ...}
    #   {"event":"start", "start":{"streamId":"...", "callId":"...", ...}}
    # We need streamId + callId before instantiating the serializer.
    stream_id: Optional[str] = None
    call_id: Optional[str] = None
    try:
        for _ in range(5):  # bound: we expect start within first few messages
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            evt = data.get("event")
            if evt == "start":
                start = data.get("start", {})
                stream_id = start.get("streamId") or start.get("stream_id")
                # Plivo sends "callId"; some docs mention "callUuid" — accept both.
                call_id = start.get("callId") or start.get("call_id") or start.get("callUuid")
                break
    except WebSocketDisconnect:
        print("[agent-ws] disconnected before start event")
        return

    if not stream_id:
        print("[agent-ws] no streamId received; closing")
        await websocket.close()
        return

    print(f"[agent-ws] start received streamId={stream_id} callId={call_id}")

    # auto_hang_up requires call_id + plivo creds; degrade gracefully if missing.
    can_auto_hangup = bool(call_id and PLIVO_AUTH_ID and PLIVO_AUTH_TOKEN)
    serializer = PlivoFrameSerializer(
        stream_id=stream_id,
        call_id=call_id if can_auto_hangup else None,
        auth_id=PLIVO_AUTH_ID if can_auto_hangup else None,
        auth_token=PLIVO_AUTH_TOKEN if can_auto_hangup else None,
        params=PlivoFrameSerializer.InputParams(auto_hang_up=can_auto_hangup),
    )

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
            add_wav_header=False,
            vad_analyzer=SileroVADAnalyzer(),
            serializer=serializer,
        ),
    )

    # Reuse the shared aiohttp session (created lazily at app startup) so the
    # agent's Sarvam HTTP TTS doesn't have to spin up a new connection pool
    # for every call.
    http_session = await _get_aiohttp_session()

    # Pull conversation context (registered in /api/plivo/answer) so we can:
    #   - attach from/to numbers to the captured lead
    #   - personalise the agent greeting for outbound calls
    with _conversations_lock:
        _conv_for_lead = _conversations.get(call_uuid or "") or {}
    _from_number = _conv_for_lead.get("from_number", "")
    _to_number = _conv_for_lead.get("to_number", "")
    _is_outbound = bool(_conv_for_lead.get("outbound"))
    # The customer's actual phone number:
    #   inbound  → they dialed us, so their number is from_number
    #   outbound → we dialed them, so their number is to_number
    # We pass this on every call so the agent can resolve "same number" /
    # "इसी नंबर पर" / "हाच नंबर" callback intents without asking the
    # customer to spell out digits.
    _caller_phone = _to_number if _is_outbound else _from_number
    _customer_context = {
        "caller_phone": _caller_phone,
        "is_outbound": _is_outbound,
        # Language pre-selected via DTMF in /api/plivo/outbound_lang. May be
        # None for inbound (agent decides via STT). For outbound this is one
        # of "mr" / "hi" / "en".
        "preferred_language": _conv_for_lead.get("language"),
    }
    if _is_outbound:
        _customer_context["customer_name"] = _conv_for_lead.get("customer_name")
        _customer_context["enquiry_no"] = _conv_for_lead.get("enquiry_no")

    # Inferred from customer_name (where known). bot.py reads this to pick the
    # right honorific in the opener brief and the system prompt tells the LLM
    # to keep using it consistently throughout the call.
    _customer_context["is_female"] = _is_female_name(_customer_context.get("customer_name"))

    # Mutable flag — flipped to True inside _on_agent_save_lead if the agent
    # called save_lead during the conversation. Used at end-of-call to decide
    # whether we need to run the post-call extractor as a fallback.
    _save_lead_called = {"yes": False}

    async def _on_agent_save_lead(arguments: dict):
        """Bridge from agent.bot.save_lead → our SQL leads store."""
        _save_lead_called["yes"] = True
        _save_agent_lead(
            lead_id=call_uuid or str(uuid.uuid4()),
            from_number=_from_number,
            to_number=_to_number,
            fields=arguments or {},
        )

    pipeline, _context = build_agent_pipeline(
        transport,
        aiohttp_session=http_session,
        on_save_lead=_on_agent_save_lead,
        customer_context=_customer_context,
    )
    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))

    @transport.event_handler("on_client_connected")
    async def _on_connected(_t, _client):
        # Speak a hardcoded greeting via Sarvam TTS the moment the pipeline is
        # ready, instead of queueing an LLMRunFrame and waiting ~1-1.5s for
        # Claude's first-token round-trip. The caller picked their language on
        # the IVR (`preferred_language` in customer_context), so we know which
        # template to use. We pre-add the greeting to the LLM context as an
        # assistant message so the model sees its "own" turn happened and
        # responds naturally to whatever the caller says next — without
        # re-greeting.
        greeting = _agent_greeting_text(
            preferred_language=_customer_context.get("preferred_language"),
            customer_name=_customer_context.get("customer_name"),
            is_outbound=bool(_customer_context.get("is_outbound")),
        )
        # NOTE: do NOT print the greeting text itself — on Windows stdout is
        # cp1252 by default and Devanagari characters raise UnicodeEncodeError,
        # which would crash this event handler and prevent the TTS frame from
        # ever being queued (silent call). Log only ASCII-safe metadata.
        print(
            f"[agent-ws] pipeline connected — pushing greeting "
            f"(call_uuid={call_uuid}, "
            f"lang={_customer_context.get('preferred_language')}, "
            f"len={len(greeting)})"
        )
        _context.add_message({"role": "assistant", "content": greeting})
        await task.queue_frames([TTSSpeakFrame(greeting)])

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnected(_t, _client):
        print(f"[agent-ws] pipeline disconnected (call_uuid={call_uuid})")
        await task.queue_frames([EndFrame()])

    runner = PipelineRunner(handle_sigint=False)
    try:
        await runner.run(task)
    except Exception as e:
        print(f"[agent-ws] pipeline crashed: {e!r}")
    finally:
        # Post-call extractor: if save_lead never fired during the call
        # (customer hung up early, network drop, agent didn't reach closing),
        # extract fields from the transcript so EVERY call still produces a
        # Summary row. Without this, half the real-world calls would leave
        # the dashboard empty even though the data was spoken.
        if not _save_lead_called["yes"]:
            try:
                extracted = await _extract_lead_from_transcript(
                    _context, http_session,
                    caller_phone=_caller_phone,
                    customer_name=_customer_context.get("customer_name") if _customer_context else None,
                    preferred_language=_customer_context.get("preferred_language") if _customer_context else None,
                )
                if extracted:
                    print(f"[post-call extract] saved {len(extracted)} fields for call_uuid={call_uuid}")
                    _save_agent_lead(
                        lead_id=call_uuid or str(uuid.uuid4()),
                        from_number=_from_number,
                        to_number=_to_number,
                        fields=extracted,
                    )
                else:
                    print(f"[post-call extract] nothing to save for call_uuid={call_uuid} (transcript too short or LLM error)")
            except Exception as e:
                print(f"[post-call extract] failed: {type(e).__name__}: {e}")
        with _conversations_lock:
            conv = _conversations.get(call_uuid or "") if call_uuid else None
            if conv:
                conv["finished_at"] = datetime.utcnow().isoformat()
        print(f"[agent-ws] done (call_uuid={call_uuid})")


# ── Active calls API ──────────────────────────────────────────────────────
# Reads in-memory `_conversations` for calls without a `finished_at` timestamp.
# Cheap snapshot for monitoring — no SQL hit.

@app.get("/api/agent/active-calls")
def list_active_calls():
    """Return calls currently in progress (no `finished_at` yet)."""
    with _conversations_lock:
        snapshot = list(_conversations.values())
    active = []
    for c in snapshot:
        if c.get("finished_at"):
            continue
        # Skip pending outbound stubs (pre-CallUUID promotion); they don't
        # represent real in-flight audio.
        status = c.get("status") or ""
        if status in ("pending_outbound",):
            continue
        active.append({
            "call_uuid": c.get("call_uuid"),
            "from_number": c.get("from_number"),
            "to_number": c.get("to_number"),
            "started_at": c.get("started_at"),
            "language": c.get("language"),
            "status": status,
            "is_outbound": bool(c.get("outbound")),
            "customer_name": c.get("customer_name"),
            "enquiry_no": c.get("enquiry_no"),
        })
    return {"active_calls": active, "count": len(active)}


# ── AI agent leads API ────────────────────────────────────────────────────
# Reads from dbo.AICallSummary (SQL). Frontend's Summary view consumes these.

@app.get("/api/agent/leads")
def list_agent_leads(month: Optional[str] = None, limit: int = 500):
    """Return AI-captured leads from dbo.AICallSummary, newest first.

    Query params:
        month: 'YYYY-MM' to filter by captured_at (optional; default = all)
        limit: max rows (default 500, cap 5000)
    """
    from erp import fetch_summaries
    try:
        items = fetch_summaries(month=month, limit=min(limit, 5000))
        return {"leads": items, "count": len(items)}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"SQL read failed: {type(e).__name__}: {e}")


@app.get("/api/agent/leads/{lead_id}")
def get_agent_lead(lead_id: str):
    """Return a single captured lead by its call_uuid."""
    from erp import fetch_summaries
    try:
        items = fetch_summaries(month=None, limit=5000)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"SQL read failed: {type(e).__name__}: {e}")
    for entry in items:
        if entry["id"] == lead_id:
            return entry
    raise HTTPException(status_code=404, detail="Lead not found")


# ── ERP integration ──────────────────────────────────────────────────────
# Read-only access to the ERP `dbo.Enquiry` table for the "Call Customers"
# frontend. The outbound call endpoint here uses Plivo's REST API to dial
# the customer; when they pick up, Plivo hits /api/plivo/answer and the
# existing Pipecat agent flow takes over (with the customer's name and
# enquiry number stashed in _conversations for a personalised greeting).

@app.get("/api/erp/health")
def erp_health():
    """Is the ERP SQL Server reachable?"""
    from erp import health_check
    return health_check()


@app.get("/api/erp/enquiries")
def erp_enquiries(month: Optional[str] = None, limit: int = 5000):
    """List NEW/unverified enquiries (ConfMobile='N') from dbo.Enquiry for a
    given month (default: current). These are leads where the customer's
    mobile hasn't been confirmed yet — they're what the AI agent should call.

    Query params:
        month: 'YYYY-MM' (optional, defaults to current month)
        limit: hard cap on rows (default 5000, max 10000)
    """
    from erp import fetch_enquiries
    try:
        items = fetch_enquiries(month=month, limit=min(limit, 10000))
        resolved_month = month or datetime.utcnow().strftime("%Y-%m")
        return {"enquiries": items, "count": len(items), "month": resolved_month}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        # Surface enough to debug, hide creds.
        raise HTTPException(status_code=503, detail=f"ERP read failed: {type(e).__name__}: {e}")


class AgentOutboundCallRequest(BaseModel):
    phone: str                                   # E.164, e.g. +919876543210
    customer_name: Optional[str] = None          # for personalised greeting
    enquiry_no: Optional[str] = None             # to link the lead back to ERP


@app.post("/api/agent/call")
def agent_outbound_call(req: AgentOutboundCallRequest):
    """Initiate an outbound call to a customer via Plivo. When they pick up,
    Plivo hits /api/plivo/answer and the existing AI agent flow takes over.

    The agent picks up customer_name + enquiry_no from _conversations and uses
    them to greet the customer by name and reference the enquiry.
    """
    if not _plivo_configured():
        raise HTTPException(status_code=503, detail="Plivo credentials not configured in .env")

    # Validate phone — pyodbc upstream should have normalised it, but defend.
    phone = (req.phone or "").strip()
    if not phone.startswith("+") or len(phone) < 10:
        raise HTTPException(status_code=400, detail=f"phone must be E.164 (got {phone!r})")

    # Plivo expects the source number to be one of our DIDs. Pick the first
    # configured DID — for KalaGenset that's the inbound number we already use.
    if not AGENT_MAPPING:
        raise HTTPException(status_code=503, detail="AGENT_MAPPING not configured in .env")
    plivo_did = next(iter(AGENT_MAPPING.keys()))

    # Pre-create the conversation entry so when Plivo's answer webhook arrives
    # for this call, /api/plivo/answer can find the customer context. We use
    # a synthetic call_uuid; Plivo's actual CallUUID arrives in the webhook and
    # we'll reconcile by looking up the most recent pending_outbound entry.
    pending_id = f"outbound-pending:{uuid.uuid4()}"
    with _conversations_lock:
        _conversations[pending_id] = {
            "call_uuid": pending_id,
            "outbound_to": phone,
            "from_number": plivo_did,
            "to_number": phone,
            "started_at": datetime.utcnow().isoformat(),
            "status": "outbound_initiated",
            "customer_name": req.customer_name,
            "enquiry_no": req.enquiry_no,
            "language": "mr",
            "history": [],
        }

    # Fire the Plivo REST call. We use the SDK so the request is signed correctly.
    try:
        import plivo as _plivo_sdk
        client = _plivo_sdk.RestClient(auth_id=PLIVO_AUTH_ID, auth_token=PLIVO_AUTH_TOKEN)
        answer_url = f"{SERVER_BASE_URL}/api/plivo/answer?outbound_pending={pending_id}"
        hangup_url = f"{SERVER_BASE_URL}/api/plivo/hangup"
        response = client.calls.create(
            from_=plivo_did,
            to_=phone,
            answer_url=answer_url,
            answer_method="POST",
            hangup_url=hangup_url,
            hangup_method="POST",
        )
        # The SDK returns a Response object; the actual Plivo CallUUID lands in
        # response.request_uuid (and then later as CallUUID in the webhook).
        plivo_request_uuid = getattr(response, "request_uuid", None) or getattr(response, "message", "")
        return {
            "status": "initiated",
            "pending_id": pending_id,
            "plivo_request_uuid": str(plivo_request_uuid),
            "to": phone,
            "from": plivo_did,
        }
    except Exception as e:
        with _conversations_lock:
            _conversations.pop(pending_id, None)
        raise HTTPException(status_code=502, detail=f"Plivo call failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
