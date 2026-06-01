/**
 * Types for the structured JSON sidecar (ADR-096) emitted by the worker
 * alongside each daily markdown report. Mirrors the shape produced by
 * `worker/src/product_search/synthesizer/report_json.py`.
 */

export type Severity = 'info' | 'warning' | 'danger';

export interface Badge {
  key: string;
  label: string;
  severity: Severity;
}

export interface ResultListing {
  rank: number;
  source: string;
  vendor_host: string | null;
  url: string;
  title: string;
  price_usd: number | null;
  total_for_target_usd: number | null;
  currency_approx_fx: string | null;
  condition: string | null;
  seller_name: string | null;
  is_kit: boolean;
  kit_module_count: number;
  badges: Badge[];
}

export type SourceStatus =
  | 'ok'
  | 'no_match'
  | 'empty_page'
  | 'parser_gap'
  | 'transient'
  | 'permanent'
  // ADR-099: the carry-gate skipped paid extraction because the product isn't
  // listed at this vendor yet (~$0 this run); auto-wakes when it's stocked.
  | 'watched';

export interface ScrappeyAttempt {
  url: string;
  body_len: number;
  origin_status: number;
  exit_ip: string | null;
  exit_country: string | null;
  exit_hosting: boolean | null;
  cf_challenge: boolean;
  triggered_by: 'tier1_configured' | 'dynamic_weak_render_fallback' | 'adr107_post_extract';
  elapsed_ms: number;
}

export interface ResultSource {
  label: string;
  host: string | null;
  fetched: number;
  passed: number;
  status: SourceStatus;
  status_label: string;
  reason: string;
  scrappey_attempts: ScrappeyAttempt[];
}

export interface PendingSource {
  label: string;
  url: string | null;
  note: string | null;
}

export interface RunCostStep {
  step: string;
  provider: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
}

export interface RunCost {
  steps: RunCostStep[];
  total_usd: number;
  any_unpriced: boolean;
}

export interface ReportSidecarV1 {
  schema_version: 1;
  generated_at: string;
  snapshot_date: string | null;
  product: { slug: string; display_name: string };
  listings: ResultListing[];
  listings_meta: { total_passed: number; shown: number; cap: number };
  sources: ResultSource[];
  sources_pending: PendingSource[];
  run_cost: RunCost;
}

export interface ReportSidecarV2 {
  schema_version: 2;
  generated_at: string;
  snapshot_date: string | null;
  slug: string;
  display_name: string;
  product_type: string | null;
  columns: string[];
  listings: ResultListing[];
  /** Full ranked survivor set (price-sorted, no vendor cap). Present in
   *  sidecars built after the progressive-disclosure change; older sidecars
   *  may omit it, in which case the UI falls back to `listings`. */
  all_listings?: ResultListing[];
  overflow: Record<string, number>;
  hidden_anomalies: number;
  recall_count: number;
  survivor_count: number;
  displayed_count: number;
  outcome: {
    class: string;
    message: string;
    notes: { class: string; message: string }[];
  };
  run_cost: RunCost;
}

export type ReportSidecar = ReportSidecarV1 | ReportSidecarV2;

/**
 * Best-effort validator. The JSON sidecar is internal data we produce,
 * so the validator is intentionally lax — it confirms the schema_version
 * matches what the renderer knows about and that the top-level shape
 * looks right. If anything is off, the page falls back to the legacy
 * markdown view.
 */
export function parseSidecar(raw: unknown): ReportSidecar | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  if (r.schema_version !== 1 && r.schema_version !== 2) return null;
  if (!Array.isArray(r.listings)) return null;
  return r as unknown as ReportSidecar;
}
