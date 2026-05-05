import { NextRequest } from 'next/server';
import { revalidatePath } from 'next/cache';
import { deleteProductTree } from '@/lib/onboard/commit';

export const runtime = 'nodejs';
export const maxDuration = 30;

function bad(reason: string, status = 400, extra?: Record<string, unknown>) {
  return Response.json({ ok: false, error: reason, ...extra }, { status });
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: { slug: string } }
) {
  const expected = process.env.WEB_SHARED_SECRET;
  if (!expected) {
    return bad('WEB_SHARED_SECRET not configured on server', 500);
  }
  if (request.headers.get('x-web-secret') !== expected) {
    return bad('invalid or missing x-web-secret header', 401);
  }

  const { slug } = params;
  if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(slug)) {
    return bad('invalid slug format');
  }

  try {
    await deleteProductTree(slug);
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'delete failed';
    return bad(msg, 502);
  }

  revalidatePath('/');

  return Response.json({ ok: true, slug });
}
