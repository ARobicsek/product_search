import { NextRequest } from 'next/server';
import { revalidatePath } from 'next/cache';
import { commitNewProfile } from '@/lib/onboard/commit';
import { parseAndValidateProfileYaml, ProfileValidationError } from '@/lib/onboard/schema';

export const runtime = 'nodejs';
export const maxDuration = 30;

const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;

function bad(reason: string, status = 400, extra?: Record<string, unknown>) {
  return Response.json({ ok: false, error: reason, ...extra }, { status });
}

export async function POST(request: NextRequest) {
  const expected = process.env.WEB_SHARED_SECRET;
  if (!expected) {
    return bad('WEB_SHARED_SECRET not configured on server', 500);
  }
  if (request.headers.get('x-web-secret') !== expected) {
    return bad('invalid or missing x-web-secret header', 401);
  }

  let body: { yaml?: unknown };
  try {
    body = await request.json();
  } catch {
    return bad('invalid JSON body');
  }
  if (typeof body.yaml !== 'string' || body.yaml.trim().length === 0) {
    return bad('yaml must be a non-empty string');
  }

  let parsed;
  try {
    parsed = parseAndValidateProfileYaml(body.yaml);
  } catch (err) {
    if (err instanceof ProfileValidationError) {
      return bad('profile failed schema validation', 422, { details: err.errors });
    }
    const msg = err instanceof Error ? err.message : 'profile validation failed';
    return bad(msg, 422);
  }

  const slug = parsed.slug;
  if (!SLUG_RE.test(slug)) {
    return bad(`slug ${JSON.stringify(slug)} fails ${SLUG_RE.source}`, 422);
  }

  let result;
  try {
    result = await commitNewProfile(slug, body.yaml);
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'commit failed';
    return bad(msg, 502);
  }

  // Invalidate the home page so the new product appears once a report lands.
  revalidatePath('/');

  return Response.json({ ok: true, ...result });
}
