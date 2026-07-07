"""Voice v3 experiment — OpenAI Realtime speech-to-speech.

Same media worker shape as bot.py (v2 cascade), but the STT→LLM→TTS trio is
replaced by ONE speech-to-speech service: the model hears audio and speaks
audio directly. Reuses v2's system prompt assembly, intake tools, red-flag
guard, and post-call recap.

Known tradeoff (deliberate, documented): no inspectable text layer between
stages — transcripts are best-effort model outputs, which weakens the
audit/compliance story for a clinic vs the cascade. This branch exists to
measure the latency/prosody gain against that cost.

Run: python -m server.voice_pipecat.bot_realtime --host localhost --port 7860
"""

import os

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.openai.realtime.events import (
    AudioConfiguration,
    AudioInput,
    InputAudioTranscription,
    SessionProperties,
)
from pipecat.services.openai.realtime.llm import OpenAIRealtimeLLMService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

from server.voice_pipecat.bot import (
    GREETING,
    RedFlagGuard,
    _build_system_prompt,
    _make_tools,
    _post_call_recap,
    _report_outstanding,
)

load_dotenv(override=True)


async def run_bot(transport, handle_sigint: bool = False):
    outstanding: list = []
    llm = OpenAIRealtimeLLMService(
        api_key=os.getenv("OPENAI_API_KEY"),
        session_properties=SessionProperties(
            instructions=_build_system_prompt(),
            tools=_make_tools(outstanding),
            tool_choice="auto",
            # Caller-audio transcription is OFF by default in the Realtime API.
            # We need it for: user bubbles in the UI, the deterministic
            # red-flag guard, and the caller side of the post-call recap.
            # NOTE: this transcript is a SIDECAR — a separate transcription
            # model on the same audio. The realtime model consumes raw audio
            # and never sees this text, so transcript and model-understanding
            # can diverge. In the v2 cascade the transcript IS the model input
            # (single source of truth) — the core audit tradeoff between the
            # two architectures.
            audio=AudioConfiguration(
                input=AudioInput(
                    # gpt-realtime-whisper is the natively-streaming transcription
                    # model intended for realtime sessions (gpt-4o-transcribe is
                    # for file/request-response workflows). Still a sidecar:
                    # the realtime model consumes raw audio, never this text.
                    transcription=InputAudioTranscription(model="gpt-realtime-whisper")
                ),
            ),
        ),
    )

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        realtime_service_mode=True,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    guard = RedFlagGuard(context)

    pipeline = Pipeline(
        [
            transport.input(),
            guard,
            user_aggregator,
            llm,
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(pipeline, params=PipelineParams(enable_metrics=True))

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("[voice-realtime] caller connected")
        context.add_message(
            {
                "role": "developer",
                "content": f'Greet the caller with exactly: "{GREETING}"',
            }
        )
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("[voice-realtime] caller disconnected")
        try:
            await _post_call_recap(context)
        except Exception as exc:
            logger.warning(f"[voice-realtime] recap failed: {exc}")
        await _report_outstanding(outstanding)
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=handle_sigint)
    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args):
    """Pipecat dev-runner entry point."""
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
