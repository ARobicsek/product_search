import 'server-only';

const REPO = process.env.GITHUB_REPO ?? 'ARobicsek/product_search';
const BRANCH = 'main';

function contentsHeaders(): Record<string, string> {
  // Prefer a separate fine-grained PAT scoped to `contents: write` only. If the
  // user is reusing the dispatch token, that token must have both `actions:
  // write` and `contents: write`.
  const token = process.env.GITHUB_CONTENTS_TOKEN ?? process.env.GITHUB_DISPATCH_TOKEN;
  if (!token) {
    throw new Error('GITHUB_CONTENTS_TOKEN (or GITHUB_DISPATCH_TOKEN fallback) is not set');
  }
  return {
    Accept: 'application/vnd.github+json',
    Authorization: `Bearer ${token}`,
    'X-GitHub-Api-Version': '2022-11-28',
    'Content-Type': 'application/json',
    'User-Agent': 'Product-Search-PWA',
  };
}

interface ContentsResponse {
  content?: { html_url?: string; sha?: string };
  commit?: { html_url?: string; sha?: string };
}

interface PutResult {
  fileUrl: string | null;
  commitUrl: string | null;
  commitSha: string | null;
}

async function putFile(
  repoPath: string,
  textContent: string,
  message: string,
  sha?: string,
): Promise<PutResult> {
  const url = `https://api.github.com/repos/${REPO}/contents/${repoPath}`;
  const payload: any = {
    message,
    content: Buffer.from(textContent, 'utf-8').toString('base64'),
    branch: BRANCH,
  };
  if (sha) {
    payload.sha = sha;
  }
  const body = JSON.stringify(payload);
  const res = await fetch(url, { method: 'PUT', headers: contentsHeaders(), body, cache: 'no-store' });
  if (res.status !== 201 && res.status !== 200) {
    const txt = await res.text().catch(() => '');
    throw new Error(`GitHub PUT ${repoPath} failed: ${res.status} ${res.statusText} ${txt}`);
  }
  const data = (await res.json()) as ContentsResponse;
  return {
    fileUrl: data.content?.html_url ?? null,
    commitUrl: data.commit?.html_url ?? null,
    commitSha: data.commit?.sha ?? null,
  };
}

async function getFileSha(repoPath: string): Promise<string | null> {
  const url = `https://api.github.com/repos/${REPO}/contents/${repoPath}?ref=${BRANCH}`;
  const headers = contentsHeaders();
  // GET doesn't need Content-Type; remove it to keep the request clean.
  delete (headers as Record<string, string>)['Content-Type'];
  const res = await fetch(url, { headers, cache: 'no-store' });
  if (res.status === 200) {
    const data = await res.json();
    return data.sha;
  }
  if (res.status === 404) return null;
  const txt = await res.text().catch(() => '');
  throw new Error(`GitHub GET ${repoPath} failed: ${res.status} ${res.statusText} ${txt}`);
}

export interface CommitProfileResult {
  slug: string;
  profileFileUrl: string | null;
  commitUrl: string | null;
  commitSha: string | null;
  qvlCreated: boolean;
}

export async function commitNewProfile(
  slug: string,
  profileYaml: string,
): Promise<CommitProfileResult> {
  const profilePath = `products/${slug}/profile.yaml`;
  const qvlPath = `products/${slug}/qvl.yaml`;

  const existingSha = await getFileSha(profilePath);
  const message = existingSha
    ? `onboard: update ${slug} profile\n\nProfile updated via /onboard interview.`
    : `onboard: add ${slug} profile\n\nProfile created via /onboard interview.`;

  // Write the profile
  const profileResult = await putFile(
    profilePath,
    profileYaml,
    message,
    existingSha ?? undefined
  );

  let qvlCreated = false;
  const existingQvlSha = await getFileSha(qvlPath);
  if (!existingQvlSha) {
    const stub = `# QVL (Qualified Vendor List) for ${slug}.\n# Add entries below as you find them. Each entry needs at minimum:\n#   - mpn, brand, capacity_gb, speed_mts\n# See products/ddr5-rdimm-256gb/qvl.yaml for a populated example.\nqvl: []\n`;
    await putFile(qvlPath, stub, `onboard: stub QVL for ${slug}`);
    qvlCreated = true;
  }

  return {
    slug,
    profileFileUrl: profileResult.fileUrl,
    commitUrl: profileResult.commitUrl,
    commitSha: profileResult.commitSha,
    qvlCreated,
  };
}
