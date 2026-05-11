import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const Module = require('module');
const originalRequire = Module.prototype.require;
Module.prototype.require = function(id: string) {
  if (id === 'server-only') return {};
  return originalRequire.apply(this, arguments);
};

import { deleteProductTree } from '../lib/onboard/commit';

// Mock the global fetch
const originalFetch = global.fetch;

async function runTests() {
  let fetchedUrls: string[] = [];
  let fetchedBodies: any[] = [];
  let methodCalls: string[] = [];

  global.fetch = async (url: string | URL | Request, options?: RequestInit) => {
    const urlStr = url.toString();
    fetchedUrls.push(urlStr);
    methodCalls.push(options?.method || 'GET');
    if (options?.body) {
      fetchedBodies.push(JSON.parse(options.body as string));
    }

    if (urlStr.includes('/contents/products/test-slug')) {
      return new Response(JSON.stringify({ type: 'dir' }), { status: 200 });
    }
    if (urlStr.includes('/contents/reports/test-slug')) {
      return new Response(JSON.stringify({ type: 'dir' }), { status: 200 });
    }
    if (urlStr.includes('/git/ref/heads/main')) {
      return new Response(JSON.stringify({ object: { sha: 'commit-sha-123' } }), { status: 200 });
    }
    if (urlStr.includes('/git/commits/commit-sha-123')) {
      return new Response(JSON.stringify({ tree: { sha: 'tree-sha-456' } }), { status: 200 });
    }
    if (urlStr.includes('/git/trees')) {
      return new Response(JSON.stringify({ sha: 'new-tree-sha-789' }), { status: 201 });
    }
    if (urlStr.includes('/git/commits')) {
      return new Response(JSON.stringify({ sha: 'new-commit-sha-000' }), { status: 201 });
    }
    if (urlStr.includes('/git/refs/heads/main')) {
      return new Response(JSON.stringify({}), { status: 200 });
    }
    return new Response('Not Found', { status: 404 });
  };

  try {
    process.env.GITHUB_DISPATCH_TOKEN = 'test-token';
    const result = await deleteProductTree('test-slug');

    console.assert(result === true, 'Expected deleteProductTree to return true');
    console.assert(fetchedUrls.length === 7, `Expected 7 fetch calls, got ${fetchedUrls.length}`);
    
    // Check tree payload
    const treePayload = fetchedBodies[0];
    console.assert(treePayload.base_tree === 'tree-sha-456', 'Base tree mismatch');
    console.assert(treePayload.tree.length === 2, 'Should delete 2 directories');
    console.assert(treePayload.tree[0].path === 'products/test-slug', 'Path 1 mismatch');
    console.assert(treePayload.tree[0].sha === null, 'Should set sha to null');
    console.assert(treePayload.tree[1].path === 'reports/test-slug', 'Path 2 mismatch');
    console.assert(treePayload.tree[1].sha === null, 'Should set sha to null');

    // Check commit payload
    const commitPayload = fetchedBodies[1];
    console.assert(commitPayload.message === 'chore: delete product test-slug', 'Commit message mismatch');
    console.assert(commitPayload.tree === 'new-tree-sha-789', 'Commit tree mismatch');
    console.assert(commitPayload.parents[0] === 'commit-sha-123', 'Commit parent mismatch');

    // Check ref update payload
    const refPayload = fetchedBodies[2];
    console.assert(refPayload.sha === 'new-commit-sha-000', 'Ref update sha mismatch');

    console.log('✅ deleteProductTree tests passed!');
  } finally {
    global.fetch = originalFetch;
  }
}

runTests().catch(console.error);
