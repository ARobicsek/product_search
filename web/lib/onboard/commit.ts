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

async function dirExists(path: string): Promise<boolean> {
  const headers = contentsHeaders();
  delete (headers as Record<string, string>)['Content-Type'];
  const url = `https://api.github.com/repos/${REPO}/contents/${path}?ref=${BRANCH}`;
  const res = await fetch(url, { headers, cache: 'no-store' });
  if (res.status === 200) return true;
  if (res.status === 404) return false;
  const txt = await res.text().catch(() => '');
  throw new Error(`GitHub GET ${path} failed: ${res.status} ${res.statusText} ${txt}`);
}

export async function deleteProductTree(slug: string): Promise<boolean> {
  const headers = contentsHeaders();
  delete (headers as Record<string, string>)['Content-Type'];

  // Only include paths that actually exist — GitHub's Git Trees API returns
  // 422 GitRPC::BadObjectState when asked to delete a path that's not in the
  // base tree (e.g. a product onboarded but never run has no reports/ dir).
  const candidates = [`products/${slug}`, `reports/${slug}`];
  const existing = (
    await Promise.all(candidates.map(async (p) => ((await dirExists(p)) ? p : null)))
  ).filter((p): p is string => p !== null);
  if (existing.length === 0) {
    throw new Error(`Nothing to delete: neither products/${slug} nor reports/${slug} exists`);
  }

  // 1. Get HEAD commit SHA
  const refUrl = `https://api.github.com/repos/${REPO}/git/ref/heads/${BRANCH}`;
  const refRes = await fetch(refUrl, { headers, cache: 'no-store' });
  if (!refRes.ok) throw new Error(`GitHub GET ref failed: ${refRes.status}`);
  const refData = await refRes.json();
  const commitSha = refData.object.sha;

  // 2. Get commit object to find the base tree
  const commitRes = await fetch(`https://api.github.com/repos/${REPO}/git/commits/${commitSha}`, { headers, cache: 'no-store' });
  if (!commitRes.ok) throw new Error(`GitHub GET commit failed: ${commitRes.status}`);
  const commitData = await commitRes.json();
  const baseTreeSha = commitData.tree.sha;

  // 3. Create a new tree with the directories removed
  const postHeaders = contentsHeaders();
  const treePayload = {
    base_tree: baseTreeSha,
    tree: existing.map((path) => ({ path, mode: '040000', sha: null })),
  };
  const treeRes = await fetch(`https://api.github.com/repos/${REPO}/git/trees`, {
    method: 'POST',
    headers: postHeaders,
    body: JSON.stringify(treePayload),
    cache: 'no-store'
  });
  if (!treeRes.ok) {
    const errText = await treeRes.text().catch(()=>'');
    throw new Error(`GitHub POST tree failed: ${treeRes.status} ${errText}`);
  }
  const treeData = await treeRes.json();
  const newTreeSha = treeData.sha;

  // 4. Create new commit
  const newCommitPayload = {
    message: `chore: delete product ${slug}`,
    tree: newTreeSha,
    parents: [commitSha]
  };
  const newCommitRes = await fetch(`https://api.github.com/repos/${REPO}/git/commits`, {
    method: 'POST',
    headers: postHeaders,
    body: JSON.stringify(newCommitPayload),
    cache: 'no-store'
  });
  if (!newCommitRes.ok) {
    const errText = await newCommitRes.text().catch(()=>'');
    throw new Error(`GitHub POST commit failed: ${newCommitRes.status} ${errText}`);
  }
  const newCommitData = await newCommitRes.json();
  const newCommitSha = newCommitData.sha;

  // 5. Update reference
  const updateRefPayload = {
    sha: newCommitSha,
    force: false
  };
  const updateRefRes = await fetch(`https://api.github.com/repos/${REPO}/git/refs/heads/${BRANCH}`, {
    method: 'PATCH',
    headers: postHeaders,
    body: JSON.stringify(updateRefPayload),
    cache: 'no-store'
  });
  if (!updateRefRes.ok) {
    const errText = await updateRefRes.text().catch(()=>'');
    throw new Error(`GitHub PATCH ref failed: ${updateRefRes.status} ${errText}`);
  }

  return true;
}
