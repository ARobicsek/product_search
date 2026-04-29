import { NextRequest } from 'next/server';
import { dispatchOnDemandRun } from '@/lib/dispatch';

const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;

function unauthorized(reason: string) {
  return Response.json({ ok: false, error: reason }, { status: 401 });
}

function badRequest(reason: string) {
  return Response.json({ ok: false, error: reason }, { status: 400 });
}

export async function POST(request: NextRequest) {
  const expected = process.env.WEB_SHARED_SECRET;
  if (!expected) {
    return Response.json(
      { ok: false, error: 'WEB_SHARED_SECRET not configured on server' },
      { status: 500 },
    );
  }

  const provided = request.headers.get('x-web-secret');
  if (!provided || provided !== expected) {
    return unauthorized('invalid or missing x-web-secret header');
  }

  let body: { product?: unknown };
  try {
    body = await request.json();
  } catch {
    return badRequest('invalid JSON body');
  }

  const product = body.product;
  if (typeof product !== 'string' || !SLUG_RE.test(product)) {
    return badRequest('product must be a valid slug');
  }

  const dispatchedAt = new Date().toISOString();
  try {
    await dispatchOnDemandRun(product);
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'dispatch failed';
    return Response.json({ ok: false, error: msg }, { status: 502 });
  }

  return Response.json({ ok: true, product, dispatchedAt });
}
