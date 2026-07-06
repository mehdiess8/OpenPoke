export const runtime = 'nodejs';

export async function POST(req: Request) {
  let body: any = {};
  try {
    body = await req.json();
  } catch {
    /* empty body is fine — heard defaults to "" */
  }

  const serverBase = process.env.PY_SERVER_URL || 'http://localhost:8001';
  const url = `${serverBase.replace(/\/$/, '')}/api/v1/voice/interrupted`;

  try {
    const upstream = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ heard: typeof body?.heard === 'string' ? body.heard : '' }),
    });
    const text = await upstream.text();
    return new Response(text, {
      status: upstream.status,
      headers: { 'Content-Type': 'application/json' },
    });
  } catch (e: any) {
    console.error('[voice-interrupted-proxy] upstream error', e);
    return new Response(JSON.stringify({ ok: false, error: e?.message || 'Upstream error' }), {
      status: 502,
    });
  }
}
