import Link from 'next/link';
import { ChevronLeft } from 'lucide-react';
import { OnboardChat } from './OnboardChat';

export default function OnboardPage() {
  return (
    <main className="min-h-screen bg-gray-50 dark:bg-[#0a0a0a] flex flex-col">
      <header className="sticky top-0 z-10 bg-white/80 dark:bg-[#0a0a0a]/80 backdrop-blur-md border-b border-gray-200 dark:border-gray-800 p-4 flex items-center justify-between">
        <Link
          href="/"
          className="flex items-center text-blue-600 dark:text-blue-400 hover:text-blue-800 text-sm font-medium"
        >
          <ChevronLeft className="w-5 h-5 mr-1" />
          Back
        </Link>
        <h1 className="font-semibold">Onboard a new product</h1>
        <span className="w-12" aria-hidden />
      </header>

      <OnboardChat />
    </main>
  );
}
