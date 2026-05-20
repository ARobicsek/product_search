import 'server-only';

import { gateUniversalAiUrls, type ProbeReport } from '@/lib/onboard/gate-universal-ai';
import { renderProfileYaml } from '@/lib/onboard/render-yaml';
import { commitNewProfile } from '@/lib/onboard/commit';

// Background probe-and-update: runs AFTER the initial save response has been
// sent to the client (via waitUntil). Probes every universal_ai_search URL
// in the draft; if any are demoted to sources_pending, commits an updated
// profile.yaml so the on-disk version is already cleaned up before the next
// worker run.
//
// This is best-effort — if it fails (network blip, GitHub conflict), the
// worst case is the profile carries URLs that will yield 0 listings on the
// next run. The user can always re-save or manually edit.

export interface ProbeAndUpdateResult {
  probed: boolean;
  reports: ProbeReport[];
  demotions: number;
  followUpCommit: boolean;
}

export async function probeAndUpdateProfile(
  slug: string,
  draft: Record<string, unknown>,
): Promise<ProbeAndUpdateResult> {
  const result: ProbeAndUpdateResult = {
    probed: false,
    reports: [],
    demotions: 0,
    followUpCommit: false,
  };

  try {
    const gated = await gateUniversalAiUrls(draft);
    result.probed = true;
    result.reports = gated.reports;
    result.demotions = gated.reports.filter((r) => !r.ok).length;

    if (result.demotions === 0) {
      console.log(`[probe-and-update] ${slug}: all ${gated.reports.length} URL(s) passed — no follow-up commit needed`);
      return result;
    }

    // Some URLs were demoted — re-render and commit the updated profile.
    console.log(
      `[probe-and-update] ${slug}: ${result.demotions}/${gated.reports.length} URL(s) demoted — committing follow-up`,
    );
    for (const r of gated.reports) {
      if (!r.ok) {
        console.log(`  ✗ ${r.url}: ${r.reason}`);
      }
    }

    const updatedYaml = renderProfileYaml(gated.draft);
    await commitNewProfile(slug, updatedYaml);
    result.followUpCommit = true;

    console.log(`[probe-and-update] ${slug}: follow-up commit succeeded`);
  } catch (err) {
    // Best-effort — log and move on. The profile is already saved (just
    // without the probe-based demotions). The production worker will
    // handle bad URLs gracefully (0 listings, no crash).
    console.error(
      `[probe-and-update] ${slug}: background probe failed:`,
      err instanceof Error ? err.message : String(err),
    );
  }

  return result;
}
