import { NextRequest } from 'next/server';
import { revalidatePath } from 'next/cache';

const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;

export async function POST(request: NextRequest) {
  let body: { product?: unknown };
  try {
    body = await request.json();
  } catch {
    return Response.json({ ok: false, error: 'invalid JSON body' }, { status: 400 });
  }

  const product = body.product;
  if (typeof product !== 'string' || !SLUG_RE.test(product)) {
    return Response.json({ ok: false, error: 'invalid product slug' }, { status: 400 });
  }

  revalidatePath(`/${product}`);
  revalidatePath('/');
  return Response.json({ ok: true });
}
