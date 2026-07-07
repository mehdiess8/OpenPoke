'use client';

/**
 * Voice v3 UI — speech-to-speech call with an orb, not a chat transcript.
 *
 * Design rationale: in the Realtime (s2s) architecture the user-side
 * transcript is a SIDECAR approximation (a separate transcription model —
 * the realtime model consumes raw audio and never sees that text). Rendering
 * it as authoritative chat bubbles misrepresents the system. So: no user
 * text on screen. The orb reflects the conversation state, and the agent's
 * spoken words roll beneath it as they are said (onBotTtsText).
 *
 * The sidecar transcription stays ENABLED in the pipeline — it feeds the
 * deterministic red-flag guard and the caller side of the post-call recap.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { PipecatClient, TransportState } from '@pipecat-ai/client-js';
import { SmallWebRTCTransport } from '@pipecat-ai/small-webrtc-transport';

type CallStatus = 'idle' | 'connecting' | 'listening' | 'thinking' | 'speaking' | 'ended';

export default function CallV2Page() {
  const [status, setStatus] = useState<CallStatus>('idle');
  const [stream, setStream] = useState('');
  const [error, setError] = useState<string | null>(null);

  const clientRef = useRef<PipecatClient | null>(null);
  const inCallRef = useRef(false);
  const audioRef = useRef<HTMLAudioElement>(null);
  const streamEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    streamEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [stream]);

  const startCall = useCallback(async () => {
    setError(null);
    setStream('');
    setStatus('connecting');
    inCallRef.current = true;

    const client = new PipecatClient({
      transport: new SmallWebRTCTransport({
        iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
      }),
      enableCam: false,
      enableMic: true,
      callbacks: {
        onConnected: () => setStatus('listening'),
        onDisconnected: () => {
          if (inCallRef.current) {
            inCallRef.current = false;
            setStatus('ended');
          }
        },
        onTransportStateChanged: (state: TransportState) => {
          if (state === 'error') setError('Connection error — is the voice worker running on :7860?');
        },
        onUserStartedSpeaking: () => setStatus('listening'),
        onUserStoppedSpeaking: () => setStatus('thinking'),
        onBotStartedSpeaking: () => setStatus('speaking'),
        onBotStoppedSpeaking: () => {
          if (inCallRef.current) setStatus('listening');
        },
        onBotTtsText: (data: any) => {
          const word = (data?.text || '').trim();
          if (word) setStream((prev) => (prev ? `${prev} ${word}` : word));
        },
      },
    });

    client.on('trackStarted', (track: MediaStreamTrack, participant: any) => {
      if (!participant?.local && track.kind === 'audio' && audioRef.current) {
        audioRef.current.srcObject = new MediaStream([track]);
        audioRef.current.play().catch(() => {});
      }
    });

    clientRef.current = client;
    try {
      await client.connect({ webrtcUrl: '/voice-worker/api/offer' });
    } catch (e: any) {
      setError(e?.message || 'Failed to connect to the voice worker.');
      setStatus('idle');
      inCallRef.current = false;
    }
  }, []);

  const hangUp = useCallback(async () => {
    inCallRef.current = false;
    setStatus('ended');
    try {
      await clientRef.current?.disconnect();
    } catch {
      /* already closing */
    }
    clientRef.current = null;
  }, []);

  useEffect(() => {
    return () => {
      inCallRef.current = false;
      clientRef.current?.disconnect().catch(() => {});
    };
  }, []);

  const orbClass: Record<CallStatus, string> = {
    idle: 'scale-90 bg-gradient-to-br from-gray-200 to-gray-300',
    connecting: 'scale-95 bg-gradient-to-br from-amber-200 to-amber-300 orb-breathe',
    listening: 'scale-100 bg-gradient-to-br from-emerald-300 to-teal-400 orb-breathe',
    thinking: 'scale-95 bg-gradient-to-br from-amber-300 to-orange-400 orb-breathe-fast',
    speaking: 'scale-110 bg-gradient-to-br from-blue-400 to-indigo-500 orb-talk',
    ended: 'scale-90 bg-gradient-to-br from-gray-300 to-gray-400',
  };

  const statusLabel: Record<CallStatus, string> = {
    idle: 'Tap to call Maple Family Clinic',
    connecting: 'Connecting…',
    listening: 'Listening',
    thinking: 'Thinking…',
    speaking: 'Maple is speaking',
    ended: 'Call ended — recap sent to your chat',
  };

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col p-6">
      <style>{`
        @keyframes orbBreathe {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.05); }
        }
        @keyframes orbTalk {
          0%, 100% { transform: scale(1.06); border-radius: 48% 52% 51% 49%; }
          25% { transform: scale(1.14); border-radius: 52% 48% 49% 51%; }
          50% { transform: scale(1.02); border-radius: 50% 50% 52% 48%; }
          75% { transform: scale(1.12); border-radius: 49% 51% 48% 52%; }
        }
        .orb-breathe { animation: orbBreathe 3s ease-in-out infinite; }
        .orb-breathe-fast { animation: orbBreathe 1.1s ease-in-out infinite; }
        .orb-talk { animation: orbTalk 0.9s ease-in-out infinite; }
      `}</style>

      <audio ref={audioRef} autoPlay hidden />

      <header className="mb-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold">Maple Family Clinic 📞</h1>
        <div className="flex items-center gap-2">
          <a href="/call" className="rounded-md border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50">
            Classic mode
          </a>
          <a href="/" className="rounded-md border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50">
            Back to chat
          </a>
        </div>
      </header>

      {error && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="flex flex-1 flex-col items-center justify-center gap-8">
        <button
          onClick={status === 'idle' || status === 'ended' ? startCall : hangUp}
          className={`h-44 w-44 rounded-full shadow-xl transition-all duration-500 ${orbClass[status]}`}
          aria-label={status === 'idle' || status === 'ended' ? 'Start call' : 'Hang up'}
        />

        <p className="text-sm font-medium text-gray-500">{statusLabel[status]}</p>

        <div className="relative h-32 w-full max-w-lg overflow-y-auto px-4 [mask-image:linear-gradient(to_bottom,transparent,black_30%)]">
          <p className="text-center text-sm leading-6 text-gray-600">{stream}</p>
          <div ref={streamEndRef} />
        </div>

        {status === 'idle' || status === 'ended' ? (
          <button
            onClick={startCall}
            className="rounded-full bg-green-600 px-8 py-3 text-sm font-semibold text-white hover:bg-green-700"
          >
            Start Call
          </button>
        ) : (
          <button
            onClick={hangUp}
            className="rounded-full bg-red-600 px-8 py-3 text-sm font-semibold text-white hover:bg-red-700"
          >
            Hang Up
          </button>
        )}
      </div>
    </main>
  );
}
