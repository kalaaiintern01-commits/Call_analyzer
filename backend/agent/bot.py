"""Pipecat agent for Kala Genset enquiry capture.

Phase 1: browser-testable agent using SmallWebRTC transport. Speak via your laptop mic
to validate quality before wiring up Plivo telephony in Phase 2.

Run standalone for browser testing:
    python -m pipecat.runner.run agent.bot:bot --transport webrtc

Or imported by main.py for the integrated /api/agent/offer endpoint.
"""

import os
import re
import time
from typing import Optional

from dotenv import load_dotenv
from loguru import logger

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    LLMRunFrame,
    TTSSpeakFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.sarvam.llm import SarvamLLMService, SarvamLLMSettings
from pipecat.services.sarvam.stt import SarvamSTTService, SarvamSTTSettings
from pipecat.services.sarvam.tts import SarvamTTSService, SarvamTTSSettings

from .prompt import SYSTEM_PROMPT


load_dotenv(override=True)


# ── Speculative-acknowledgment processor ──────────────────────────────────
# Reduces *perceived* latency dramatically by emitting a short filler TTS
# frame the moment the user stops speaking. The customer hears something
# ("हम्म...", "Okay sir...", etc.) within ~200-300 ms instead of the
# 1.5-2.5 s of dead air that the LLM + TTS round-trip normally produces.
# By the time the real LLM response starts streaming through TTS, the
# filler has finished and the conversation flows naturally.
#
# Placed BETWEEN llm and tts in the pipeline so the TTSSpeakFrame we push
# downstream doesn't need to traverse the LLM service (which doesn't
# consume it but adds a small frame-handling delay).
class FillerAcknowledgmentProcessor(FrameProcessor):
    """Emits a brief language-appropriate filler TTS frame on every
    `UserStoppedSpeakingFrame` so the customer perceives the bot as
    responsive even while the LLM is still generating.

    Filler text is intentionally:
    - SHORT (1-2 words) so it finishes before the LLM's real reply starts
    - GENERIC (no specific content) so it never contradicts what the LLM
      eventually says
    - ROTATING across a small phrase pool so back-and-forth turns don't
      sound robotic
    """

    # Filler pools keyed by IVR-selected language. All phrases are 1-2
    # syllables — meant to feel like a natural conversational backchannel.
    _FILLERS = {
        "mr": ["हम्म...", "बरं...", "ठीक आहे...", "अच्छा..."],
        "hi": ["हम्म...", "जी sir...", "ठीक है...", "अच्छा..."],
        "en": ["Hmm...", "Okay sir...", "Got it...", "Sure..."],
    }

    def __init__(self, *, language: str = "mr", min_interval_secs: float = 2.5):
        """Args:
            language: 'mr' / 'hi' / 'en' — picks the filler pool. Falls back
                to Marathi if unrecognised.
            min_interval_secs: debounce — don't emit fillers more often than
                this. Prevents stacking on rapid back-and-forth utterances.
        """
        super().__init__()
        self._language = (language or "mr").lower()
        if self._language not in self._FILLERS:
            self._language = "mr"
        self._min_interval = float(min_interval_secs)
        self._last_filler_ts = 0.0
        self._idx = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        # MUST call super().process_frame() so the base class sets up the
        # processor's internal state (Pipecat 1.x requirement).
        await super().process_frame(frame, direction)

        # Pass the original frame through first — never swallow anything.
        await self.push_frame(frame, direction)

        # Hook: user just stopped speaking → emit an acknowledgment filler.
        if isinstance(frame, UserStoppedSpeakingFrame):
            now = time.monotonic()
            if now - self._last_filler_ts < self._min_interval:
                return  # debounce — too soon since last filler
            fillers = self._FILLERS[self._language]
            phrase = fillers[self._idx % len(fillers)]
            self._idx += 1
            self._last_filler_ts = now
            logger.debug(f"[filler] emitting '{phrase}' (lang={self._language})")
            # Downstream: filler → TTS → audio out, in parallel with the
            # LLM doing its actual work.
            await self.push_frame(TTSSpeakFrame(phrase), FrameDirection.DOWNSTREAM)


