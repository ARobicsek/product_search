import { NextRequest } from 'next/server';
import { getLatestOnDemandRun } from '@/lib/dispatch';

const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;

export async function GET(request: NextRequest) {
  const url = new URL(request.url);
  const product = url.searchParams.get('product') ?? '';
  const since = url.searchParams.get('since') ?? '';

  if (!SLUG_RE.test(product)) {
    return Response.json({ ok: false, error: 'invalid product slug' }, { status: 400 });
  }
  if (Number.isNaN(Date.parse(since))) {
    return Response.json({ ok: false, error: 'invalid since timestamp' }, { status: 400 });
  }

  try {
    const status = await getLatestOnDemandRun(product, since);
    return Response.json({ ok: true, ...status });
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'run-status query failed';
    return Response.json({ ok: false, error: msg }, { status: 502 });
  }
}
