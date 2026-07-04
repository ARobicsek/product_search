'use client';

import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import Link from 'next/link';
import { ChevronDown, ChevronRight, FileText, Sparkles, X } from 'lucide-react';
import { summarizeProfile } from '@/lib/profile-summary';
import { detectBrowserTimeZone, humanizeSchedule, readScheduleFromYaml } from '@/lib/schedule';
import { describeRule, readAlertsFromYaml } from '@/lib/alerts';

/**
 * Read-only profile viewer. Renders entirely from the `profileYaml` string the
 * product page already fetched — no network call, no LLM, $0. The only
 * token-spending path (Edit with the assistant) lives as an explicit button
 * inside the modal, so the cheap "just look" action is the default and the
 * paid one is deliberate.
 */
export function ViewProfileButton({
  product,
  profileYaml,
}: {
  product: string;
  profileYaml: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [showRaw, setShowRaw] = useState(false);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  const summary = summarizeProfile(profileYaml);

  // Schedule + alerts reuse the app's existing humanizers (schedule needs the
  // browser time zone), so they read in the same language as the editor.
  const schedule = profileYaml ? readScheduleFromYaml(profileYaml) : null;
  const scheduleText = profileYaml ? humanizeSchedule(schedule, detectBrowserTimeZone()) : null;
  const alerts = profileYaml ? readAlertsFromYaml(profileYaml) : [];

  const editHref = `/onboard?edit=${product}`;

  return (
    <>
      <button
        type="button"
        onClick={() => {
          setShowRaw(false);
          setOpen(true);
        }}
        className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-lg bg-gray-100 text-gray-900 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700 transition focus:outline-none focus:ring-2 focus:ring-blue-500"
        aria-label="View profile"
      >
        <FileText className="w-4 h-4" />
        <span className="hidden sm:inline">Profile</span>
      </button>

      {open &&
        typeof document !== 'undefined' &&
        createPortal(
          <div
            className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-4 bg-black/50 backdrop-blur-sm"
            onClick={() => setOpen(false)}
          >
            <div
              role="dialog"
              aria-modal="true"
              aria-label="Profile"
              className="bg-white dark:bg-gray-900 rounded-2xl shadow-xl w-full max-w-lg max-h-[88vh] flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-200"
              onClick={(e) => e.stopPropagation()}
            >
              {/* Header */}
              <div className="flex items-start justify-between gap-3 p-4 border-b border-gray-100 dark:border-gray-800">
                <div className="min-w-0">
                  <h3 className="text-base font-semibold truncate">
                    {summary.displayName ?? product}
                  </h3>
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 truncate">
                    {[summary.productType, summary.slug ?? product].filter(Boolean).join(' · ')}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="shrink-0 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition"
                  aria-label="Close"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* Body */}
              <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3.5 text-sm">
                {summary.parseError && (
                  <p className="text-[13px] text-amber-800 dark:text-amber-300 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-900/40 rounded-lg px-3 py-2">
                    {summary.parseError} Showing the raw file below.
                  </p>
                )}

                {summary.ok && (
                  <>
                    {summary.description && (
                      <Row label="Tracking">
                        <p className="text-gray-700 dark:text-gray-300 leading-snug">
                          {summary.description}
                        </p>
                        {summary.target && (
                          <p className="text-[12px] text-gray-500 dark:text-gray-400 mt-0.5">
                            Target: {summary.target}
                          </p>
                        )}
                      </Row>
                    )}
                    {!summary.description && summary.target && (
                      <Row label="Target">{summary.target}</Row>
                    )}

                    {summary.queries.length > 0 && (
                      <Row label="Search queries">
                        <ul className="space-y-0.5">
                          {summary.queries.map((q, i) => (
                            <li key={i} className="text-gray-700 dark:text-gray-300">
                              &ldquo;{q}&rdquo;
                            </li>
                          ))}
                        </ul>
                      </Row>
                    )}

                    {(summary.aliases.length > 0 ||
                      summary.titleExcludes.length > 0 ||
                      summary.variantStrict !== null) && (
                      <Row label="Matching">
                        <div className="space-y-1">
                          {summary.aliases.length > 0 && (
                            <ChipLine label="Aliases" items={summary.aliases} />
                          )}
                          {summary.titleExcludes.length > 0 && (
                            <ChipLine label="Exclude titles" items={summary.titleExcludes} muted />
                          )}
                          {summary.variantStrict !== null && (
                            <p className="text-[12px] text-gray-600 dark:text-gray-400">
                              {summary.variantStrict
                                ? 'Exact variant only — must match an alias in the title.'
                                : 'Family match allowed — related variants can pass.'}
                            </p>
                          )}
                        </div>
                      </Row>
                    )}

                    <Row label="Filters">
                      {summary.filters.length > 0 ? (
                        <span className="text-gray-700 dark:text-gray-300">
                          {summary.filters.join(' · ')}
                        </span>
                      ) : (
                        <span className="text-gray-500 dark:text-gray-400">
                          No filters — all matching listings.
                        </span>
                      )}
                    </Row>

                    <Row label="Sources">
                      <div className="flex flex-wrap gap-1.5">
                        {summary.sources.map((s) => (
                          <span
                            key={s.label}
                            className={`inline-flex items-center gap-1 text-[12px] px-2 py-0.5 rounded-full border ${
                              s.enabled
                                ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 border-blue-200 dark:border-blue-900/50'
                                : 'text-gray-400 dark:text-gray-500 border-gray-200 dark:border-gray-700 line-through decoration-1'
                            }`}
                            title={s.detail ?? undefined}
                          >
                            {s.label}
                            {s.enabled && s.detail && (
                              <span className="text-blue-500/70 dark:text-blue-400/70 no-underline">
                                {s.detail}
                              </span>
                            )}
                          </span>
                        ))}
                      </div>
                    </Row>

                    {summary.flags.length > 0 && (
                      <Row label="Flags">
                        <ul className="space-y-0.5">
                          {summary.flags.map((f, i) => (
                            <li key={i} className="text-[12px] text-gray-600 dark:text-gray-400">
                              {f}
                            </li>
                          ))}
                        </ul>
                      </Row>
                    )}

                    {(summary.vendorAllowlist.length > 0 ||
                      summary.vendorBlocklist.length > 0) && (
                      <Row label="Vendors">
                        <div className="space-y-1">
                          {summary.vendorAllowlist.length > 0 && (
                            <ChipLine label="Only" items={summary.vendorAllowlist} />
                          )}
                          {summary.vendorBlocklist.length > 0 && (
                            <ChipLine label="Never" items={summary.vendorBlocklist} muted />
                          )}
                        </div>
                      </Row>
                    )}

                    <Row label="Display">
                      <span className="text-gray-700 dark:text-gray-300">
                        {[
                          summary.maxListings !== null
                            ? `Up to ${summary.maxListings} listings`
                            : null,
                          summary.perVendorCap !== null
                            ? `${summary.perVendorCap} per vendor`
                            : null,
                        ]
                          .filter(Boolean)
                          .join(' · ') || '—'}
                      </span>
                      {summary.displayAttrs.length > 0 && (
                        <p className="text-[12px] text-gray-500 dark:text-gray-400 mt-0.5">
                          Columns: {summary.displayAttrs.join(', ')}
                        </p>
                      )}
                    </Row>

                    {scheduleText && <Row label="Schedule">{scheduleText}</Row>}

                    <Row label="Alerts">
                      {alerts.length > 0 ? (
                        <ul className="space-y-0.5">
                          {alerts.map((a, i) => (
                            <li key={i} className="text-gray-700 dark:text-gray-300">
                              {describeRule(a)}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <span className="text-gray-500 dark:text-gray-400">None configured.</span>
                      )}
                    </Row>
                  </>
                )}

                {/* Raw YAML — progressive disclosure */}
                {summary.raw && (
                  <div className="pt-1">
                    <button
                      type="button"
                      onClick={() => setShowRaw((v) => !v)}
                      className="inline-flex items-center gap-1 text-[12px] font-medium text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
                      aria-expanded={showRaw}
                    >
                      {showRaw ? (
                        <ChevronDown className="w-3.5 h-3.5" />
                      ) : (
                        <ChevronRight className="w-3.5 h-3.5" />
                      )}
                      {showRaw ? 'Hide raw YAML' : 'View raw YAML'}
                    </button>
                    {showRaw && (
                      <pre className="mt-2 p-3 text-[11px] leading-snug font-mono bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-800 rounded-lg overflow-x-auto whitespace-pre text-gray-800 dark:text-gray-200">
                        {summary.raw}
                      </pre>
                    )}
                  </div>
                )}
              </div>

              {/* Footer — the one paid path, made explicit */}
              <div className="border-t border-gray-100 dark:border-gray-800 p-3 bg-gray-50 dark:bg-gray-900/50">
                <Link
                  href={editHref}
                  className="w-full flex items-center justify-center gap-2 rounded-lg bg-blue-600 text-white text-sm font-medium px-4 py-2 hover:bg-blue-700 transition no-underline focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <Sparkles className="w-4 h-4" />
                  Edit with the assistant →
                </Link>
                <p className="text-[11px] text-gray-500 dark:text-gray-400 text-center mt-1.5">
                  Viewing is free. Editing opens the AI assistant, which uses tokens.
                </p>
              </div>
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide font-semibold text-gray-500 dark:text-gray-400 mb-0.5">
        {label}
      </div>
      <div className="text-[13px]">{children}</div>
    </div>
  );
}

function ChipLine({ label, items, muted }: { label: string; items: string[]; muted?: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[11px] text-gray-500 dark:text-gray-400 shrink-0">{label}:</span>
      {items.map((it, i) => (
        <span
          key={i}
          className={`text-[12px] px-1.5 py-0.5 rounded ${
            muted
              ? 'bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400'
              : 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-200'
          }`}
        >
          {it}
        </span>
      ))}
    </div>
  );
}

export default ViewProfileButton;
