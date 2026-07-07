'use client';

/**
 * Voice v2 — native UI over the Pipecat media worker.
 *
 * PipecatClient (WebRTC) connects to the voice worker via the /voice-worker
 * Next.js rewrite (same-origin, no CORS). The transcript renders from SDK
 * events: onUserTranscript (interim + final) and onBotTtsText — which emits
 * words AS THEY ARE SPOKEN, so the screen never shows unspoken text and
 * barge-in truncation is visually accurate by construction.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { PipecatClient, TransportState } from '@pipecat-ai/client-js';
import { SmallWebRTCTransport } from '@pipecat-ai/small-webrtc-transport';

type CallStatus = 'idle' | 'connecting' | 'listening' | 'thinking' | 'speaking' | 'ended';
type Turn = { role: 'caller' | 'assistant'; text: string };

export default function CallV2Page() {
  const [status, setStatus] = useState<CallStatus>('idle');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [interim, setInterim] = useState('');
  const [liveBot, setLiveBot] = useState('');
  const [error, setError] = useState<string | null>(null);

  const clientRef = useRef<PipecatClient | null>(null);
  const liveBotRef = useRef('');
  const inCallRef = useRef(false);
  const audioRef = useRef<HTMLAudioElement>(null);
  const transcriptEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [turns, interim, liveBot]);

  const commitBotTurn = useCallback(() => {
    const text = liveBotRef.current.trim();
    if (text) setTurns((prev) => [...prev, { role: 'assistant', text }]);
    liveBotRef.current = '';
    setLiveBot('');
  }, []);

  const startCall = useCallback(async () => {
    setError(null);
    setTurns([]);
    setInterim('');
    liveBotRef.current = '';
    setLiveBot('');
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
        onUserTranscript: (data: any) => {
          if (data?.final) {
            const text = (data.text || '').trim();
            if (text) setTurns((prev) => [...prev, { role: 'caller', text }]);
            setInterim('');
            setStatus('thinking');
          } else {
            setInterim(data?.text || '');
          }
        },
        onUserStartedSpeaking: () => {
          // Barge-in: freeze the bot bubble at the words actually spoken.
          if (liveBotRef.current) {
            liveBotRef.current = `${liveBotRef.current.trim()} —`;
            commitBotTurn();
          }
          setStatus('listening');
        },
        onBotTtsText: (data: any) => {
          // Words arrive as they are spoken — append to the live bubble.
          const word = data?.text || '';
          liveBotRef.current = liveBotRef.current ? `${liveBotRef.current} ${word}` : word;
          setLiveBot(liveBotRef.current);
        },
        onBotStartedSpeaking: () => setStatus('speaking'),
        onBotStoppedSpeaking: () => {
          commitBotTurn();
          if (inCallRef.current) setStatus('listening');
        },
      },
    });

    // Play the bot's audio track through our own <audio> element.
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
  }, [commitBotTurn]);

  const hangUp = useCallback(async () => {
    inCallRef.current = false;
    commitBotTurn();
    setInterim('');
    setStatus('ended');
    try {
      await clientRef.current?.disconnect();
    } catch {
      /* already closing */
    }
    clientRef.current = null;
  }, [commitBotTurn]);

  useEffect(() => {
    return () => {
      inCallRef.current = false;
      clientRef.current?.disconnect().catch(() => {});
    };
  }, []);

  const statusLabel: Record<CallStatus, string> = {
    idle: 'Ready to call',
    connecting: 'Connecting…',
    listening: 'Listening…',
    thinking: 'Thinking…',
    speaking: 'Speaking',
    ended: 'Call ended — recap sent to your chat',
  };

  const statusColor: Record<CallStatus, string> = {
    idle: 'bg-gray-300',
    connecting: 'bg-amber-400 animate-pulse',
    listening: 'bg-green-500 animate-pulse',
    thinking: 'bg-amber-400 animate-pulse',
    speaking: 'bg-blue-500 animate-pulse',
    ended: 'bg-gray-400',
  };

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col p-6">
      <audio ref={audioRef} autoPlay hidden />

      <header className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-semibold">Maple Family Clinic 📞</h1>
          <span className="flex items-center gap-2 text-sm text-gray-500">
            <span className={`h-2.5 w-2.5 rounded-full ${statusColor[status]}`} />
            {statusLabel[status]}
          </span>
        </div>
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

      <div className="flex-1 space-y-3 overflow-y-auto rounded-lg border border-gray-200 bg-white p-4">
        {turns.length === 0 && status === 'idle' && (
          <p className="py-12 text-center text-sm text-gray-400">
            Tap Start Call and speak — streaming voice, no headphones needed.
          </p>
        )}
        {turns.map((turn, i) => (
          <div key={i} className={`flex ${turn.role === 'caller' ? 'justify-end' : 'justify-start'}`}>
            <div
              className={`max-w-[80%] rounded-2xl px-4 py-2 text-sm ${
                turn.role === 'caller' ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-900'
              }`}
            >
              {turn.text}
            </div>
          </div>
        ))}
        {liveBot && (
          <div className="flex justify-start">
            <div className="max-w-[80%] rounded-2xl bg-gray-100 px-4 py-2 text-sm text-gray-900">
              {liveBot}
            </div>
          </div>
        )}
        {interim && (
          <div className="flex justify-end">
            <div className="max-w-[80%] rounded-2xl bg-blue-200 px-4 py-2 text-sm italic text-blue-900">
              {interim}
            </div>
          </div>
        )}
        <div ref={transcriptEndRef} />
      </div>

      <div className="mt-4 flex justify-center gap-3">
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