# ── English-path pronunciation fixes ──────────────────────────────────────
# The English Sarvam voice (anushka, en-IN) applies English phonetics, so it
# mispronounces some domain words ("diesel" → wrong). We respell each offender
# to a form the voice says correctly. This is wired as a TTS `text_transform`
# that runs on the FULLY-AGGREGATED sentence just before synthesis, so a word
# is never split across streamed LLM tokens — and ONLY on the English path
# (Marathi/Hindi pronounce these correctly already and must not be touched).
#
# This is deterministic: the fix lands 100% of the time, independent of whether
# the LLM remembered a prompt rule. To add a word: run backend/_tts_pron_test.py
# with candidate spellings, pick the one that sounds right by ear, add it below.
# Keys are matched case-insensitively on whole words.
# Place names & domain words — matched CASE-INSENSITIVELY. These aren't normal
# English words, so capitalisation slips ("kothrud"/"Kothrud") should all match.
# NOTE: the hyphenated respellings are HYPOTHESES until heard with the anushka
# voice — verify with backend/_tts_batch_test.py and tune anything that sounds
# off. Identity mappings (e.g. "Camp"→"Camp") are intentionally omitted — they'd
# be no-ops.
_EN_PLACE_PRONUNCIATION = {
    "diesel": "deezel",   # ear-confirmed
    # ── Pune City ──
    "Shivajinagar": "Shi-va-jee-na-gar",
    "Deccan": "Deck-an",
    "Kothrud": "Ko-throod",
    "Karve Nagar": "Kar-vay Na-gar",
    "Erandwane": "Ee-rand-va-nay",
    "Sadashiv Peth": "Sa-da-shiv Peth",
    "Narayan Peth": "Na-ra-yan Peth",
    "Swargate": "Swar-gate",
    "Parvati": "Par-va-tee",
    "Bibwewadi": "Bib-way-wa-dee",
    "Sahakar Nagar": "Sa-ha-kar Na-gar",
    "Dhankawadi": "Dhan-ka-wa-dee",
    "Hadapsar": "Ha-dap-sar",
    "Magarpatta": "Ma-gar-pat-ta",
    "Kharadi": "Kha-ra-dee",
    "Viman Nagar": "Vee-man Na-gar",
    "Wagholi": "Wag-ho-lee",
    "Mundhwa": "Moon-dhwa",
    "Koregaon Park": "Ko-ray-gaon Park",
    "Kalyani Nagar": "Kal-yaa-nee Na-gar",
    "Yerawada": "Ye-ra-wa-da",
    "Lohegaon": "Lo-hay-gaon",
    "Dhanori": "Dha-no-ree",
    "Vishrantwadi": "Vish-rant-wa-dee",
    "Baner": "Bay-ner",
    "Balewadi": "Ba-lay-wa-dee",
    "Aundh": "Aund",
    "Pashan": "Paa-shaan",
    "Bavdhan": "Baav-dhan",
    "Warje": "War-jay",
    "Sus": "Soos",
    "Mahalunge": "Ma-ha-loon-gay",
    "Kondhwa": "Kon-dhwa",
    "Undri": "Oon-dree",
    "Mohammed Wadi": "Mo-ham-med Wa-dee",
    "Wanowrie": "Wa-no-ree",
    # ── PCMC ──
    "Pimpri": "Pim-pree",
    "Chinchwad": "Chinch-wad",
    "Akurdi": "A-kur-dee",
    "Nigdi": "Nig-dee",
    "Ravet": "Ra-vet",
    "Punawale": "Poo-na-wa-lay",
    "Tathawade": "Ta-tha-wa-day",
    "Wakad": "Wa-kad",
    "Pimple Saudagar": "Pim-pul Sau-da-gar",
    "Pimple Gurav": "Pim-pul Goo-raav",
    "Sangvi": "Sang-vee",
    "Bhosari": "Bho-sa-ree",
    "Moshi": "Mo-shee",
    "Charholi": "Char-ho-lee",
    "Chikhali": "Chi-kha-lee",
    "Talawade": "Ta-la-wa-day",
    "Pradhikaran": "Pra-dhi-ka-ran",
    "Thergaon": "Ther-gaon",
    "Kalewadi": "Ka-lay-wa-dee",
    "Rahatani": "Ra-ha-ta-nee",
    "Dapodi": "Da-po-dee",
    "Kasarwadi": "Ka-sar-wa-dee",
    # ── IT hubs ──
    "Hinjewadi": "Hin-je-wa-dee",
    "Rajiv Gandhi Infotech Park": "Ra-jeev Gandhi Info-tech Park",
    "EON IT Park": "E O N I T Park",
    # ── Stations ──
    "Shivajinagar Station": "Shi-va-jee-na-gar Station",
    "NIBM Road": "N I B M Road",
}

# Acronyms — matched CASE-SENSITIVELY (uppercase only). Case-insensitive here
# would mangle ordinary words: "ml" (millilitres) → "M L", "rag" → "R A G".
_EN_ACRONYM_PRONUNCIATION = {
    "kVA": "k-V-A",
    "AMF": "A-M-F",
    "PCMC": "P C M C",
    "AI": "A I",
    "ML": "M L",
    "AIML": "A I M L",
    "API": "A P I",
    "CRM": "C R M",
    "ERP": "E R P",
    "HRMS": "H R M S",
    "LLM": "L L M",
    "RAG": "R A G",
    "NLP": "N L P",
    "IoT": "I O T",
}


def _compile_pron_map(mapping: dict, case_insensitive: bool):
    """Build (regex, lookup) for a respelling map. Longest key first so
    multi-word keys win over their substrings; word-boundary anchored."""
    if not mapping:
        return None, {}
    flags = re.IGNORECASE if case_insensitive else 0
    rx = re.compile(
        r"\b(" + "|".join(
            re.escape(k) for k in sorted(mapping, key=len, reverse=True)
        ) + r")\b",
        flags,
    )
    lookup = {k.lower(): v for k, v in mapping.items()} if case_insensitive else dict(mapping)
    return rx, lookup


_PLACE_RE, _PLACE_LOOKUP = _compile_pron_map(_EN_PLACE_PRONUNCIATION, case_insensitive=True)
_ACRONYM_RE, _ACRONYM_LOOKUP = _compile_pron_map(_EN_ACRONYM_PRONUNCIATION, case_insensitive=False)


