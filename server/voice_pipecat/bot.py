"""Voice-native Maple: fully streaming pipeline with a per-call agent.

Architecture (Mehdi's insight): a phone call is a SESSION — it doesn't need the
persistent OpenPoke runtime in the loop. Instead:

  call start   → inject context (persona + intake rules + recent memory tail)
  during call  → streaming STT (OpenAI realtime) → streaming LLM via OpenRouter
                 (tokens flow into TTS sentence-by-sentence — no full-reply wait)
                 → streaming TTS. Same intake tools, called in-process.
                 Deterministic red-flag guard on every transcript.
  call end     → summarize the call and post ONE recap into the main chat
                 (same memory model as the hand-rolled loop).

Latency win over v1: the LLM leg streams — first sentence reaches TTS while the
model is still writing, instead of waiting for the full agent turn.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import TranscriptionFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.stt import OpenAIRealtimeSTTService
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

# In-process reuse of the existing clinic stack — same tools, same safety.
from server.agents.interaction_agent.intake_tools import INTAKE_TOOL_SCHEMAS, handle_intake_tool
from server.config import get_settings
from server.routes.voice import _SUMMARY_SYSTEM_PROMPT, _detect_red_flags
from server.openrouter_client import request_chat_completion
from server.services.conversation import get_conversation_log

load_dotenv(override=True)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "agents" / "interaction_agent"

GREETING = "Thanks for calling Maple Family Clinic, this is Maple. How can I help you today?"

_PERSONA = """You are Maple, the warm, efficient voice receptionist for Maple Family Clinic, speaking with a caller ON THE PHONE right now. Everything you say is spoken aloud by text-to-speech.

Speak in short natural sentences. One question at a time. No lists, no markdown, no symbols. Say times naturally ("tomorrow at two forty in the afternoon"). Acknowledge answers briefly and vary your phrasing. Never mention tools, systems, or anything technical. Never narrate what you are doing internally — instead of "fetching your data", say "one moment while I pull that up".

Example exchanges (match this register exactly):
Caller: I need to see a doctor about my back.
You: Sorry to hear that — how long has your back been bothering you?
Caller: Can I come in tomorrow morning?
You: Let me take a look. One moment.
You: I have nine twenty or ten forty tomorrow morning. Which works better?
"""


def _build_system_prompt() -> str:
    """Persona + intake/safety rules + a tail of long-term memory."""
    scheduling_rules = (_PROMPTS_DIR / "scheduling_addendum.md").read_text(encoding="utf-8")

    memory_tail = ""
    try:
        transcript = get_conversation_log().load_transcript().strip()
        if transcript:
            memory_tail = (
                "\n\n# BACKGROUND MEMORY (recent conversation with this user across text "
                "and past calls — context only, do NOT imitate its style)\n"
                + transcript[-2000:]
            )
    except Exception as exc:
        logger.warning(f"[voice-native] memory tail unavailable: {exc}")

    return f"{_PERSONA}\n\n{scheduling_rules}{memory_tail}"


def _make_tools() -> ToolsSchema:
    """Reuse the existing intake tool schemas; handlers call the clinic services in-process."""

    def make_handler(tool_name: str):
        async def handler(params: FunctionCallParams):
            logger.info(f"[voice-native] tool: {tool_name}({params.arguments})")
            try:
                result = handle_intake_tool(tool_name, params.arguments or {})
            except Exception as exc:
                result = {"error": str(exc)}
            await params.result_callback(result)

        return handler

    functions = []
    for schema in INTAKE_TOOL_SCHEMAS:
        fn = schema["function"]
        functions.append(
            FunctionSchema(
                name=fn["name"],
                description=fn["description"],
                properties=fn["parameters"]["properties"],
                required=fn["parameters"].get("required", []),
                handler=make_handler(fn["name"]),
            )
        )
    return ToolsSchema(standard_tools=functions)


class RedFlagGuard(FrameProcessor):
    """Deterministic emergency screen on every final transcript (code flags, model converses)."""

    def __init__(self, context: LLMContext) -> None:
        super().__init__()
        self._context = context

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            alert = _detect_red_flags(frame.text)
            if alert:
                self._context.add_message(
                    {"role": "system", "content": f"EMERGENCY SCREEN ALERT: {alert}"}
                )
        await self.push_frame(frame, direction)


async def _post_call_recap(context: LLMContext) -> None:
    """Summarize the call from the pipecat context and post one recap to the main chat."""
    turns = []
    for message in context.messages:
        role = getattr(message, "role", None) or (
            message.get("role") if isinstance(message, dict) else None
        )
        content = getattr(message, "content", None) or (
            message.get("content") if isinstance(message, dict) else ""
        )
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            speaker = "Caller" if role == "user" else "Maple"
            turns.append(f"{speaker}: {content.strip()}")

    if not turns:
        return

    settings = get_settings()
    try:
        response = await request_chat_completion(
            model=settings.summarizer_model,
            messages=[{"role": "user", "content": "Call transcript:\n\n" + "\n".join(turns)}],
            system=_SUMMARY_SYSTEM_PROMPT,
            api_key=settings.openrouter_api_key,
        )
        summary = (response.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    except Exception as exc:
        logger.error(f"[voice-native] recap summarization failed: {exc}")
        summary = ""

    if not summary:
        summary = "We spoke on a call just now. If anything didn't get resolved, just text me here."

    get_conversation_log().record_reply(f"Call recap: {summary}")
    logger.info("[voice-native] recap posted to chat")


async def run_bot(transport, handle_sigint: bool = False):
    settings = get_settings()

    stt = OpenAIRealtimeSTTService(api_key=os.getenv("OPENAI_API_KEY"))
    tts = OpenAITTSService(api_key=os.getenv("OPENAI_API_KEY"), voice="nova")

    llm = OpenAILLMService(
        api_key=settings.openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
        model=os.getenv("OPENPOKE_VOICE_MODEL", settings.interaction_agent_model),
    )

    context = LLMContext(
        messages=[{"role": "system", "content": _build_system_prompt()}],
        tools=_make_tools(),
    )
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    guard = RedFlagGuard(context)

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            guard,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(pipeline, params=PipelineParams(enable_metrics=True))

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("[voice-native] caller connected")
        await worker.queue_frames([TTSSpeakFrame(GREETING)])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("[voice-native] caller disconnected")
        try:
            await _post_call_recap(context)
        except Exception as exc:
            logger.warning(f"[voice-native] recap failed: {exc}")
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=handle_sigint)
    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args):
    """Pipecat dev-runner entry point (serves /start, /api/offer, and the web client)."""
    from pipecat.runner.types import SmallWebRTCRunnerArguments

    if not isinstance(runner_args, SmallWebRTCRunnerArguments):
        logger.error(f"Unsupported runner arguments: {type(runner_args)}")
        return

    transport = SmallWebRTCTransport(
        webrtc_connection=runner_args.webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_out_10ms_chunks=2,
        ),
    )
    await run_bot(transport, handle_sigint=runner_args.handle_sigint)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
