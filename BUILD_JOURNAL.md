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
- **Upgrade path noted :** OpenRouter serves STT/TTS models via API — one vendor for all three cascade stages, server-side, more voice control. Drop-in swap because every stage boundary is just text.

### Barge-in state reconciliation (interruption handling done right)

**The bug (caught during testing):** replies are recorded to the call log at tool-execution time — before TTS speaks. On barge-in, the UI showed the full reply text and the agent's context contained words the caller never heard (e.g. cut off mid "your appointment is Tuesday at—" → agent believes the caller knows the time).

**The fix — sync display and state to what was actually spoken:**
- `call/page.tsx` — reply bubbles now reveal word-by-word driven by TTS `onboundary` events (`charIndex`): the screen never shows unspoken words. On barge-in: cancel TTS, commit only the heard prefix (`"...Tuesday at —"`) to the transcript, POST the prefix to `/voice/interrupted`. Same truncation on hang-up mid-speech (no server note — call is over).
- `routes/voice.py` — `POST /voice/interrupted` (NEW) → `call_session.record_interruption(heard)`.
- `voice_call.py` — `record_interruption()`: appends an `<interruption>` marker quoting exactly what was heard. Full intended reply stays in the log (honest record); the marker annotates it.
- `voice_addendum.md` — teaches the model: a reply followed by `<interruption>` was only heard up to the quote; re-confirm anything important that fell after the cutoff.
- **Design choice:** annotate, don't rewrite. The log keeps what the agent intended AND what the caller heard — auditable, and the model re-confirms naturally.

### Fixes from noisy-room stress test (2026-07-06 ~12:50)

- **Hang-up race:** a turn in flight at hang-up finished AFTER `/voice/end` cleared the session — its reply recreated `active_call.log` as a ghost transcript polluting the next call. Fix: `hangUp` waits (≤8s) for in-flight turns before ending.
- **Repeated transfers:** agent called `transfer_to_human` on each successive garbled turn (3x). Fix: prompt rule — at most once per call, then reassure.
- Positive findings from the same test: 7 barge-ins reconciled (incl. `heard 0 chars` edge), and the escalation ladder fired correctly on sustained garbled input — agent transferred to a human with a reasoned explanation rather than looping forever.

### Google Calendar integration (real availability + booking)

