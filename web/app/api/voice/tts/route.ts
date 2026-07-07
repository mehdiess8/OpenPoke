export const runtime = 'nodejs';

export async function POST(req: Request) {
  let body: any;
  try {
    body = await req.json();
  } catch {
    return new Response(JSON.stringify({ ok: false, error: 'Invalid JSON' }), { status: 400 });
  }

  const text = typeof body?.text === 'string' ? body.text : '';
  if (!text.trim()) {
    return new Response(JSON.stringify({ ok: false, error: 'Missing text' }), { status: 400 });
  }

  const serverBase = process.env.PY_SERVER_URL || 'http://localhost:8001';
  const url = `${serverBase.replace(/\/$/, '')}/api/v1/voice/tts`;

  try {
    const upstream = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!upstream.ok) {
      const detail = await upstream.text();
      return new Response(detail, { status: upstream.status });
    }
    const audio = await upstream.arrayBuffer();
    return new Response(audio, {
      status: 200,
      headers: { 'Content-Type': upstream.headers.get('Content-Type') || 'audio/mpeg' },
    });
  } catch (e: any) {
    console.error('[voice-tts-proxy] upstream error', e);
    return new Response(JSON.stringify({ ok: false, error: e?.message || 'Upstream error' }), {
      status: 502,
    });
  }
}
