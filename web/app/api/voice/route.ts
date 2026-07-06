export const runtime = 'nodejs';

export async function POST(req: Request) {
  let body: any;
  try {
    body = await req.json();
  } catch (e) {
    return new Response(JSON.stringify({ ok: false, error: 'Invalid JSON' }), { status: 400 });
  }

  const message = typeof body?.message === 'string' ? body.message : '';
  if (!message.trim()) {
    return new Response(JSON.stringify({ ok: false, error: 'Missing message' }), { status: 400 });
  }

  const serverBase = process.env.PY_SERVER_URL || 'http://localhost:8001';
  const url = `${serverBase.replace(/\/$/, '')}/api/v1/voice/send`;

  try {
    const upstream = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    });
    const text = await upstream.text();
    return new Response(text, {
      status: upstream.status,
      headers: { 'Content-Type': 'application/json' },
    });
  } catch (e: any) {
    console.error('[voice-proxy] upstream error', e);
    return new Response(JSON.stringify({ ok: false, error: e?.message || 'Upstream error' }), {
      status: 502,
    });
  }
}
