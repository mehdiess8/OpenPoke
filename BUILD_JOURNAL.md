# Build Journal — Voice Intake Agent (General Magic Build Trial)

**Task:** Extend OpenPoke into a natural voice agent for medical office intake, urgency routing, scheduling, tool calls, and human handoff.
**Branch:** `feature/voice-intake-agent`
**Author:** Mehdi Essoussi — July 6–7, 2026

This doc tracks every change, decision, and tradeoff as the build progresses. Newest entries at the bottom of each section.

---

## Architecture (one paragraph)

One assistant, two channels. The existing OpenPoke interaction agent is extended — not forked — with clinic intake capabilities. Text chat (`/chat/send`) and voice calls (`/voice/send`) hit the same brain with the same long-term memory. Channel-aware prompting appends a scheduling addendum for all turns and a voice-style addendum only for call turns. Voice turns are isolated in a call-session log; when a call ends, a summary is posted into the main chat so the user keeps a clean record without the full transcript.

```
 Chat UI ──► /chat/send ──► InteractionAgentRuntime(channel="text") ──┐
                                                                      ├──► same prompt base + scheduling addendum
 Call UI ──► /voice/send ─► InteractionAgentRuntime(channel="voice") ─┘         └ + voice addendum (voice only)
                │                                    │
                │ red-flag guard (deterministic)     ├─ tools: send_message_to_user / wait / send_message_to_agent (existing)
                │ injects <emergency_screen_alert>   └─ intake tools: lookup_patient · check_availability ·
                │                                              book_appointment · escalate_emergency · transfer_to_human
                ▼
      /voice/end ──► LLM summary of call ──► posted to main chat log
```

## Key decisions log

