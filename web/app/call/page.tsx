'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

type CallStatus = 'idle' | 'listening' | 'thinking' | 'speaking' | 'ended';
type Turn = { role: 'caller' | 'assistant'; text: string };

export default function CallPage() {
  const [status, setStatus] = useState<CallStatus>('idle');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [interim, setInterim] = useState('');
  const [recap, setRecap] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [browserWarning, setBrowserWarning] = useState<string | null>(null);

  useEffect(() => {
    const ua = navigator.userAgent;
    const isSafari = /Safari/.test(ua) && !/Chrome|Chromium|Edg/.test(ua);
    if (isSafari) {
      setBrowserWarning(
        'Safari speech recognition is unreliable (results often never fire). Open this page in Chrome for the working demo.'
      );
    }
  }, []);

  const recognitionRef = useRef<any>(null);
  const statusRef = useRef<CallStatus>('idle');
  const inFlightRef = useRef(false);
  const inCallRef = useRef(false);
  const transcriptEndRef = useRef<HTMLDivElement>(null);

  const setStatusBoth = useCallback((s: CallStatus) => {
    statusRef.current = s;
    setStatus(s);
  }, []);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [turns, interim]);

  // Speak a reply via browser TTS; mic stays open so the caller can barge in.
  const speak = useCallback(
    (text: string) => {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = 1.05;
      const voices = window.speechSynthesis.getVoices();
      const preferred = voices.find((v) => v.name.includes('Samantha') || v.name.includes('Google US English'));
      if (preferred) utterance.voice = preferred;
      utterance.onstart = () => setStatusBoth('speaking');
      utterance.onend = () => {
        if (inCallRef.current && statusRef.current === 'speaking') setStatusBoth('listening');
      };
      window.speechSynthesis.speak(utterance);
    },
    [setStatusBoth]
  );

  // Send one finished caller utterance to the agent and speak the reply.
  const sendUtterance = useCallback(
    async (text: string) => {
      if (inFlightRef.current || !text.trim()) return;
      inFlightRef.current = true;
      setTurns((prev) => [...prev, { role: 'caller', text }]);
      setInterim('');
      setStatusBoth('thinking');

      try {
        const res = await fetch('/api/voice', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: text }),
        });
        const data = await res.json();
        const reply: string = data?.reply || "Sorry, I didn't catch that. Could you say it again?";
        setTurns((prev) => [...prev, { role: 'assistant', text: reply }]);
        if (inCallRef.current) speak(reply);
      } catch (e: any) {
        setError(e?.message || 'Connection error');
        setStatusBoth('listening');
      } finally {
        inFlightRef.current = false;
      }
    },
    [speak, setStatusBoth]
  );

  const startCall = useCallback(() => {
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) {
      setError('Speech recognition not supported — use Chrome.');
      return;
    }

    setError(null);
    setRecap(null);
    setTurns([]);
    inCallRef.current = true;

    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = 'en-US';

    recognition.onresult = (event: any) => {
      let interimText = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        const text = result[0].transcript;
        if (result.isFinal) {
          sendUtterance(text.trim());
        } else {
          interimText += text;
        }
      }
      if (interimText) {
        // Barge-in: caller started talking while the assistant is speaking.
        if (window.speechSynthesis.speaking) {
          window.speechSynthesis.cancel();
          setStatusBoth('listening');
        }
        setInterim(interimText);
      }
    };

    recognition.onerror = (event: any) => {
      if (event.error === 'not-allowed') {
        setError('Microphone access denied. Allow mic access and try again.');
        setStatusBoth('idle');
        inCallRef.current = false;
      }
      // 'no-speech' and 'aborted' are routine — onend handles restart.
    };

    // Chrome stops recognition after silence; restart while the call is live.
    recognition.onend = () => {
      if (inCallRef.current) {
        try {
          recognition.start();
        } catch {
          /* already started */
        }
      }
    };

    recognitionRef.current = recognition;
    recognition.start();
    setStatusBoth('listening');
  }, [sendUtterance, setStatusBoth]);

  const hangUp = useCallback(async () => {
    inCallRef.current = false;
    recognitionRef.current?.stop();
    window.speechSynthesis.cancel();
    setInterim('');
    setStatusBoth('ended');

    try {
      const res = await fetch('/api/voice/end', { method: 'POST' });
      const data = await res.json();
      if (data?.summary) setRecap(data.summary);
    } catch {
      /* recap is best-effort */
    }
  }, [setStatusBoth]);

  useEffect(() => {
    return () => {
      inCallRef.current = false;
      recognitionRef.current?.stop();
      window.speechSynthesis.cancel();
    };
  }, []);

  const statusLabel: Record<CallStatus, string> = {
    idle: 'Ready to call',
    listening: 'Listening…',
    thinking: 'Thinking…',
    speaking: 'Speaking',
    ended: 'Call ended',
  };

  const statusColor: Record<CallStatus, string> = {
    idle: 'bg-gray-300',
    listening: 'bg-green-500 animate-pulse',
    thinking: 'bg-amber-400 animate-pulse',
    speaking: 'bg-blue-500 animate-pulse',
    ended: 'bg-gray-400',
  };

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col p-6">
      <header className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-semibold">Maple Family Clinic 📞</h1>
          <span className="flex items-center gap-2 text-sm text-gray-500">
            <span className={`h-2.5 w-2.5 rounded-full ${statusColor[status]}`} />
            {statusLabel[status]}
          </span>
        </div>
        <a href="/" className="rounded-md border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50">
          Back to chat
        </a>
      </header>

      {browserWarning && (
        <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800">
          ⚠️ {browserWarning}
        </div>
      )}

      {error && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="flex-1 space-y-3 overflow-y-auto rounded-lg border border-gray-200 bg-white p-4">
        {turns.length === 0 && status === 'idle' && (
          <p className="py-12 text-center text-sm text-gray-400">
            Tap Start Call and speak — the assistant answers out loud.
            <br />
            Works best in Chrome with headphones.
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
        {interim && (
          <div className="flex justify-end">
            <div className="max-w-[80%] rounded-2xl bg-blue-200 px-4 py-2 text-sm italic text-blue-900">
              {interim}
            </div>
          </div>
        )}
        {recap && (
          <div className="rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
            <span className="font-semibold">Recap sent to your chat:</span> {recap}
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
