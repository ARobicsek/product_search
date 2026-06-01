# Phase 37: Smart Listing Cards — Detailed Implementation Guide

**Status:** PLANNED (approved by user 2026-06-01). Ready to implement.

**Context:** This session discovered that the worker already emits ~12 fields per listing that the frontend doesn't consume (image_url, buy_url, seller_rating_pct, rating, quantity_available, brand, mpn, attrs, etc.). The `display.attrs` + `extracted_features` + `columns` pipeline is 70% built — this phase completes the last mile.

**Design decisions (already approved, do not re-ask):**
1. Structured data wins over extracted (title-derived) data — never override API fields.
2. Extracted attrs render with `opacity-60` (low-confidence visual treatment).
3. All dynamic attrs use uniform `Label: Value` format (no special widgets).
4. Product images shown when `image_url` is available. eBay gap accepted (no images).

---

## Files to modify (5 files, ordered by dependency)

### 1. `web/app/[product]/result-types.ts` (lines 15-29)

**What:** Add missing fields to `ResultListing` interface. The worker's `report_json_v2.py` `_listing_to_display()` (lines 66-94) already emits these in every JSON sidecar — the TS type just doesn't declare them.

**Replace lines 15-29 with:**

```typescript
export interface ResultListing {
  rank: number;
  source: string;
  vendor_host: string | null;
  url: string;
  buy_url: string | null;
  image_url: string | null;
  title: string;
  price_usd: number | null;
  total_for_target_usd: number | null;
  currency_approx_fx: string | null;
  condition: string | null;
  seller_name: string | null;
  is_kit: boolean;
  kit_module_count: number;
  badges: Badge[];
  seller_rating_pct: number | null;
  seller_feedback_count: number | null;
  rating: number | null;
  rating_count: number | null;
  ship_from_country: string | null;
  quantity_available: number | null;
  brand: string | null;
  mpn: string | null;
  attrs: Record<string, string> | null;
  flags: string[];
}
```

---

### 2. `web/app/[product]/ResultView.tsx`

This is the main UI change. Three sub-changes:

#### 2a. Add helpers BETWEEN `VendorFavicon` (ends line 134) and `ListingCard` (starts line 136)

Insert these constants and functions:

```typescript
/** Column keys already rendered by hardcoded card sections — skip in dynamic loop. */
const HARDCODED_COLUMNS = new Set([
  'price', 'condition', 'seller', 'title',
]);

/** Human-readable labels for known attribute keys. */
const ATTR_LABELS: Record<string, string> = {
  seller_rating: 'Rating',
  rating: 'Rating',
  rating_count: 'Reviews',
  quantity: 'Qty',
  ship_from: 'Ships from',
  brand: 'Brand',
  mpn: 'MPN',
  color: 'Color',
  size: 'Size',
  storage: 'Storage',
  material: 'Material',
  edition: 'Edition',
  pack_size: 'Pack size',
  term: 'Term',
  flavor: 'Flavor',
};

/**
 * Resolve a column's display value from the listing.
 * Structured fields (from the API) take priority over attrs (title-derived).
 */
function getColumnValue(listing: ResultListing, col: string): string | null {
  switch (col) {
    case 'seller_rating':
      return listing.seller_rating_pct != null ? `${listing.seller_rating_pct}%` : null;
    case 'rating':
      return listing.rating != null ? `${listing.rating}★` : null;
    case 'rating_count':
      return listing.rating_count != null ? `${listing.rating_count}` : null;
    case 'quantity':
      return listing.quantity_available != null ? `${listing.quantity_available}` : null;
    case 'ship_from':
      return listing.ship_from_country || null;
    case 'brand':
      return listing.brand || null;
    case 'mpn':
      return listing.mpn || null;
    default:
      // Fall back to attrs dict (extracted features from title)
      return listing.attrs?.[col] ? String(listing.attrs[col]) : null;
  }
}

/** Whether a column value came from title extraction (low confidence) vs structured API data. */
function isExtractedAttr(listing: ResultListing, col: string): boolean {
  const STRUCTURED_COLS = new Set([
    'seller_rating', 'rating', 'rating_count',
    'quantity', 'ship_from', 'brand', 'mpn',
  ]);
  if (STRUCTURED_COLS.has(col)) return false;
  // If it's resolved from attrs, it was extracted from the title by the AI filter
  return !!listing.attrs?.[col];
}
```

#### 2b. Modify `ListingCard` component

**Change the signature (line 136):**

From: `function ListingCard({ listing }: { listing: ResultListing }) {`
To: `function ListingCard({ listing, columns }: { listing: ResultListing; columns?: string[] }) {`

**Add image thumbnail — replace lines 160-162:**

