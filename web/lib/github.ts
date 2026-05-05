const REPO = 'ARobicsek/product_search';
const BRANCH = 'main';

export interface GitHubFile {
  name: string;
  path: string;
  sha: string;
  size: number;
  url: string;
  html_url: string;
  git_url: string;
  download_url: string;
  type: 'file' | 'dir';
}

function getHeaders() {
  const headers: Record<string, string> = {
    'Accept': 'application/vnd.github.v3+json',
    'User-Agent': 'Product-Search-PWA',
  };
  
  if (process.env.GITHUB_TOKEN) {
    headers['Authorization'] = `token ${process.env.GITHUB_TOKEN}`;
  }
  
  return headers;
}

async function listDirSlugs(dirPath: string): Promise<string[]> {
  try {
    const res = await fetch(`https://api.github.com/repos/${REPO}/contents/${dirPath}?ref=${BRANCH}&_cb=${Date.now()}`, {
      headers: getHeaders(),
      cache: 'no-store',
    });

    if (!res.ok) {
      if (res.status === 404) return [];
      throw new Error(`GitHub API error: ${res.status} ${res.statusText}`);
    }

    const data: GitHubFile[] = await res.json();
    return data.filter(item => item.type === 'dir').map(item => item.name);
  } catch (err) {
    console.error(`Failed to list ${dirPath}:`, err);
    return [];
  }
}

export async function getProducts(): Promise<string[]> {
  // Union of slugs that have a report committed and slugs that have a profile
  // committed. The latter covers the gap right after onboarding, before the
  // first scheduled or on-demand run lands a report.
  const [withReports, onboarded] = await Promise.all([
    listDirSlugs('reports'),
    listDirSlugs('products'),
  ]);
  const merged = new Set<string>([...withReports, ...onboarded.filter((s) => s !== '_template')]);
  return [...merged].sort();
}

export async function getProductProfileExists(slug: string): Promise<boolean> {
  try {
    const res = await fetch(
      `https://api.github.com/repos/${REPO}/contents/products/${slug}/profile.yaml?ref=${BRANCH}&_cb=${Date.now()}`,
      { headers: getHeaders(), cache: 'no-store' },
    );
    if (res.status === 200) return true;
    if (res.status === 404) return false;
    throw new Error(`GitHub API error: ${res.status} ${res.statusText}`);
  } catch (err) {
    console.error(`Failed to probe profile for ${slug}:`, err);
    return false;
  }
}

export async function getProductReports(product: string): Promise<string[]> {
  try {
    // No-store: a Run-now click commits a new report and the user expects to
    // see it on the next page render. The 1-hour data cache silently masked
    // the first prod-data success in Phase 12 wave 4.
    const res = await fetch(`https://api.github.com/repos/${REPO}/contents/reports/${product}?ref=${BRANCH}&_cb=${Date.now()}`, {
      headers: getHeaders(),
      cache: 'no-store',
    });
    
    if (!res.ok) {
      if (res.status === 404) return [];
      throw new Error(`GitHub API error: ${res.status} ${res.statusText}`);
    }
    
    const data: GitHubFile[] = await res.json();
    // Return dates (without .md) sorted descending
    return data
      .filter(item => item.type === 'file' && item.name.endsWith('.md'))
      .map(item => item.name.replace('.md', ''))
      .sort((a, b) => b.localeCompare(a));
  } catch (err) {
    console.error(`Failed to fetch reports for ${product}:`, err);
    return [];
  }
}

export async function getReportContent(product: string, date: string): Promise<string | null> {
  try {
    // Use the strongly consistent REST API instead of raw.githubusercontent.com.
    // The raw CDN caches branch ref resolution for ~5 minutes at the origin server,
    // which ignores our cache buster. The REST API reads directly from the Git database.
    const url = `https://api.github.com/repos/${REPO}/contents/reports/${product}/${date}.md?ref=${BRANCH}&_cb=${Date.now()}`;
    
    const res = await fetch(url, {
      headers: getHeaders(),
      cache: 'no-store',
    });
    
    if (!res.ok) {
      if (res.status === 404) return null;
      throw new Error(`GitHub contents fetch error: ${res.status} ${res.statusText}`);
    }
    
    const data = await res.json();
    return Buffer.from(data.content, 'base64').toString('utf8');
  } catch (err) {
    console.error(`Failed to fetch content for ${product}/${date}:`, err);
    return null;
  }
}

export async function getProductProfileContent(slug: string): Promise<string | null> {
  try {
    const url = `https://api.github.com/repos/${REPO}/contents/products/${slug}/profile.yaml?ref=${BRANCH}&_cb=${Date.now()}`;
    
    const res = await fetch(url, {
      headers: getHeaders(),
      cache: 'no-store',
    });
    
    if (!res.ok) {
      if (res.status === 404) return null;
      throw new Error(`GitHub contents fetch error: ${res.status} ${res.statusText}`);
    }
    
    const data = await res.json();
    return Buffer.from(data.content, 'base64').toString('utf8');
  } catch (err) {
    console.error(`Failed to fetch profile content for ${slug}:`, err);
    return null;
  }
}

