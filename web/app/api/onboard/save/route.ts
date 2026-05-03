import { NextRequest } from 'next/server';
import { revalidatePath } from 'next/cache';
import { commitNewProfile } from '@/lib/onboard/commit';
import { parseAndValidateProfileYaml, ProfileValidationError } from '@/lib/onboard/schema';
import { renderProfileYaml } from '@/lib/onboard/render-yaml';

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

  let body: { yaml?: unknown; draft?: unknown; originalSlug?: string | null };
  try {
    body = await request.json();
  } catch {
    return bad('invalid JSON body');
  }

  // Phase 14: prefer the structured `draft` JSON path. The legacy `yaml`
  // path stays for any in-flight client that hasn't reloaded since the
  // chat route was updated.
  let yamlText: string | null = null;
  if (body.draft !== undefined && body.draft !== null) {
    if (typeof body.draft !== 'object' || Array.isArray(body.draft)) {
      return bad('draft must be a JSON object');
    }
    try {
      yamlText = renderProfileYaml(body.draft as Record<string, unknown>);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'render-yaml failed';
      return bad(`failed to render YAML from draft: ${msg}`, 422);
    }
  } else if (typeof body.yaml === 'string' && body.yaml.trim().length > 0) {
    yamlText = body.yaml;
  } else {
    return bad('either draft (object) or yaml (non-empty string) is required');
  }

  if (body.originalSlug && SLUG_RE.test(body.originalSlug)) {
    // Edit mode: aggressively pin the slug to whatever the URL said, even if
    // the LLM hallucinated a new one in the draft.
    yamlText = yamlText.replace(/^\s*slug\s*:\s*.*$/m, `slug: "${body.originalSlug}"`);
  }

  let parsed;
  try {
    parsed = parseAndValidateProfileYaml(yamlText);
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
    result = await commitNewProfile(slug, yamlText);
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'commit failed';
    return bad(msg, 502);
  }

  revalidatePath('/');

  return Response.json({ ok: true, ...result });
}