From:
```tsx
      <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100 break-words">
        {listing.title}
      </h3>
```

To:
```tsx
      <div className="flex gap-3">
        {listing.image_url && (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={listing.image_url}
            alt=""
            width={48}
            height={48}
            className="rounded-lg object-cover shrink-0 self-start"
            onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
          />
        )}
        <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100 break-words">
          {listing.title}
        </h3>
      </div>
```

**Add dynamic attribute pills — insert INSIDE the `<div>` at line 175, AFTER the kit_module_count `<span>` (line 185), BEFORE the closing `</div>` (line 186):**

```tsx
        {/* Dynamic attribute pills from sidecar columns */}
        {columns?.filter(c => !HARDCODED_COLUMNS.has(c)).map(col => {
          const value = getColumnValue(listing, col);
          if (!value) return null;
          const label = ATTR_LABELS[col] || col.replace(/_/g, ' ');
          const extracted = isExtractedAttr(listing, col);
          return (
            <span key={col} className={extracted ? 'opacity-60' : ''}>
              {label}: {value}
            </span>
          );
        })}
```

**Use buy_url for action link — line 192:**

From: `href={listing.url}`
To: `href={listing.buy_url || listing.url}`

#### 2c. Pass columns from ResultView to ListingCard

**In the `ResultView` component, change line 385:**

From: `<ListingCard key={`${lst.rank}-${lst.url}`} listing={lst} />`

To:
```tsx
            <ListingCard
              key={`${lst.rank}-${lst.url}`}
              listing={lst}
              columns={!isV1 ? v2.columns : undefined}
            />
```

---

### 3. `worker/src/product_search/display_v2.py` (lines 78-79)

**What:** Currently drops unknown column keys. Change to: check if any displayed listing has that key populated in `lst.attrs`.

**Replace lines 78-79:**

From:
```python
        if pred is None:
            continue  # unknown column key — drop rather than show a blank
```

To:
```python
        if pred is None:
            # Dynamic attr from extracted_features — show if any
            # displayed listing carries a non-empty value in its attrs dict.
            if any(
                bool(str((lst.attrs or {}).get(key, "")).strip())
                for lst in displayed
            ):
                if key not in out:
                    out.append(key)
            continue
```

---

### 4. `worker/src/product_search/validators/ai_filter.py`

Two changes:

#### 4a. Broaden extraction prompt (lines 279-282)

From:
```
The profile expects the following display attributes: {display_attrs or []}
If any of these attributes can be clearly extracted from the title (e.g., color, storage capacity), add them to a new
"extracted_features" dictionary in your evaluation object for that listing. For example:
"extracted_features": {{"color": "black"}}.
```

To:
```
The profile expects the following display attributes: {display_attrs or []}
Additionally, if you can clearly identify any of these common product attributes
from the title, extract them too: color, size, storage, material, edition,
pack_size, term, flavor. Only extract when the value is UNAMBIGUOUSLY present
in the title — never guess.
If any of these attributes can be clearly extracted, add them to a new
"extracted_features" dictionary in your evaluation object for that listing. For example:
"extracted_features": {{"color": "black"}}.
```

#### 4b. Structured data wins — merge logic (lines 520-526)

From:
```python
            extracted = verdict.get("extracted_features", {})
            if extracted and isinstance(extracted, dict):
                if lst.attrs is None:
                    lst.attrs = {}
                for k, v in extracted.items():
                    if v and str(v).strip():
                        lst.attrs[k] = str(v).strip()
```

To:
```python
            extracted = verdict.get("extracted_features", {})
            if extracted and isinstance(extracted, dict):
                if lst.attrs is None:
                    lst.attrs = {}
                for k, v in extracted.items():
                    if not v or not str(v).strip():
                        continue
                    # Structured data wins — don't override real API fields
                    # with title-derived guesses.
                    if k == "condition" and lst.condition:
                        continue
                    if k == "brand" and lst.brand:
                        continue
                    if k == "quantity" and lst.quantity_available is not None:
                        continue
                    if k not in lst.attrs:
                        lst.attrs[k] = str(v).strip()
```

---

### 5. `web/lib/onboard/promptTextV2.ts` (line 35)

**What:** Improve guidance for choosing display attrs.

**Replace the text at line 35** (the `2. **product_type**` paragraph) with the version that lists all available attrs and when to use each. See the full text in the implementation plan artifact.

---

## Verification checklist

1. `cd web && npm run build` — must pass (TS compile check)
2. `cd worker && python -m pytest` — must pass (346 tests)
3. Add a unit test in `worker/tests/test_display_v2.py` for dynamic attr column resolution
4. Visually verify on `lululemon-never-lost-keychain` that cards show Color and thumbnails