- `server/services/calendar_client.py` (NEW) — Composio calendar: `initiate_calendar_connect()` (link flow, same as the Gmail fix), `calendar_status()`, `execute_calendar_tool()`. Reuses the Gmail Composio client singleton. Connection state persisted to `server/data/calendar_connection.json` (survives --reload, unlike Gmail's in-memory user id).
- `server/routes/calendar.py` (NEW) — `POST /calendar/connect`, `GET /calendar/status`.
- `config.py` — `COMPOSIO_GOOGLE_CALENDAR_AUTH_CONFIG_ID` setting.
- `scheduling.py` — the interface swap, exactly as designed (decision #4):
  - `check_availability`: generates candidate 30-min slots within clinic hours (9:00–16:30) for the urgency window, queries `GOOGLECALENDAR_FIND_FREE_SLOTS` (free/busy), filters overlaps, offers ~4 spread across the window. **Falls back to mock on any failure or when not connected** — the demo can never be killed by OAuth/network.
  - `book_appointment`: calendar-backed slots carry `start_iso`; booking creates a real `GOOGLECALENDAR_CREATE_EVENT` (30 min, confirmation number + callback in description). If event creation fails, booking still succeeds locally with a note telling the agent to say staff will confirm.
  - Tool slugs + argument schemas verified against the live Composio API before writing (e.g. `event_duration_minutes` must be ≤59; durations of 1h+ use `event_duration_hour`).
- Timezone from the existing `timezone_store` (default America/Toronto).
- The agent/tool layer needed ZERO changes — the swap happened entirely behind the service interface, as designed on day one.

### Parking lot additions
- Better TTS voice: browser TTS sounds robotic — swap to OpenRouter/ElevenLabs TTS server-side; also consider better STT. Cascade boundaries are text, so this is frontend-only or a new `/voice/tts` endpoint.

### Preference fix — "not offered" ≠ "unavailable"

**Bug found by using the product:** caller asked "Tuesday at 4pm"; calendar had 0 busy ranges (4pm was free), but the tool's 4-slot spread sample didn't include it → agent truthfully reported its tool result but falsely told the caller the time was unavailable. Artificial scarcity presented as fact.

**Fix (scheduling.py, deterministic + explainable):**
- `_filter_by_preference()` — parses day names, morning/afternoon, "after N", "N am/pm" from the preference string; filters FREE slots to match
- Preference matches → those slots offered (`note: matches caller's request`)
- Preference understood but no free match → alternatives + explicit `note: genuinely unavailable` so the agent's phrasing stays truthful
- Tool description updated: pass the caller's day/time request verbatim
- Unit-tested: 'Tuesday at 4pm' / 'tuesday morning' / 'after 3pm' / 'wednesday' / 'whenever' all behave correctly

**Q&A recorded (architecture clarification):** the agent is a hybrid — direct synchronous tools for on-call scheduling work (caller is waiting), OpenPoke-style async delegation to execution agents still available for background work (e.g. confirmation emails). Deliberate: "sync where a human waits, async where they don't."

### Clinic calendar + patient invites

- `CLINIC_CALENDAR_ID` (.env) — availability queries and event creation now target the dedicated Maple Clinic calendar (source of truth for the clinic's schedule), falling back to `primary`.
- `book_appointment` gained `patient_email`: the event is created ON the clinic calendar with the patient as attendee (`send_updates: all`) — Google delivers an invite to the patient's calendar with RSVP. **Write once, invite — no two-calendar sync logic.**
- Mock patient records now carry `email_on_file` (Mehdi's record uses his real email for the invite demo).

### THE AGENT OVERLOAD FIX (implemented, not just discussed)

The PRE-WORK topic, built with real embeddings:

- `services/execution/roster.py` — records now carry name, description (seeded from first delegation instructions), created_at, last_active, cached embedding. Legacy bare-string rosters upgrade in place on load. `touch()` = recency signal on every reuse.
- `services/embeddings.py` (NEW) — OpenAI `text-embedding-3-small` at **256 dims** (small cached vectors; brute-force cosine in pure Python is microseconds at roster scale — a vector DB would be slower than the network call to reach it).
- `services/execution/selection.py` (NEW) — the layered selection: ① semantic top-6 (query embedding vs cached agent embeddings) ② ∪ recently-active (24h — covers semantic misses like "actually, cancel that") ③ cap 10 ④ lazy embedding backfill. **Any failure → full roster injection (correctness over efficiency).**
- `agent.py::_render_active_agents(latest_text, channel)` — the overload site itself now selects. Rosters ≤10 inject whole (selection is pointless below threshold). Injects an HTML comment telling the model the list is filtered.
- **Escape hatch:** `search_agents(query)` tool — full-roster semantic search when the expected agent isn't in the injected list. Every call is logged as a top-k miss signal = free retrieval eval. System prompt teaches: search before creating a new agent.
- **Channel-aware latency:** the query-embedding call costs ~400-700ms. Text pays it (semantic selection); **voice uses recency-only selection (3ms, zero network)** — execution agents are rarely delegated mid-call, and dead air is worse than a slightly staler list.
- `seed_roster.py` — seeds ~35 fake agents for the demo.
- **Measured:** 36-agent roster → 6 injected; text 426ms (semantic), voice 3ms (recency).
- **Live demo of the designed failure mode:** query "did alice reply about the invoice?" ranked 6 other invoice agents above "Weekly report to Alice" (rank 7, outside top-6) — near-topic agents dominate name matches. Exactly why the escape hatch + description-based embeddings exist.
- ⚠ Demo note: `DELETE /chat/history` clears the roster — re-run `seed_roster.py` after a reset if demoing the overload fix.

### Gmail disconnect-on-reload fix (found via overload-fix testing)

- **Bug:** Gmail's connected user id lived in memory only (`_ACTIVE_USER_ID`) — every `--reload` (dozens today) silently disconnected Gmail. Same class of bug we pre-empted in `calendar_client.py` by persisting connection state.
- **Fix:** persist to `server/data/gmail_user.json` on set; fall back to the file on read after a reload. One-time reconnect required to seed the file.
- **The gold in the same logs:** the overload-fix loop ran end-to-end UNPROMPTED — selection injected 6 invoice agents (Alice ranked 7th, missed) → model called `search_agents` (miss signal logged) → found and REUSED the existing Alice agent instead of creating a duplicate → delegated the email search. Design validated by the model's own behavior 3 minutes after being built.

### Server-side TTS: built, measured, REJECTED (kept as optional backend)

Explored replacing the robotic browser voice with OpenRouter's `/audio/speech` endpoint (mirrors OpenAI's API):
- Discovered available models by querying the models API (`output_modalities=speech`) after the docs' example slug 404'd; probed provider-specific voice conventions (Azure needs full neural voice names like `en-US-Ava:DragonHDLatestNeural`; Gemini TTS only outputs PCM; `voice` is required)
- Measured: `kokoro-82m`/`af_heart` 0.4–0.8s per sentence (cheapest), `mai-voice-2` DragonHD ~1.2s (premium)
- Implemented the production pattern: sentence-chunk queueing with pipelined synthesis (Pipecat/LiveKit style) + sentence-precise heard-tracking for barge-in
- **Verdict after live testing: worse call UX than browser TTS.** Gaps between sentence chunks + slower time-to-first-audio beat the quality gain. Instant-robotic > natural-laggy — the latency-is-the-product thesis validated against our own feature.
- Kept: `POST /voice/tts` endpoint + `OPENPOKE_TTS_MODEL`/`OPENPOKE_TTS_VOICE` config (working, tested) for a future streaming implementation; frontend defaults to browser TTS with exact word-level `onboundary` tracking.
- Production path (README material): true streaming TTS over websocket (ElevenLabs Flash / Deepgram Aura) with character-level timestamps — removes both the gaps and the tracking estimate. The heard-chars interface we built is provider-agnostic either way.

### Voice output contract (robustness at any context length)

**Incident:** after ~100+ messages of accumulated test history, the agent began narrating internal plans ("Starting intake - need to understand reason for visit...") instead of replying. Root-cause candidates: long/summarized history steering the model's register (A/B: clearing history restored behavior — n=1, mechanism unverified; evidence lost to a failed backup, owned in review). **Mehdi's design challenge: the system must work with 100s of messages — clearing history is a workaround, not a fix.**

**Fix — enforce the contract in code, independent of context:**
- The leak path was `_finalize_response` falling back to raw assistant text when no `send_message_to_user` was called.
- Voice turns now guarantee: reply tool → else ONE corrective iteration (`<system_correction>` nudge) → else scripted safe fallback line. Raw model text can never be spoken to a caller.
- Verified by monkeypatching the LLM to (a) never reply — safe fallback used, no leak; (b) recover on the corrective pass — proper reply surfaced.
- Remaining (with-more-time): inspect the summarizer's output register; long-context eval fixture (200-message synthetic history + golden scenario assertions).

### Resilience pass + the "Booking Check" incident

- **Transient LLM timeout** (60s) killed a chat turn silently. Fixes: one retry on timeout/429/5xx in the OpenRouter client; chat failures now post a visible "something went wrong, mind sending that again?" instead of silence. (The suspected "system badly broken" moment — diagnosed as upstream weather, revert avoided; same turn succeeded on retry.)
- **The Booking Check incident:** "what bookings do I have?" → no bookings tool existed → agent improvised: delegated to an execution agent → which only has EMAIL tools → crawled the user's Gmail for appointment info (privacy smell: patient email is not clinic data) → 90s batch timeout → FAILED → safety rule correctly escalated to human. Every step locally reasonable; the chain started from a missing capability.
- Fixes: `lookup_appointments(patient_name)` tool reading the clinic's booking records (single source of truth); prompt rules — never search email or delegate for booking records; tool failures are momentary, re-attempt before claiming something is broken.
- Lesson for the walkthrough: agents fail by IMPROVISING around capability gaps — the fix is completing the tool surface for the real user journeys (book → recall → [cancel/reschedule: future]), not punishing the improvisation.

### The voice architecture saga (evening of day 1) — three implementations, one lesson

**v1 — hand-rolled browser loop** (Chrome SpeechRecognition + speechSynthesis + /voice/send): worked, but four field problems: self-interruption on speakers (mic hears TTS, no AEC), register bleed (voice agent narrating text-style — history is text-register), robotic voice, dead air (~1s Chrome endpointing + 2–6s full agent turn before any audio).

**Framework research:** Pipecat (OSS, P2P WebRTC, VAD, streaming) vs LiveKit (same class, needs media server) vs Vapi/Retell (hosted, bring-your-own-LLM). Key analysis: the dominant latency was OUR full-turn agent (2–6s), which no framework removes — frameworks fix echo (WebRTC AEC), endpointing (Silero), and streaming plumbing.

**v2 — voice-native streaming agent on Pipecat (feature/pipecat-voice) — THE FIX.** Mehdi's architectural insight: a call is a SESSION; it doesn't need the persistent runtime in the loop. Per-call agent with injected context (voice persona + intake/safety rules + memory tail), streaming end-to-end: OpenAI realtime STT → streaming LLM via OpenRouter → streaming TTS. Same clinic stack reused in-process (intake tool schemas as pipecat function handlers, red-flag guard on transcripts, recap posted to main chat on disconnect). Result in field test: "voice felt human, delay much smaller" — all four v1 problems resolved.

- Media worker (:7860) is a deliberately SEPARATE process: media plane (10ms audio frames, VAD inference, live websockets) vs control plane (request/response app on :8001, --reload restarts would kill live calls). Standard voice-platform architecture.
- Custom in-app call UI (/call-v2) on @pipecat-ai/client-js: own transcript bubbles; bot text renders word-by-word AS SPOKEN (onBotTtsText) — the "never show unspoken words" property by construction; barge-in freezes the bubble at the cutoff. Signaling proxied via Next rewrite (same-origin). Classic v1 retained at /call.

**Discipline notes for the walkthrough:** browser-TTS-upgrade (OpenRouter kokoro) was built, measured, and REVERTED on UX evidence; the framework switch only happened after latency analysis showed which problems it would and wouldn't fix; v1 remains demoable as the from-scratch implementation.

### v3 experiment — OpenAI Realtime speech-to-speech (branch: feature/openai-realtime-voice)

Motivation: even lower latency + natural prosody (model hears audio directly). Known tradeoff going in (from prep + relevant to a medical/insurance context): NO inspectable text layer between stages — weaker audit/compliance story than the cascade; tools work but via the provider's protocol. Plan: pipecat's OpenAI Realtime service in the same pipeline slot, same tool schemas, same recap-from-context on disconnect. Timeboxed experiment — cascade v2 remains the primary demo.

### v3 results — OpenAI Realtime s2s (field-tested ~1:30am)

- **Verdict: "by far the best working solution"** — lowest latency, most natural prosody. Tools, greeting, recap all worked through the same reused stack (~120 new lines; everything else imported from v2).
- **Bug found & fixed:** Realtime API does NOT transcribe caller audio by default — user bubbles empty, and (critically) the red-flag guard was blind and the recap lost the caller side. Fix: enable `InputAudioTranscription` explicitly (upgraded to `gpt-4o-transcribe`).
- **THE DISCOVERY:** even enabled, the caller transcript is a SIDECAR — a separate transcription model on the same audio; the realtime model consumes raw audio and never sees that text. Mehdi observed transcript ≠ what the model clearly understood. **In the cascade, the transcript IS the model input (single source of truth); in s2s the audit log is an approximation of what the model processed.** The compliance tradeoff we documented from theory, demonstrated empirically in our own product.
- **Orb UI (design honesty):** v3's /call-v2 replaces chat bubbles with a ChatGPT-voice-style orb (breathing = listening, morphing blob = speaking) + the agent's words rolling beneath as spoken. No user text on screen — because in s2s it isn't the truth. Sidecar transcription stays enabled in the pipeline for the guard + recap. Each branch's UI now tells the truth about its architecture: bubbles where transcript = source of truth (v2), orb where it isn't (v3).

**Final state: three voice architectures on three branches, one shared brain-stack, one web UI.**
| | v1 hand-rolled | v2 streaming cascade | v3 realtime s2s |
|---|---|---|---|
| Branch | feature/voice-intake-agent | feature/pipecat-voice | feature/openai-realtime-voice |
| Latency | worst (full-turn wait) | good (streaming overlap) | best |
| Voice quality | robotic | natural (OpenAI TTS) | most natural (prosody-aware) |
| Speaker echo | vulnerable (heuristics) | solved (WebRTC AEC) | solved (WebRTC AEC) |
| Audit/text layer | full (transcript = input) | full (transcript = input) | sidecar approximation |
| Cost/min | lowest | low | ~10x cascade |
| Production pick for a clinic | teaching artifact | **RECOMMENDED** | frontier option, compliance caveat |

### Async follow-up for failed/slow call tasks (the closing loop)

- Tool calls in the voice workers are now bounded (20s) and failure-aware: on timeout/crash the model is instructed to promise a text follow-up (no dead air, no on-call retry loops), and the item is recorded on a per-call outstanding list.
- On hang-up, outstanding items POST to `/voice/followup`, which delivers them to the interaction agent as a **plain agent message via handle_agent_message — the normal pipeline, no scaffolding** (Mehdi's correction: don't prescribe tool usage; the agent's standard routing/delegation decides). It completes the work and messages the user the outcome in chat.
- This completes the architecture's thesis: sync where a human waits, async where they don't — INCLUDING the recovery path. Voice session → persistent agent handoff is the same direction as call recaps, now carrying work, not just memory.
- `SIMULATE_TOOL_FAILURE=<tool>` env var = live failure-injection lever for demos/testing.
- **defer_task (capability-gap escape valve, from field testing):** caller asked the voice agent to send an email → it refused and offered a human. Fix: a 7th tool — anything outside the call tools gets queued ("I can take care of that after the call — you'll get a text") onto the same outstanding list. On hang-up the interaction agent's NORMAL pipeline handles it (e.g. delegates the email to an execution agent → real Gmail send → outcome in chat). Transfers now reserved for safety cases only. Full relay: voice session → persistent agent → execution agent → Gmail.
- Also this morning: sidecar transcription switched to `gpt-realtime-whisper` (the natively-streaming model intended for realtime sessions); README gained the OpenAI Agents SDK migration path — notably OpenAI's own docs recommend the chained pipeline for "approval-heavy flows / durable transcripts," independently confirming the v2-for-clinics call.

### Safety audit (day 2, prompted by Mehdi: "if I asked the agent to do something bad?")

**Layers in place:** capability confinement (7 tools, model narrates / tools decide, everything audit-logged) · deterministic emergency guard · scope rules (no diagnosis, sensitive→human) · output contract · draft-confirmation human-in-the-loop for outbound email · inherited model refusals.

**Gaps found & fixed:**
- `lookup_appointments` disclosed appointment times + reasons (PHI) on a NAME ALONE. Now identity-gated: DOB must match the patient record (normalized to YYYY-MM-DD by the model; deny-by-default, denials logged). Tested: no-DOB/wrong-DOB denied, match discloses.
- `defer_task` bridged an unauthenticated caller to the interaction agent's full capabilities (incl. Gmail). The handoff message now flags items as weakly-verified caller requests: apply normal judgment + draft-confirmation, decline anything inappropriate/suspicious/out-of-scope.

**Known residual risks (honest list for the walkthrough):** name+DOB is weak verification (same as most real clinics' phone flows — production wants callback-number matching or patient-portal auth) · voice prompt injection (caller speaking instructions) is mitigated by capability confinement + deterministic guards, not eliminated · PHI in plaintext flat files is demo-only · no rate limiting / abuse-pattern detection on bookings.

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
