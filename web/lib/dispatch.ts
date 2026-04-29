import 'server-only';

const REPO = process.env.GITHUB_REPO ?? 'ARobicsek/product_search';
const WORKFLOW_FILE = 'search-on-demand.yml';
const BRANCH = 'main';

function dispatchHeaders(): Record<string, string> {
  const token = process.env.GITHUB_DISPATCH_TOKEN;
  if (!token) {
    throw new Error('GITHUB_DISPATCH_TOKEN is not set');
  }
  return {
    Accept: 'application/vnd.github+json',
    Authorization: `Bearer ${token}`,
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'Product-Search-PWA',
  };
}

export async function dispatchOnDemandRun(product: string): Promise<void> {
  const url = `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches`;
  const res = await fetch(url, {
    method: 'POST',
    headers: { ...dispatchHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ ref: BRANCH, inputs: { product } }),
    cache: 'no-store',
  });
  if (res.status !== 204) {
    const body = await res.text().catch(() => '');
    throw new Error(`GitHub dispatch failed: ${res.status} ${res.statusText} ${body}`);
  }
}

export interface RunStatus {
  state: 'pending' | 'queued' | 'in_progress' | 'completed';
  conclusion: string | null;
  runId: number | null;
  htmlUrl: string | null;
  startedAt: string | null;
  completedAt: string | null;
}

interface GhRun {
  id: number;
  status: string;
  conclusion: string | null;
  html_url: string;
  created_at: string;
  run_started_at: string | null;
  updated_at: string;
  name: string;
  display_title: string;
  event: string;
}

export async function getLatestOnDemandRun(product: string, sinceIso: string): Promise<RunStatus> {
  const created = encodeURIComponent(`>=${sinceIso}`);
  const url =
    `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW_FILE}/runs` +
    `?event=workflow_dispatch&per_page=20&created=${created}`;

  const res = await fetch(url, { headers: dispatchHeaders(), cache: 'no-store' });
  if (!res.ok) {
    throw new Error(`GitHub runs query failed: ${res.status} ${res.statusText}`);
  }
  const data = (await res.json()) as { workflow_runs?: GhRun[] };
  const runs = data.workflow_runs ?? [];

  const match = runs.find(
    (r) => r.display_title?.includes(product) || r.name?.includes(product),
  ) ?? runs[0];

  if (!match) {
    return {
      state: 'pending',
      conclusion: null,
      runId: null,
      htmlUrl: null,
      startedAt: null,
      completedAt: null,
    };
  }

  const state = (
    match.status === 'completed' ? 'completed'
    : match.status === 'queued' ? 'queued'
    : match.status === 'in_progress' ? 'in_progress'
    : 'pending'
  ) as RunStatus['state'];

  return {
    state,
    conclusion: match.conclusion,
    runId: match.id,
    htmlUrl: match.html_url,
    startedAt: match.run_started_at,
    completedAt: state === 'completed' ? match.updated_at : null,
  };
}
