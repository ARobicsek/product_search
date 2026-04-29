import Link from 'next/link';
import { Plus } from 'lucide-react';
import { getProducts, getProductReports, getReportContent } from '@/lib/github';

// Helper to extract a summary from markdown (e.g., first non-heading line)
function extractBottomLine(markdown: string) {
  const lines = markdown.split('\n');
  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed && !trimmed.startsWith('#') && !trimmed.startsWith('|') && !trimmed.startsWith('-')) {
      return trimmed.substring(0, 150) + (trimmed.length > 150 ? '...' : '');
    }
  }
  return 'No summary available.';
}

export default async function Home() {
  const products = await getProducts();

  const productData = await Promise.all(
    products.map(async (product) => {
      const reports = await getProductReports(product);
      const latestDate = reports.length > 0 ? reports[0] : null;
      let summary = 'No reports yet.';
      
      if (latestDate) {
        const content = await getReportContent(product, latestDate);
        if (content) {
          summary = extractBottomLine(content);
        }
      }

      return {
        product,
        latestDate,
        summary,
      };
    })
  );

  return (
    <main className="p-4 max-w-2xl mx-auto w-full">
      <header className="mb-8 mt-4 flex items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Product Search</h1>
          <p className="text-gray-500 dark:text-gray-400 mt-2">Daily price tracking reports</p>
        </div>
        <Link
          href="/onboard"
          className="shrink-0 mt-1 flex items-center text-sm font-medium px-3 py-1.5 rounded-full bg-blue-600 text-white hover:bg-blue-700 transition focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          <Plus className="w-4 h-4 mr-1" />
          New
        </Link>
      </header>

      {productData.length === 0 ? (
        <div className="p-6 bg-gray-50 dark:bg-gray-900 rounded-xl text-center border border-gray-100 dark:border-gray-800">
          <p className="text-gray-500">No products found.</p>
        </div>
      ) : (
        <div className="grid gap-4">
          {productData.map((data) => (
            <Link
              key={data.product}
              href={`/${data.product}`}
              className="block p-6 bg-white dark:bg-gray-950 rounded-xl shadow-sm border border-gray-100 dark:border-gray-800 hover:shadow-md transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <div className="flex justify-between items-start mb-3">
                <h2 className="text-xl font-semibold capitalize">
                  {data.product.replace(/-/g, ' ')}
                </h2>
                {data.latestDate && (
                  <span className="text-xs font-medium text-gray-500 bg-gray-100 dark:bg-gray-800 px-2 py-1 rounded-full whitespace-nowrap">
                    {data.latestDate}
                  </span>
                )}
              </div>
              <p className="text-gray-600 dark:text-gray-400 text-sm line-clamp-2">
                {data.summary}
              </p>
            </Link>
          ))}
        </div>
      )}
    </main>
  );
}