async def _en_pronunciation_transform(text: str, aggregation_type) -> str:
    """TTS text_transform (English path only): respell mispronounced words.

    Signature matches Pipecat's transform contract: async, takes the aggregated
    sentence text + its aggregation type, returns the transformed text. Returns
    the input unchanged when nothing matches. Place names match case-
    insensitively; acronyms case-sensitively (so "ml"/"rag" aren't mangled).
    """
    if not text:
        return text
    out = text
    if _PLACE_RE:
        out = _PLACE_RE.sub(lambda m: _PLACE_LOOKUP[m.group(0).lower()], out)
    if _ACRONYM_RE:
        out = _ACRONYM_RE.sub(lambda m: _ACRONYM_LOOKUP[m.group(0)], out)
    # TEMP DIAGNOSTIC: log exactly what the LLM produced and what the TTS will
    # speak, so we can see whether names arrive in Latin or Devanagari and what
    # the transform did. Remove once pronunciation is dialed in.
    logger.info(f"[pron] IN={text!r}  OUT={out!r}")
    return out


# ── Tools the agent can call ───────────────────────────────────────────────
# These are exposed to the LLM as functions; the LLM calls them by name with JSON args.

save_lead_schema = FunctionSchema(
    name="save_lead",
    description=(
        "Save the captured customer information as a lead. Call this once at the end "
        "of the call, after all fields have been covered (or the customer is ending the "
        "call early). "
        "\n\n"
        "**ALWAYS WRITE ALL FIELD VALUES IN ENGLISH (Latin script).** This includes "
        "call_summary, location, designation, business_type, other_questions, etc. — "
        "the sales team's ERP is English-only. Transliterate names if needed: "
        "'श्वेतांक' → 'Shvetank', 'पुणे शिवाजीनगर' → 'Pune, Shivajinagar', "
        "'सीईओ' → 'CEO', 'कॉर्पोरेट ऑफिस' → 'corporate office'. The spoken conversation "
        "stays in the customer's language (Marathi/Hindi); only the structured save_lead "
        "values must be English. call_summary specifically: 2-3 short English sentences."
        "\n\n"
        "CRITICAL: If you don't have a value for a field, OMIT THE FIELD ENTIRELY from "
        "the JSON. Do NOT include it with an empty string or null — that produces "
        "invalid JSON and the call will hang. Only `call_summary` is required; every "
        "other field should be present ONLY when you have a concrete answer for it. "
        "For booleans, send true/false explicitly — never empty. For arrays like "
        "competitor_brands, send [] only if the customer was asked and said no brands."
    ),
    properties={
        # WHO
        "customer_name": {"type": "string", "description": "English / Latin script. Transliterate if needed (e.g. 'श्वेतांक' → 'Shvetank')"},
        "designation": {"type": "string", "description": "English. e.g. 'owner', 'purchase manager', 'engineer', 'CEO'"},
        "company_name": {"type": "string", "description": "English. Transliterate if needed (e.g. 'इन्फोसिस' → 'Infosys')"},
        # WHAT / WHERE
        "business_type": {"type": "string", "description": "English. factory / hotel / hospital / IT park / retail / residential / agriculture / construction / other"},
        "location": {"type": "string", "description": "English. City + brief site area, e.g. 'Pune, Hinjewadi'"},
        # WHY
        "purpose": {"type": "string", "description": "English. backup / prime / continuous / industrial"},
        "new_or_replacement": {"type": "string", "description": "English. 'new' or 'replacement'. If replacement, append brand/age, e.g. 'replacement: Cummins 125 kVA, 8 yrs'"},
        # SIZING
        "capacity_kva": {"type": "string", "description": "Numeric kVA value as string, e.g. '35', '100', '250'"},
        "load_calculated": {"type": "string", "description": "English. 'self', 'consultant', 'estimated', or 'not_done'"},
        # CONFIG
        "phase": {"type": "string", "description": "English. single_phase or three_phase"},
        "fuel_type": {"type": "string", "description": "English. diesel / gas / petrol. **Auto-rule**: if capacity_kva > 58.5, this is ALWAYS 'diesel' (petrol/gas not available at that size) — do not ask the customer, set silently."},
        "amf_required": {"type": "boolean"},
        "canopy_required": {"type": "string", "description": "English. **ALWAYS 'silent_canopy'** — silent canopy is compulsory on every unit कला जेनसेट sells. Never ask the customer; set this to 'silent_canopy' on every save_lead call."},
        # TIMING + COMMERCIAL
        "timeline": {"type": "string", "description": "English. When they want to purchase, e.g. 'within 1 month', '3-6 months', 'this FY'"},
        "site_visit_ok": {"type": "boolean", "description": "true if customer is open to our engineer visiting for a survey"},
        "competitor_quotes_received": {"type": "boolean"},
        "competitor_brands": {"type": "array", "items": {"type": "string"}, "description": "English brand names, e.g. ['Cummins', 'Mahindra Powerol']"},
        "budget_range": {"type": "string", "description": "English. e.g. '20 lakh', '3-5 lakh', 'not disclosed'"},
        # CONTACT (critical for follow-up)
        "callback_number": {"type": "string", "description": "Best phone number for follow-up, e.g. '+919876543210' or '9876543210' — no spaces. If the customer says 'same number' / 'इसी नंबर पर' / 'हाच नंबर' / 'इथेच कॉल करा' or similar, use the `Customer's current phone` from the Call context brief verbatim — do NOT ask them to spell out the digits."},
        "email": {"type": "string", "description": "Email ID where formal quotation should be sent"},
        # WRAP
        "other_questions": {"type": "string", "description": "English. Anything else the customer asked or mentioned that the sales team should know"},
        "language_used": {"type": "string", "description": "marathi / hindi / english / mix — the language the customer spoke in"},
        "call_summary": {"type": "string", "description": "**English, 2-3 short sentences.** Concise summary of who/what/when/budget that a sales rep can scan in 5 seconds."},
        "hot_lead": {"type": "boolean", "description": "true if customer seems serious / ready to buy"},
    },
    required=["call_summary"],
)

