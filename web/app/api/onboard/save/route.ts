import { NextRequest } from 'next/server';
import { revalidatePath } from 'next/cache';
import { commitNewProfile } from '@/lib/onboard/commit';
import { parseAndValidateProfileYaml, ProfileValidationError } from '@/lib/onboard/schema';
import { getProductProfileContent } from '@/lib/github';
import { readAlertsFromYaml } from '@/lib/alerts';
import { probeAndUpdateProfile } from '@/lib/onboard/probe-and-update';

import { validateProfileDraft } from '@/lib/onboard/validation';

export const runtime = 'nodejs';
export const maxDuration = 60;

const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;

function bad(reason: string, status = 400, extra?: Record<string, unknown>) {
  return Response.json({ ok: false, error: reason, ...extra }, { status });
}

// Attempt to get the waitUntil helper from Next.js (available on Vercel
// since Next 15). Falls back to a fire-and-forget promise on local dev.
async function getWaitUntil(): Promise<((p: Promise<unknown>) => void) | null> {
  try {
    // Dynamic import — the module may not exist in older Next.js versions.
    const mod = await import('next/server');
    if ('waitUntil' in mod && typeof mod.waitUntil === 'function') {
      return mod.waitUntil as (p: Promise<unknown>) => void;
    }
  } catch {
    // Not available — fall through.
  }
  return null;
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
    /**
     * ADR-115: set by the OnboardChat "Save and proceed anyway" path after
     * the save-time probe modal couldn't complete (no detail URLs found in
     * the budget). Downgrades the ADR-111 gate to a warning.
     */
    bypassForceDetailBackup?: unknown;
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
  // Keep a reference to the raw draft so we can pass it to the background
  // probe step (only relevant for the `draft` path).
  let draftForProbe: Record<string, unknown> | null = null;
  // ADR-123: `message` stays for back-compat; `userMessage` is the plain-English
  // text the save UI actually renders.
  const warnings: Array<{ host?: string; message: string; userMessage: string }> = [];

  if (body.draft !== undefined && body.draft !== null) {
    if (typeof body.draft !== 'object' || Array.isArray(body.draft)) {
      return bad('draft must be a JSON object');
    }
    try {
      const draft = body.draft as Record<string, unknown>;
      // Onboarder-edit-strips-alerts fix (Phase 17 Part D): the onboarder
      // prompt is intentionally unaware of `alerts` — they're user-driven
      // via the schedule editor. Without this splice, editing a profile
      // through /onboard?edit=<slug> would silently drop every alert the
      // user had configured. We re-attach the existing alerts from the
      // on-disk profile whenever the draft omits the key.
      if (
        body.originalSlug &&
        SLUG_RE.test(body.originalSlug) &&
        draft.alerts === undefined
      ) {
        const existing = await getProductProfileContent(body.originalSlug).catch(
          () => null,
        );
        if (existing) {
          const existingAlerts = readAlertsFromYaml(existing);
          if (existingAlerts.length > 0) {
            draft.alerts = existingAlerts;
          }
        }
      }
      // Optimistic save: render YAML directly from the draft WITHOUT
      // running probes. Probes will run asynchronously after the response.
      draftForProbe = { ...draft };
      
      const state =
        body.state && typeof body.state === 'object' && !Array.isArray(body.state)
          ? (body.state as Record<string, unknown>)
          : null;
      const originalSlug = (body.originalSlug && SLUG_RE.test(body.originalSlug)) ? body.originalSlug : null;
      const bypassForceDetailBackup = body.bypassForceDetailBackup === true;

      const validationRes = validateProfileDraft(draft, state, originalSlug, {
        bypassForceDetailBackup,
      });
      
      warnings.push(
        ...validationRes.warnings.map((message, i) => ({
          message,
          userMessage: validationRes.userWarnings[i] ?? message,
        })),
      );
      
      if (!validationRes.ok || !validationRes.yamlText) {
        return bad(validationRes.errors[0] || 'profile validation failed', 422, {
          details: validationRes.errors.slice(1),
        });
      }
      yamlText = validationRes.yamlText;
      
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'render-yaml failed';
      return bad(`failed to render YAML from draft: ${msg}`, 422);
    }
  } else if (typeof body.yaml === 'string' && body.yaml.trim().length > 0) {
    // Legacy path: no probe gating — older clients send pre-rendered YAML
    // and we accept it as-is to avoid breaking in-flight saves.
    yamlText = body.yaml;
  } else {
    return bad('either draft (object) or yaml (non-empty string) is required');
  }

  let slug: string;

  if (body.draft !== undefined && body.draft !== null) {
    // Already validated via validateProfileDraft which checks slug
    const state = body.state && typeof body.state === 'object' && !Array.isArray(body.state) ? (body.state as Record<string, unknown>) : null;
    const originalSlug = (body.originalSlug && SLUG_RE.test(body.originalSlug)) ? body.originalSlug : null;
    const bypassForceDetailBackup = body.bypassForceDetailBackup === true;
    const validationRes = validateProfileDraft(
      draftForProbe as Record<string, unknown>,
      state,
      originalSlug,
      { bypassForceDetailBackup },
    );
    slug = validationRes.slug!;
  } else {
    if (body.originalSlug && SLUG_RE.test(body.originalSlug)) {
      // Edit mode: aggressively pin the slug to whatever the URL said, even if
      // the LLM hallucinated a new one in the draft.
      yamlText = yamlText!.replace(/^\s*slug\s*:\s*.*$/m, `slug: "${body.originalSlug}"`);
    }

    let parsed;
    try {
      parsed = parseAndValidateProfileYaml(yamlText!);
    } catch (err) {
      if (err instanceof ProfileValidationError) {
        return bad('profile failed schema validation', 422, {
          details: err.errors,
        });
      }
      const msg = err instanceof Error ? err.message : 'profile validation failed';
      return bad(msg, 422);
    }

    slug = parsed.slug;
    if (!SLUG_RE.test(slug)) {
      return bad(`slug ${JSON.stringify(slug)} fails ${SLUG_RE.source}`, 422);
    }
  }

  let result;
  try {
    result = await commitNewProfile(slug, yamlText);
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'commit failed';
    return bad(msg, 502);
  }

  // Schedule background URL probes (draft path only — the legacy yaml
  // path never ran probes). Uses waitUntil() on Vercel to keep the
  // function alive past the response; falls back to fire-and-forget
  // on local dev.
  if (draftForProbe) {
    const probePromise = probeAndUpdateProfile(slug, draftForProbe);
    if (process.env.NODE_ENV === 'development') {
      console.log(`[save] Dev mode: awaiting probeAndUpdateProfile synchronously...`);
      await probePromise;
    } else {
      const waitUntil = await getWaitUntil();
      if (waitUntil) {
        waitUntil(probePromise);
      } else {
        probePromise.catch((err) => {
          console.error('[probe-and-update] fire-and-forget failed:', err);
        });
      }
    }
  }

  revalidatePath('/');

  return Response.json({
    ok: true,
    probeStatus: draftForProbe ? 'pending' : 'skipped',
    warnings,
    ...result,
  });
}
