export const runtime = 'nodejs';

export async function POST() {
  const serverBase = process.env.PY_SERVER_URL || 'http://localhost:8001';
  const url = `${serverBase.replace(/\/$/, '')}/api/v1/voice/end`;

  try {
    const upstream = await fetch(url, { method: 'POST' });
    const text = await upstream.text();
    return new Response(text, {
      status: upstream.status,
      headers: { 'Content-Type': 'application/json' },
    });
  } catch (e: any) {
    console.error('[voice-end-proxy] upstream error', e);
    return new Response(JSON.stringify({ ok: false, error: e?.message || 'Upstream error' }), {
      status: 502,
    });
  }
}
