// Shared client+server constants for the per-product report-table column
// registry. Mirror of `worker/src/product_search/synthesizer/synthesizer.py`'s
// `COLUMN_DEFS`. The set in `web/lib/onboard/schema.ts` (KNOWN_REPORT_COLUMNS)
// is the validator's allow-list; this module's array is what the UI renders
// and reorders. Both must list the same column ids.

export interface ReportColumnDef {
  id: string;
  label: string;
  description: string;
}

export const REPORT_COLUMN_DEFS: ReportColumnDef[] = [
  { id: 'rank', label: 'Rank', description: '1, 2, 3 — listing position in the table' },
  { id: 'source', label: 'Source', description: 'Adapter id (linked to the listing URL)' },
  { id: 'title', label: 'Title', description: 'Listing title as the seller wrote it' },
  { id: 'price_unit', label: 'Price (unit)', description: 'Per-unit price in USD' },
  {
    id: 'total_for_target',
    label: 'Total for target',
    description: 'Total cost to reach the profile target (e.g. 8x32GB)',
  },
  { id: 'qty', label: 'Qty', description: 'Quantity available; "unknown" when not declared' },
  { id: 'condition', label: 'Condition', description: 'New / used / refurbished' },
  { id: 'brand', label: 'Brand', description: 'Brand name (auto-inferred when adapter omits)' },
  { id: 'mpn', label: 'MPN', description: 'Manufacturer part number' },
  { id: 'seller', label: 'Seller', description: 'Seller name / handle' },
  { id: 'seller_rating', label: 'Seller rating', description: 'Seller rating percent' },
  { id: 'ship_from', label: 'Ships from', description: 'Country of origin' },
  { id: 'qvl_status', label: 'QVL', description: 'Whether MPN is in the qualified vendor list' },
  { id: 'flags', label: 'Flags', description: 'Any flags raised by the validator pipeline' },
];

export const REPORT_COLUMN_IDS: string[] = REPORT_COLUMN_DEFS.map((c) => c.id);

export const DEFAULT_REPORT_COLUMNS: string[] = [
  'rank',
  'source',
  'title',
  'price_unit',
  'total_for_target',
  'qty',
  'seller',
  'flags',
];

/** Replace (or insert) the `report_columns:` block in a profile YAML.
 *
 * Handles three forms of the field as it might appear in committed YAML:
 *   1. Block list — `report_columns:\n  - rank\n  - source\n…`
 *   2. Inline list — `report_columns: [rank, source, …]`
 *   3. Absent — uses the default 8 columns; we insert a new block before
 *      `schedule:` (the conventional placement) or append at end of file.
 *
 * The output always emits the block-list form, which is what the
 * onboarding AI also produces and what the existing committed profiles
 * use. No quoting/escaping is needed because column ids are restricted
 * to the allow-list of bare-word identifiers.
 */
export function applyReportColumnsToYaml(
  yamlText: string,
  columns: string[]
): string {
  const newBlock = ['report_columns:', ...columns.map((c) => `  - ${c}`)].join('\n');

  // Case 1: block-list form.
  const blockListRe = /^report_columns:[ \t]*\r?\n((?:[ \t]+-[^\n]*\r?\n?)+)/m;
  if (blockListRe.test(yamlText)) {
    return yamlText.replace(blockListRe, newBlock + '\n');
  }

  // Case 2: inline list form (single line).
  const inlineRe = /^report_columns:[ \t]*\[[^\]]*\][ \t]*$/m;
  if (inlineRe.test(yamlText)) {
    return yamlText.replace(inlineRe, newBlock);
  }

  // Case 3: not present. Insert before `schedule:` if it exists, else append.
  const schedRe = /^schedule:/m;
  if (schedRe.test(yamlText)) {
    return yamlText.replace(schedRe, `${newBlock}\n\n$&`);
  }
  return yamlText.replace(/\s*$/, '') + '\n\n' + newBlock + '\n';
}

/** Read the existing `report_columns:` block from a YAML string.
 *
 * Returns the ordered list, or null when the field is absent. Best-effort —
 * not a full YAML parser; relies on the same conventions
 * `applyReportColumnsToYaml` emits.
 */
export function readReportColumnsFromYaml(yamlText: string): string[] | null {
  const blockListRe = /^report_columns:[ \t]*\r?\n((?:[ \t]+-[^\n]*\r?\n?)+)/m;
  const blockMatch = blockListRe.exec(yamlText);
  if (blockMatch) {
    return blockMatch[1]
      .split(/\r?\n/)
      .map((ln) => ln.trim())
      .filter((ln) => ln.startsWith('-'))
      .map((ln) => ln.slice(1).trim());
  }

  const inlineRe = /^report_columns:[ \t]*\[([^\]]*)\][ \t]*$/m;
  const inlineMatch = inlineRe.exec(yamlText);
  if (inlineMatch) {
    return inlineMatch[1]
      .split(',')
      .map((c) => c.trim())
      .filter((c) => c.length > 0);
  }

  return null;
}
