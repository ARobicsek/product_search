import { NextRequest } from 'next/server';
import { revalidatePath } from 'next/cache';
import { commitNewProfile } from '@/lib/onboard/commit';
import { parseAndValidateProfileYaml, ProfileValidationError } from '@/lib/onboard/schema';
import { getProductProfileContent } from '@/lib/github';
import { readAlertsFromYaml } from '@/lib/alerts';

import { validateProfileDraftV2 } from '@/lib/onboard/validation-v2';

export const runtime = 'nodejs';
export const maxDuration = 60;

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

  let body: {
    yaml?: unknown;
    draft?: unknown;
    originalSlug?: string | null;
    state?: unknown;
  };
  try {
    body = await request.json();
  } catch {
    return bad('invalid JSON body');
  }

  // Phase 14: prefer the structured `draft` JSON path. The legacy `yaml`
  // path stays for any in-flight client that hasn't reloaded since the
  // chat route was updated.
  let yamlText: string | null = null;
  let slug: string | null = null;
  // ADR-123: `message` stays for back-compat; `userMessage` is the plain-English
  // text the save UI actually renders.
  const warnings: Array<{ host?: string; message: string; userMessage: string }> = [];

  const originalSlug =
    typeof body.originalSlug === 'string' && SLUG_RE.test(body.originalSlug)
      ? body.originalSlug
      : null;

  if (body.draft !== undefined && body.draft !== null) {
    if (typeof body.draft !== 'object' || Array.isArray(body.draft)) {
      return bad('draft must be a JSON object');
    }
    const draft = body.draft as Record<string, unknown>;

    // Onboarder-edit-strips-alerts fix (Phase 17 Part D): the onboarder is
    // intentionally unaware of `alerts` — they're user-driven via the schedule
    // editor. Without this re-attach, editing a profile through
    // /onboard?edit=<slug> would silently drop every alert the user configured.
    if (originalSlug && draft.alerts === undefined) {
      const existing = await getProductProfileContent(originalSlug).catch(() => null);
      if (existing) {
        const existingAlerts = readAlertsFromYaml(existing);
        if (existingAlerts.length > 0) {
          draft.alerts = existingAlerts;
        }
      }
    }

    if (draft.schema_version === 2) {
      // Phase 34 (ADR-137): v2 "query + spec" draft. Validate + render via the
      // v2 gate; no probing — the whole probe/backfill apparatus retired with
      // self-scraping (REBUILD_PLAN §6).
      const r = validateProfileDraftV2(draft, originalSlug);
      warnings.push(
        ...r.warnings.map((message, i) => ({
          message,
          userMessage: r.userWarnings[i] ?? message,
        })),
      );
      if (!r.ok || !r.yamlText || !r.slug) {
        return bad(r.errors[0] || 'profile validation failed', 422, {
          details: r.errors.slice(1),
        });
      }
      yamlText = r.yamlText;
      slug = r.slug;
    } else {
      // The v1 onboarder (and its per-vendor probe apparatus) was retired in
      // Phase 36 (REBUILD_PLAN §10). Only schema_version: 2 drafts are saved.
      return bad(
        'schema_version: 2 is required — the v1 onboarder was retired (Phase 36)',
        422,
      );
    }
  } else if (typeof body.yaml === 'string' && body.yaml.trim().length > 0) {
    // Legacy path: older clients send pre-rendered YAML; accept it as-is.
    yamlText = body.yaml;
    if (originalSlug) {
      // Edit mode: pin the slug to whatever the URL said, even if the LLM
      // hallucinated a new one in the draft.
      yamlText = yamlText.replace(/^\s*slug\s*:\s*.*$/m, `slug: "${originalSlug}"`);
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
    slug = parsed.slug;
    if (!SLUG_RE.test(slug)) {
      return bad(`slug ${JSON.stringify(slug)} fails ${SLUG_RE.source}`, 422);
    }
  } else {
    return bad('either draft (object) or yaml (non-empty string) is required');
  }

  if (!yamlText || !slug) {
    return bad('internal: failed to resolve slug/yaml from request', 500);
  }

  let result;
  try {
    result = await commitNewProfile(slug, yamlText);
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'commit failed';
    return bad(msg, 502);
  }

  revalidatePath('/');

  return Response.json({
    ok: true,
    // The per-vendor probe apparatus was retired in Phase 36; v2 saves never
    // probe. Field kept for response back-compat with older clients.
    probeStatus: 'skipped',
    warnings,
    ...result,
  });
}
