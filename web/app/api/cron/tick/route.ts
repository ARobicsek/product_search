import { NextRequest } from 'next/server';
import { dispatchScheduledTick } from '@/lib/dispatch';

// Phase 20 / ADR-052: external scheduler (cron-job.org, every 15 min) hits this
// route, which forwards a workflow_dispatch to search-scheduled.yml using the
// GITHUB_DISPATCH_TOKEN that already lives in Vercel env. The powerful GitHub
// PAT never leaves Vercel; cron-job.org only holds CRON_TRIGGER_SECRET (a
// low-value shared secret — a leak only lets an attacker force a scheduler
// tick, which costs a run only if a profile is actually due).
//
// Both GET and POST are accepted because some external schedulers only issue
// GET. The guard (500 if env unset, 401 on missing/mismatch) mirrors
// /api/dispatch for consistency.

async function handle(request: NextRequest): Promise<Response> {
  const legacySecret = process.env.CRON_TRIGGER_SECRET;
  const vercelSecret = process.env.CRON_SECRET;

  if (!legacySecret && !vercelSecret) {
    return Response.json(
      { ok: false, error: 'Cron secret not configured' },
      { status: 500 },
    );
  }

  const legacyProvided = request.headers.get('x-cron-secret');
  const authHeader = request.headers.get('authorization');
  
  const isLegacyAuth = legacySecret && legacyProvided === legacySecret;
  const isVercelAuth = vercelSecret && authHeader === `Bearer ${vercelSecret}`;

  if (!isLegacyAuth && !isVercelAuth) {
    return Response.json(
      { ok: false, error: 'invalid or missing cron secret' },
      { status: 401 },
    );
  }

  const dispatchedAt = new Date().toISOString();
  try {
    await dispatchScheduledTick();
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'dispatch failed';
    return Response.json({ ok: false, error: msg }, { status: 502 });
  }

  return Response.json({ ok: true, dispatchedAt });
}

export async function POST(request: NextRequest) {
  return handle(request);
}

export async function GET(request: NextRequest) {
  return handle(request);
}