transfer_schema = FunctionSchema(
    name="transfer_to_human",
    description=(
        "Transfer the call to a human salesperson (Varun or Arpita). Call this if the "
        "customer asks to speak to a real person or has questions you cannot answer."
    ),
    properties={
        "reason": {"type": "string", "description": "why we're transferring"},
    },
    required=["reason"],
)

TOOLS_SCHEMA = ToolsSchema(standard_tools=[save_lead_schema, transfer_schema])


# ── Tool implementations ───────────────────────────────────────────────────
# Default handlers just log. main.py overrides save_lead via a per-call
# callback (see build_pipeline below) so leads land in the dashboard.

async def _default_save_lead_handler(params: FunctionCallParams):
    """Fallback when no on_save_lead callback was passed (e.g. webrtc runner)."""
    logger.info(f"[LEAD CAPTURED] {params.arguments}")
    await params.result_callback({"status": "saved"})


async def transfer_to_human_handler(params: FunctionCallParams):
    """Transfer the call. Phase 1: just log. Phase 2: invoke Plivo REST API to dial agent."""
    logger.info(f"[TRANSFER REQUESTED] {params.arguments}")
    await params.result_callback({"status": "transfer_initiated", "message": "Connecting you to our team."})


def build_pipeline(
    transport,
    aiohttp_session=None,
    on_save_lead=None,
    customer_context=None,
):
    """Build the Pipecat pipeline: transport → STT → LLM → TTS → transport.

    Args:
        transport: Pipecat transport (FastAPIWebsocketTransport for Plivo, etc.)
        aiohttp_session: shared aiohttp.ClientSession for Sarvam HTTP TTS.
            REQUIRED for telephony — without it we'd have to create a per-call
            session and leak it. Caller (main.py) maintains one app-wide session.
        on_save_lead: optional async callback `(arguments: dict) -> None` invoked
            whenever the LLM calls the `save_lead` tool. main.py uses this to
            persist the captured lead into the dashboard's in-memory store.
        customer_context: optional dict for OUTBOUND calls — when /api/agent/call
            initiates a call from an ERP enquiry, this carries the customer's
            name + enquiry_no so the agent can greet by name and reference the
            enquiry. Keys: customer_name, enquiry_no, to_number. None for
            inbound calls (agent uses the default introductory greeting).
    """
    if aiohttp_session is None:
        # Fallback for non-telephony invocations (webrtc runner). Create a per-
        # call session; it will leak, but that's acceptable for dev/test.
        import aiohttp
        aiohttp_session = aiohttp.ClientSession()

    sarvam_key = os.getenv("SARVAM_API_KEY", "")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
    openai_key = os.getenv("OPENAI_API_KEY", "")

    if not sarvam_key:
        raise RuntimeError("SARVAM_API_KEY not set in .env (used for both STT and TTS)")
    # Prefer direct Anthropic over OpenRouter when the key is present:
    # saves ~100-300ms per turn (one less hop) and gives us first-party
    # access to prompt-cache hit/miss metrics. Fall back to OpenRouter if
    # the direct key isn't set so existing deployments keep working.
    if not anthropic_key and not openrouter_key:
        raise RuntimeError(
            "Neither ANTHROPIC_API_KEY nor OPENROUTER_API_KEY is set in .env "
            "— one is needed for the Claude LLM."
        )

    _pref = ((customer_context or {}).get("preferred_language") or "").strip().lower()
    _sarvam_lang_map = {"mr": "mr-IN", "hi": "hi-IN", "en": "en-IN"}
    _sarvam_lang = _sarvam_lang_map.get(_pref, "mr-IN")

    # STT — Sarvam Saarika v2.5 streaming.
    # Pinned per IVR-selected language (mr/hi/en). Marathi default.
    # NOTE: must use settings=... in pipecat 1.x — top-level `language=` and
    # `model=` parameters are silently ignored.
    # (Deepgram swap reverted — kept the import + .env key for future trials.)
    stt = SarvamSTTService(
        api_key=sarvam_key,
        settings=SarvamSTTSettings(
            model="saarika:v2.5",
            language=_sarvam_lang,
        ),
    )

    # TTS — Sarvam Bulbul (WebSocket).
    # We briefly tried SarvamHttpTTSService to dodge the ~15s WS idle timeout,
    # but the HTTP variant returned warped audio (sample-rate mismatch — the
    # `sample_rate=8000` param doesn't seem to be respected by the HTTP API the
    # same way it is by the WS API). Going back to the WS service: audio quality
    # is correct, and we'll address the idle-timeout via a different mechanism
    # (TBD: TTS keep-alive heartbeat, or accept brief reconnect blips).
    #
    # sample_rate=8000 forces Sarvam to produce 8kHz PCM directly so the audio
    # bytes match the pipeline's audio_out_sample_rate (8000) and Plivo's
    # required 8kHz mulaw.
    # Voice selection is per-language: a Maharashtra-native speaker handles
    # Marathi/Hindi well but mispronounces English ("kVA" becomes "kuh-VAH",
    # brand names land wrong). Sarvam Bulbul v2's multilingual voices read
    # Indian English much more naturally, so we swap when the caller picked
    # English on the IVR. Each language can be overridden via env vars.
    #
    # Sarvam Bulbul v2 voices we've found useful here:
    #   anushka  — multilingual female, strongest Indian English diction
    #   manisha  — Maharashtra-native female (current Marathi default)
    #   vidya, arya, priya, kavya  — female (Marathi-strong)
    #   karun, hitesh, amol, soham, mohit  — male
    # Sarvam's `enable_preprocessing=True` normalises numbers, abbreviations,
    # and proper nouns before synthesis — meaningfully cleaner English
    # pronunciation. We keep it OFF for mr/hi because those languages were
    # tuned without it and the team verified that behaviour.
    if _pref == "en":
        # English path runs on Bulbul v3 + 'ritu' — chosen by ear for clearly
        # better Indian place/person-name pronunciation than v2/anushka.
        # 'ritu' is a v3-only voice; the model is switched to v3 below.
        _tts_voice = os.getenv("SARVAM_TTS_SPEAKER_EN", "ritu")
        _tts_preprocess = True
    elif _pref == "hi":
        _tts_voice = os.getenv("SARVAM_TTS_SPEAKER_HI", os.getenv("SARVAM_TTS_SPEAKER", "manisha"))
        _tts_preprocess = False
    else:  # mr or default
        _tts_voice = os.getenv("SARVAM_TTS_SPEAKER", "manisha")
        _tts_preprocess = False
    logger.info(f"[tts] language={_sarvam_lang} voice={_tts_voice} preprocess={_tts_preprocess}")

    # English runs on Bulbul v3 ('ritu'); mr/hi stay on v2 (manisha), which works.
    # 'ritu' is v3-only and anushka is v2-only, so model + voice are paired here.
    _tts_model = "bulbul:v3" if _pref == "en" else "bulbul:v2"
    _tts_kwargs = {}
    _settings_kwargs = dict(
        model=_tts_model,
        language=_sarvam_lang,
        voice=_tts_voice,
        enable_preprocessing=_tts_preprocess,
    )
    if _pref == "en":
        # English-only: respell known domain offenders (diesel, k-V-A…) just
        # before synthesis + slow pace slightly (0.9; range 0.3-3.0) for clearer
        # multi-syllable Indian names. mr/hi are left untouched.
        _tts_kwargs["text_transforms"] = [("*", _en_pronunciation_transform)]
        _settings_kwargs["pace"] = 0.9
        logger.info(f"[tts] english v3/ritu + pronunciation transform + pace=0.9 (model={_tts_model})")

    tts = SarvamTTSService(
        api_key=sarvam_key,
        sample_rate=8000,
        settings=SarvamTTSSettings(**_settings_kwargs),
        **_tts_kwargs,
    )
    # aiohttp_session is no longer used by TTS, but we keep the parameter on
    # build_pipeline() for forward compatibility (in case we revisit HTTP TTS
    # or add other HTTP-backed services later).
    _ = aiohttp_session

    # LLM — Claude Haiku 4.5
    #
    # We tried sarvam-30b first: fast (~2-3s/turn) and cheap, but consistently
    # failed at state tracking across our 18-field conversation — repeated
    # questions ("location" twice, "phase" twice, "kVA" twice in one call) and
    # leaked formatting (parenthetical option lists, raw field names like
    # "(new_or_replacement)"). Many prompt-engineering attempts didn't move the
    # needle.
    # We tried sarvam-105b: better instruction-following, but 60+ s/turn on a
    # phone call — unviable.
    # Claude Haiku 4.5 is the best of both: ~1-2s/turn, materially better at
    # state tracking + tool calls + multilingual (Marathi/Hindi/English)
    # instruction following.
    #
    # Provider preference: direct Anthropic API > OpenRouter.
    #   - Direct Anthropic saves ~100-300ms per turn (one less network hop).
    #   - Direct also gives reliable prompt-cache hit/miss metrics in
    #     Anthropic's console for tuning.
    #   - OpenRouter fallback keeps the agent working if the Anthropic key
    #     isn't configured (legacy deployments).
    if anthropic_key:
        # Prompt caching is OFF by default in pipecat's AnthropicLLMService —
        # we confirmed via the logs (`Cache creation input tokens: 0`,
        # `Cache read input tokens: 0` on every turn). Enabling it via the
        # Settings flag (NOT the deprecated `model=` kwarg) lets pipecat
        # forward the `cache_control: ephemeral` marker we set on the system
        # prompt to Anthropic. Our SYSTEM_PROMPT is ~7,100 tokens, well above
        # Haiku's 2,048 cache minimum, so once enabled every turn after the
        # first reads the cached prefix at ~10% input cost and ~50-200ms
        # faster TTFT.
        logger.info("[llm] using direct Anthropic API (claude-haiku-4-5, prompt caching ON)")
        llm = AnthropicLLMService(
            api_key=anthropic_key,
            settings=AnthropicLLMService.Settings(
                model="claude-haiku-4-5",
                enable_prompt_caching=True,
            ),
        )
        # Make it obvious in the per-call logs WHAT pipecat is caching, so when
        # we tail logs we don't have to remember the cache-marker indirection.
        # Counts are approximate (computed locally; Anthropic re-tokenises on
        # its side and the per-call totals show up as Cache creation/read
        # tokens in the pipecat debug stream — see the next-step log lines).
        _sysprompt_chars = len(SYSTEM_PROMPT)
        _sysprompt_tok_est = _sysprompt_chars // 4   # rough; Anthropic count was 7,112
        _tools_names = [t.name for t in getattr(TOOLS_SCHEMA, 'standard_tools', [])] or ['save_lead', 'transfer_to_human']
        logger.info(
            f"[llm][cache] enabled — caching prefix marked with "
            f"cache_control=ephemeral. Cached content:"
        )
        logger.info(
            f"[llm][cache]   1) SYSTEM_PROMPT  ({_sysprompt_chars:,} chars, "
            f"~{_sysprompt_tok_est:,} tok est / ~7,112 tok measured)"
        )
        logger.info(
            f"[llm][cache]   2) tools schema   ({len(_tools_names)} tools: {', '.join(_tools_names)})"
        )
        logger.info(
            f"[llm][cache]   3) conversation history grows incrementally — "
            f"each turn's prior exchange is added to the cached set"
        )
        logger.info(
            f"[llm][cache] Expect: turn-1 'Cache creation input tokens' > 0, "
            f"turns 2+ 'Cache read input tokens' >= prior cache size. "
            f"TTL = 5 min idle."
        )
    else:
        # OpenRouter exposes Claude via an OpenAI-compatible endpoint, so we
        # reuse pipecat's OpenAILLMService and just point base_url at it.
        logger.info("[llm] ANTHROPIC_API_KEY not set — falling back to OpenRouter")
        llm = OpenAILLMService(
            api_key=openrouter_key,
            base_url="https://openrouter.ai/api/v1",
            model="anthropic/claude-haiku-4.5",
            timeout=30.0,  # OpenRouter adds routing latency on top of Claude's own
        )

    # Register the agent's tools with the LLM
    # save_lead: if caller provided on_save_lead, wrap it so we both log AND
    # invoke the caller's callback (e.g. main.py persists to the dashboard).
    if on_save_lead is not None:
        async def _save_lead_handler(params: FunctionCallParams):
            logger.info(f"[LEAD CAPTURED] {params.arguments}")
            try:
                await on_save_lead(params.arguments)
            except Exception as e:
                logger.error(f"on_save_lead callback failed: {e!r}")
            await params.result_callback({"status": "saved"})
        llm.register_function("save_lead", _save_lead_handler)
    else:
        llm.register_function("save_lead", _default_save_lead_handler)
    llm.register_function("transfer_to_human", transfer_to_human_handler)

    # Conversation context.
    # For OUTBOUND calls (when /api/agent/call initiated the dial), we prepend
    # a second system message with the customer's name + enquiry number so the
    # agent greets them by name and references the ERP enquiry instead of using
    # the default "I'm calling about your genset enquiry" stub greeting.
    #
    # PROMPT CACHING — Anthropic via OpenRouter:
    # The system prompt is ~3.5KB of static content (script rules, product
    # knowledge, AMC info, pronunciation rules). It's identical for every
    # turn of every call. We mark it with `cache_control: ephemeral` so
    # Anthropic caches the tokenized prefix on first read; every subsequent
    # turn within the cache TTL (~5 min) reads it back at ~10% of the
    # uncached input cost AND saves ~50–200 ms of TTFT.
    # The per-call `## Call context` brief (appended below) intentionally
    # does NOT carry cache_control — it varies per call, so caching it
    # would waste cache writes.
    # OpenRouter accepts and forwards the structured-content `cache_control`
    # marker when routing to Anthropic models — for non-Anthropic models the
    # marker is silently ignored (safe).
    _messages = [{
        "role": "system",
        "content": [{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
    }]
    if customer_context:
        caller_phone = (customer_context.get("caller_phone") or "").strip()
        name = (customer_context.get("customer_name") or "").strip()
        enquiry_no = (customer_context.get("enquiry_no") or "").strip()
        is_outbound = bool(customer_context.get("is_outbound"))
        preferred_lang = (customer_context.get("preferred_language") or "").strip().lower()
        # main.py infers gender from the name via `_is_female_name`. Used to
        # pick the right honorific in the opener example below AND surfaced
        # to the LLM in the call-context brief so it stays consistent through
        # the whole conversation (closing line, ack interjections, etc.).
        is_female = bool(customer_context.get("is_female"))
        if preferred_lang == "en":
            honorific = "madam" if is_female else "sir"
        elif preferred_lang == "hi":
            honorific = "मैडम" if is_female else "सर"
        else:  # mr (and default)
            honorific = "मॅडम" if is_female else "सर"

        brief_lines = ["## Call context"]
        if is_outbound:
            brief_lines.append(
                "You are calling this customer OUTBOUND — they are not calling you. "
                "They previously submitted an enquiry to Kala Genset and we are "
                "following up to capture more details for a quotation."
            )
            if name:
                brief_lines.append(f"- Customer name on file: {name}")
            if enquiry_no:
                brief_lines.append(f"- ERP enquiry number: {enquiry_no}")
        # Pre-selected language via DTMF (outbound calls: 1=mr, 2=hi, 3=en).
        # When set, the customer has already heard our IVR intro ("This call
        # is from Kala Genset...") and picked a language — do NOT re-greet
        # with "नमस्कार सर, मी शिवांगी कला जेनसेट मधून", just dive into the
        # enquiry capture in that language.
        if preferred_lang in ("mr", "hi", "en"):
            lang_label = {"mr": "Marathi", "hi": "Hindi", "en": "English"}[preferred_lang]
            if preferred_lang == "en":
                opener_example = (
                    f"\"Hi{(' ' + name) if name else ''} {honorific}, this is Shivangi, "
                    f"an A-I assistant, just calling about your generator enquiry — "
                    f"need a couple of quick details. Is now a good time to talk?\""
                )
                script_rule = (
                    "**English** — Latin script for all ordinary words. **No Marathi/Hindi "
                    "filler words** ('namaste', 'theek hai', 'haan', 'jee' are FORBIDDEN — "
                    "say 'hello', 'okay', 'yes', 'sir/madam' instead). "
                    "**CRITICAL TTS PRONUNCIATION RULES** (the English voice mispronounces "
                    "certain words badly — work around it):"
                    "  (1) **Write every Indian place / area / society / landmark / person "
                    "name in DEVANAGARI**, even inside an English sentence — the voice says "
                    "Indian names correctly ONLY in Devanagari and mangles them in Latin. "
                    "E.g. 'You are in कोथरूड, correct?' (not 'Kothrud'); 'our team will "
                    "visit हिंजवडी' (not 'Hinjewadi'); 'मगरपट्टा' (not 'Magarpatta'). This "
                    "applies to the caller's name and locality too. Keep ALL other words in "
                    "English/Latin — do NOT switch whole sentences to Marathi/Hindi. "
                    "  (2) **NEVER say 'Kala Genset' by name** in English mode. The TTS "
                    "pronounces 'Kala' as 'yala'. The IVR already told the caller who's "
                    "calling — refer to the company as 'we', 'our team', 'our company', "
                    "or 'us' from this point on. E.g. 'our team will get back to you' "
                    "(not 'Kala Genset team will get back'). "
                    "  (3) **Prefer 'generator' over 'genset'.** Both work but 'generator' "
                    "is pronounced more cleanly. Use 'genset' only if the customer used "
                    "it first. "
                    "  (4) **Spell abbreviations with hyphens** so the TTS reads them "
                    "letter-by-letter: 'k-V-A' not 'kVA', 'A-M-F' not 'AMF', "
                    "'D-G set' not 'DG set', 'three-phase' not '3-phase', "
                    "'CPCB four plus' not 'CPCB IV+'. "
                    "  (5) **Brand names in Latin** but minimise repetition — say "
                    "'Kirloskar', 'Mahindra', 'Cummins' once when needed, then 'they' "
                    "or 'that brand'. "
                    "**Phrasing rules:** "
                    "(a) Use natural Indian English — full sentences, simple words. "
                    "(b) Numbers under 100 spelled out is fine ('twenty k-V-A'); "
                    "above 100 keep digits. "
                    "(c) Short, complete sentences. Avoid Hindi/Marathi sentence "
                    "structures like 'You are wanting k-V-A how much?' — say "
                    "'How many k-V-A do you need, sir?' instead."
                )
            elif preferred_lang == "hi":
                opener_example = (
                    f"\"नमस्ते{(' ' + name) if name else ''} {honorific}, मैं शिवांगी, "
                    f"कला जेनसेट की ए. आय. असिस्टंट. आपकी genset enquiry के बारे में "
                    f"call कर रही हूँ — थोड़ी और जानकारी चाहिए। अभी बात कर सकते हैं?\""
                )
                script_rule = "Speak in Hindi using Devanagari script throughout."
            else:  # mr
                opener_example = (
                    f"\"{('नमस्कार ' + name + ' ' + honorific + ', ') if name else ('नमस्कार ' + honorific + ', ')}"
                    f"मी शिवांगी, कला जेनसेटची ए. आय. असिस्टंट. तुमच्या genset enquiry "
                    f"बद्दल थोडी माहिती हवी होती — आत्ता बोलायला वेळ आहे का?\""
                )
                script_rule = "Speak in Marathi using Devanagari script throughout."
            # Tell the LLM what honorific to use. Two paths:
            #
            #   Outbound (we have the customer's name from ERP):
            #     We've already inferred gender → lock in the honorific
            #     for every turn so the LLM doesn't drift back to "sir".
            #
            #   Inbound (no name yet — we didn't pre-fill customer_name):
            #     Tell the LLM to default to "sir" until it captures the
            #     name, then SWITCH to "madam"/"मॅडम"/"मैडम" the instant
            #     it hears a clearly female name. Without this clause the
            #     model would otherwise stay on "sir" forever because the
            #     opening greeting addressed the caller that way.
            if preferred_lang == "en":
                male_h, female_h = "sir", "madam"
            elif preferred_lang == "hi":
                male_h, female_h = "सर", "मैडम"
            else:  # mr
                male_h, female_h = "सर", "मॅडम"

            if name:
                gender_rule = (
                    f"Address the customer as **'{honorific}'** throughout the "
                    f"entire call (every question, every ack like "
                    f"'अच्छा {honorific}'/'ओके {honorific}', the closing line). "
                )
                if is_female:
                    gender_rule += (
                        f"We inferred the customer is FEMALE from her name — "
                        f"do NOT slip back into '{male_h}' at any point. The "
                        f"closing thank-you line must also use '{honorific}'."
                    )
            else:
                gender_rule = (
                    f"**Honorific switching (CRITICAL):** Default to "
                    f"**'{male_h}'** for the first turn or two. The instant "
                    f"the customer says their name, decide if it's female "
                    f"(Priya, Sunita, Pooja, Lakshmi, Anjali, Neha, Vidya, "
                    f"Rekha, Shivangi, Anita, Kavita, Sangita, Sneha, "
                    f"Madhuri, Jyoti, Shilpa, Shweta, Smita, Sonali, etc.) "
                    f"— if YES, immediately switch to **'{female_h}'** for "
                    f"every subsequent question, every ack ('अच्छा {female_h}', "
                    f"'ओके {female_h}'), and the closing line. Do NOT keep "
                    f"saying '{male_h}' to a female caller — it's the most "
                    f"common complaint we hear from women. If the name is "
                    f"male or you genuinely can't tell, keep using '{male_h}'."
                )
            brief_lines.append(
                f"- Customer pre-selected **{lang_label}** via IVR. Speak ONLY "
                f"in {lang_label} for the entire call. {script_rule} "
                f"{gender_rule}"
                f"They've already heard our intro that this call is from Kala Genset "
                f"— do NOT repeat a full self-introduction. Open directly with a short "
                f"personalised line, e.g. {opener_example}, then start capturing fields."
            )
        if caller_phone:
            brief_lines.append(
                f"- Customer's current phone: {caller_phone}. "
                f"If they say 'same number' / 'इसी नंबर पर' / 'हाच नंबर' / "
                f"'इथेच कॉल करा' or similar when asked about callback_number, "
                f"silently use {caller_phone} — do NOT ask them to spell out digits."
            )
        if is_outbound and name and not preferred_lang:
            # Fallback greeting only when language wasn't pre-selected (legacy
            # path; new outbound flow always sets preferred_language).
            legacy_hon = "मॅडम" if is_female else "सर"
            brief_lines.append(
                f"Greet them by name and mention you are calling about their genset "
                f"enquiry, e.g.: \"नमस्कार {{name}} {legacy_hon}, मी शिवांगी कला जेनसेट मधून — "
                f"तुम्ही नुकतीच generator बद्दल enquiry केली होती, त्याबद्दल थोडी "
                f"अधिक माहिती हवी होती. आत्ता बोलायला वेळ आहे का?\". Address her/him "
                f"as '{legacy_hon}' throughout the call. Treat the name as already "
                f"captured for save_lead — do not ask for it again."
            )
        _messages.append({"role": "system", "content": "\n".join(brief_lines)})

    context = LLMContext(
        messages=_messages,
        tools=TOOLS_SCHEMA,
    )
    aggregator = LLMContextAggregatorPair(context)

    # ── Silence watchdog: DISABLED ─────────────────────────────────────────
    # Was an IdleFrameProcessor that nudged the user after 12-20s of silence
    # ("क्षमा करा सर, ऐकू आलं नाही — परत सांगाल का?"). Problem: every time it
    # fired and pushed a TTSSpeakFrame, Sarvam TTS's WebSocket crashed with
    # "no close frame received or sent" and the call dropped — see logs from
    # 2026-05-11. Hypothesis: Sarvam TTS's WS has a ~15-20s server-side idle
    # timeout. During long user silences the WS goes half-dead silently.
    # Sending a fresh TTSSpeakFrame exposes the dead socket and cascades the
    # whole call. Without the watchdog, the dead WS only surfaces when Claude
    # next responds, by which time the user has usually spoken again.
    # If we re-enable this, we need a Sarvam TTS keep-alive heartbeat first.
    # Re-enable by restoring the IdleFrameProcessor + adding it back to the
    # Pipeline list below.

    # Filler / speculative-acknowledgment DISABLED.
    # We tried emitting a short "हम्म..." / "बरं..." / "Okay sir..." within
    # 200-300 ms of the user stopping, to mask LLM TTFT — but in practice
    # the fillers sounded mechanical and broke the natural flow of the call.
    # The FillerAcknowledgmentProcessor class is left defined above but is
    # NOT in the pipeline. To re-enable, instantiate it (e.g., `filler =
    # FillerAcknowledgmentProcessor(language=_pref or "mr")`) and insert it
    # between `llm` and `tts` in the Pipeline list below.

    pipeline = Pipeline([
        transport.input(),       # raw audio in (mic / phone)
        stt,                      # Sarvam STT → text frame
        aggregator.user(),        # adds user msg to context
        llm,                      # LLM thinks → text frames + tool calls
        tts,                      # Sarvam TTS → audio frame
        transport.output(),       # audio out (speaker / phone)
        aggregator.assistant(),   # adds assistant msg to context
    ])

    return pipeline, context


async def bot(runner_args):
    """Pipecat runner entrypoint. Used by `python -m pipecat.runner.run agent.bot:bot --transport webrtc`."""
    from pipecat.runner.utils import create_transport
    from pipecat.transports.base_transport import TransportParams

    # SileroVADAnalyzer = proper voice-activity detection so the agent reacts immediately
    # when the user stops talking instead of waiting on a silence timeout.
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams

    # Plivo Media Streams send 8 kHz mu-law audio. The runner uses FastAPIWebsocket transport
    # under the hood for telephony; it expects FastAPIWebsocketParams (not base TransportParams)
    # because it needs the `add_wav_header` field.
    transport = await create_transport(
        runner_args,
        {
            "webrtc": lambda: TransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                vad_analyzer=SileroVADAnalyzer(),
            ),
            "plivo": lambda: FastAPIWebsocketParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                audio_in_sample_rate=8000,
                audio_out_sample_rate=8000,
                vad_analyzer=SileroVADAnalyzer(),
            ),
        },
    )

    pipeline, _context = build_pipeline(transport)
    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_t, _client):
        logger.info("client connected — kicking off greeting")
        # Trigger the LLM to speak first (greeting from the system prompt)
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_t, _client):
        logger.info("client disconnected")
        await task.queue_frames([EndFrame()])

    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)
    await runner.run(task)


if __name__ == "__main__":
    from pipecat.runner.run import main
    main()
