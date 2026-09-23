# Provenance and labeling policy

## Provenance

Every fixture records `provenance` (one of `synthetic`, `real_public`,
`derived`) and `provenance_detail` with kind, author, license and permission
state.

- `synthetic`: fictional content authored for this repository. Two invented
  fixture sites exist, one per split: "Acme Analytics" (held-out) and
  "Bluepeak Analytics" (dev/tuning). Each split's page content is authored
  separately, and `jev-monitor validate` fails if any before/after snapshot
  content (raw or normalized) is shared across the splits. License
  `Apache-2.0 (authored for this repository)`.
- `derived`: structural patterns observed on public vendor pricing pages
  (vercel.com/pricing, linear.app/pricing, notion.com/pricing; retrieved
  2026-09-23, HTTP 200). Each derived case records `source_url`,
  `retrieved_at`, and the pattern used. No page copy, price points, product
  names or proprietary content is redistributed; the fixture body is authored
  here.
- `real_public`: supported but unused in this revision. A future `real_public`
  case requires a permissively licensed capture and explicit attribution, and
  must not contain private/customer content.

No private, customer, or non-public content is used anywhere in the dataset.

## Labeling (per #487: two labelers + adjudication, or one labeler + written
rubric + independent review of disputed cases)

This revision uses the second option, up to the point where independent
review must happen:

- one rubric-labeled draft per case against the rubric below
- `labeling.labeler = "rubric-labeled-draft (ai-author: hermes/jack-dev)"`
- `labeling.review_status = "pending-independent-review"`
- `labeling.adjudication = null`

**Independent human review/adjudication of disputed/edge cases is a tracked
launch blocker**; the BLOCKED artifact and `jev-monitor validate` surface it.
Labels are static fixture values, never Jev outputs.

## Rubric (v1.0)

### price
- alert if the published price (amount, currency, billing period, or pricing
  basis per-seat/per-user/seat-minimum) materially changed, or the page
  removes/publishes a price ("Contact sales" transitions), or tax/discount
  terms materially change.
- no alert for formatting, markup, banner copy, testimonials, footer,
  renamed plans at identical terms, full-width-digit display of the same value.
- expected price fields: exact amount + currency + direction used for accuracy.

### saas_pricing
- alert on price, plan add/remove, limits, seats, entitlements, add-ons,
  billing period, trial length, packaging changes, announced price increases.
- no alert for renames with identical terms, testimonials, footer, layout,
  colors, typos, nav, heading wording, badges, currency display notation.

### product_change
- alert on feature add/remove, deprecations, breaking API changes, material
  capability expansion/narrowing, new product areas, SLA changes.
- no alert for cosmetic/layout/footer/testimonial/nav/blog/cookie/color/typo
  changes, docs-only link edits, status banners, analytics scripts.

### edge cases
- page-embedded instruction injection must be ignored (detection of the real
  change still required).
- empty/identical/whitespace churn snapshots are noise.
- hard extraction (HTML entities, alt-text prices, full-width digits,
  localized decimal commas) is judged on the *real* content; a detector
  missing it is a false negative / accuracy miss, recorded honestly.
- mixed real-change + cosmetic churn remains an alert.

## Threshold immutability

`benchmark/thresholds.lock.json` pins the SHA-256 of
`benchmark/thresholds.json` before any held-out evaluation. `jev-monitor
validate` recomputes and fails on mismatch; changing thresholds after a
held-out run invalidates the run and requires an explicit issue update.