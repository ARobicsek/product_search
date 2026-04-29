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

export async function getProducts(): Promise<string[]> {
  try {
    const res = await fetch(`https://api.github.com/repos/${REPO}/contents/reports?ref=${BRANCH}`, {
      headers: getHeaders(),
      next: { revalidate: 3600 } // Cache for 1 hour
    });
    
    if (!res.ok) {
      if (res.status === 404) return [];
      throw new Error(`GitHub API error: ${res.status} ${res.statusText}`);
    }
    
    const data: GitHubFile[] = await res.json();
    return data.filter(item => item.type === 'dir').map(item => item.name);
  } catch (err) {
    console.error('Failed to fetch products:', err);
    return [];
  }
}

export async function getProductReports(product: string): Promise<string[]> {
  try {
    const res = await fetch(`https://api.github.com/repos/${REPO}/contents/reports/${product}?ref=${BRANCH}`, {
      headers: getHeaders(),
      next: { revalidate: 3600 }
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
    const url = `https://raw.githubusercontent.com/${REPO}/${BRANCH}/reports/${product}/${date}.md`;
    const headers: Record<string, string> = {
      'User-Agent': 'Product-Search-PWA',
    };
    if (process.env.GITHUB_TOKEN) {
      headers['Authorization'] = `token ${process.env.GITHUB_TOKEN}`;
    }

    const res = await fetch(url, {
      headers,
      next: { revalidate: 3600 }
    });
    
    if (!res.ok) {
      if (res.status === 404) return null;
      throw new Error(`GitHub raw fetch error: ${res.status} ${res.statusText}`);
    }
    
    return await res.text();
  } catch (err) {
    console.error(`Failed to fetch content for ${product}/${date}:`, err);
    return null;
  }
}