| # | Decision | Alternatives considered | Why |
|---|----------|------------------------|-----|
| 1 | **Extend the existing interaction agent** (channel param + prompt addendums) instead of a sibling voice agent | Separate `voice_agent/` package; parameterized runtime profiles | Product vision: same assistant reachable by text or call, shared memory. Sibling would duplicate the loop and split memory |
| 2 | **`/voice/send` awaits the reply** (sync) while `/chat/send` stays fire-and-forget (202 + polling) | Reuse chat's async pattern | A caller is waiting on the line — the reply IS the response. Text users tolerate async; callers don't |
| 3 | **Deterministic red-flag guard in code** + LLM handles the conversation | Pure prompt-based triage; pure keyword auto-response | Brief demands "explainable and conservative." Code flags (auditable list of patterns), model converses (no false-positive 911 scripts for "my dad had chest pain last year"). Defense in depth |
| 4 | **Mock scheduling behind a service interface** (`services/scheduling.py`) | Wire Google Calendar first | Demo never blocks on OAuth; Composio GCal drops in behind the same functions later |
| 5 | **Intake tools attached directly to the interaction agent** (synchronous) | Delegate to execution agents (OpenPoke's async batch path) | On a live call, latency is the product. Fire-and-forget + batch return is built for background email work, not for a caller waiting to hear available slots. Also: 5 focused tools = deliberate answer to tool/agent overload — don't expose what the task doesn't need |
| 6 | **Call transcript isolated from main chat; post-call summary posted instead** | Voice turns recorded straight into main conversation log (v1) | UX: full call logs pollute the chat. Summary keeps "same person, shared memory" while keeping chat readable |
| 7 | **Browser Web Speech API for STT/TTS** | Deepgram/ElevenLabs streaming stack | Zero keys, zero latency-budget engineering, demos today, barge-in implementable via speech-start → cancel TTS. Production would swap to streaming STT/TTS vendors |

## Change log (files)

### Fixes to the existing repo
- `server/services/gmail/client.py:220` — `connected_accounts.initiate(...)` → `.link(...)`. Composio retired the legacy endpoint (`ComposioLegacyConnectedAccountsEndpointRetiredError`); the installed SDK (0.17.1) already ships the replacement with the same return shape.

### New files
- `server/services/scheduling.py` — mock clinic backend: patient records, slot generation by urgency (`same_day`/`soon`/`routine`), bookings persisted to `server/data/clinic_bookings.json`, escalations/transfers audit-logged to `server/data/clinic_escalations.json`. Real calendar drops in behind these functions.
- `server/agents/interaction_agent/intake_tools.py` — 5 tool schemas + dispatcher, isolated so core `tools.py` changes stay additive (2 lines). Plain-dict returns to avoid circular import with `ToolResult`.
- `server/agents/interaction_agent/scheduling_addendum.md` — intake flow, priority rubric (explainable, conservative, escalate-up-when-unsure), safety rules, handoff rules. Appended to the system prompt on BOTH channels (text can schedule too).
- `server/agents/interaction_agent/voice_addendum.md` — TTS style constraints (one question at a time, no markdown, spoken times, confirm critical details, transcription-error tolerance). Appended ONLY for voice turns.
- `server/routes/voice.py` — `POST /voice/send` (awaits reply, red-flag guard), registered in `routes/__init__.py`.

### Modified files (all additive, defaults preserve old behavior)
- `agents/interaction_agent/tools.py` — +2 edits: register intake schemas; route unknown tools through `handle_intake_tool` before erroring.
- `agents/interaction_agent/agent.py` — `build_system_prompt(channel)`; `prepare_message_with_history(..., channel, emergency_alert)`; current-turn tag gains `channel="voice"` attribute; `<emergency_screen_alert>` section injection.
- `agents/interaction_agent/runtime.py` — `execute(user_message, channel="text", emergency_alert=None)` passthrough.

### Call-session isolation + post-call recap (v2 memory model)

- `server/services/voice_call.py` (NEW) — `CallSessionLog`: file-backed transcript of the active call (`server/data/active_call.log`), singleton, single active call (demo scope; production keys sessions by caller).
- `runtime.py` — voice turns record to the call session, not the main log; context = long-term memory + `<active_call>` transcript. Channel threaded to tools via `_execute_tool`.
- `tools.py` — `send_message_to_user(message, channel)`: voice replies land in the call session.
- `routes/voice.py` — `POST /voice/end` (NEW): summarizes the call transcript with `summarizer_model`, posts one "Call recap: ..." message into the main chat, clears the session. Fallback recap on summarizer failure — never lose the record.
- `voice_addendum.md` — explains `<active_call>` vs `<conversation_history>` semantics to the model.
- **Decision #6 implemented.** Chat UI stays clean; user gets one recap text after hanging up; the agent still recognizes returning callers via long-term memory.

### Voice UI (browser Web Speech API)

- `web/app/api/voice/route.ts` + `web/app/api/voice/end/route.ts` (NEW) — Next.js proxy routes to the Python voice endpoints (same pattern as the existing chat proxy: CORS-free, `PY_SERVER_URL` stays server-side).
- `web/app/call/page.tsx` (NEW) — the call UI. Turn-taking state machine: `listening → thinking → speaking → listening`. Chrome `SpeechRecognition` (STT, `isFinal` results = endpointing delegated to the browser) + `speechSynthesis` (TTS). **Barge-in:** mic stays open while speaking; interim speech during TTS → `speechSynthesis.cancel()`. Chrome auto-stops recognition after silence → `onend` restarts it while the call is live. Live transcript with bubbles, interim text shown italic, recap banner after hang-up.
- `web/components/chat/ChatHeader.tsx` — +📞 Call link.
- Known limits (say in demo): Chrome-only; silence-based endpointing (not semantic, not tunable); robotic TTS voices; echo can self-trigger barge-in without headphones.
- **Upgrade path noted (Mehdi):** OpenRouter serves STT/TTS models via API — one vendor for all three cascade stages, server-side, more voice control. Drop-in swap because every stage boundary is just text.

## Test log

- **2026-07-06** — three curl scenarios against `/voice/send`:
  - Routine booking intent → natural reply, asked for name + DOB (one step) ✅
  - Identity turn → `lookup_patient` found mock record, moved to focused intake ✅
  - Mid-call emergency ("dad chest pain, trouble breathing") → `emergency_flagged: true`, clean 911 script, booking stopped ✅

## Ideas / later (parking lot)

- Swap mock scheduling for Composio Google Calendar (needs Calendar auth-config ID from team)
- Post-booking confirmation email via existing execution-agent + Gmail path (real email lands in inbox — good demo beat)
- Latency: measure /voice/send p50; sentence-level TTS streaming; smaller/faster model for voice turns
- Evals: golden transcripts for the 3 scenarios; red-flag guard unit tests (false-positive list: "chest pain last year", "my friend said...")
- Privacy: PHI in flat files is demo-only; production needs encrypted storage + retention policy + consent line at call start
- Semantic agent-roster retrieval if execution agents proliferate (the overload fix — designed, see interview prep)

## What I'd improve with more time

(collect during the build — feeds the final README/walkthrough)

- Real streaming voice stack (Deepgram STT + ElevenLabs TTS) with semantic endpointing
- Call sessions keyed by caller ID for concurrent calls (current: single active call session)
- Booking conflict handling against a real calendar; timezone handling
- Trajectory evals + red-flag regression suite before any prompt change
