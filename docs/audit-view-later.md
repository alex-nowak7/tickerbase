# Audit View — ideas for later (not v1)

Ideas that came up mid-build. Parked here so they don't creep into v1 (spec section 11).

- **Expectation-based fluctuations (F2).** Real analytical procedures compare a balance to an *expectation*, not just to last year. Example: expect cost of revenue and receivables to grow at the same rate as revenue, and flag only the gap. That would cut the noise on fast-growing companies (AAPL FY2026 flags 9 of 12 lines under the simple rule). Raised at CHECK-IN 2, 2026-11-10.
- **Title-case company names.** SEC submissions return some names in caps ("CATERPILLAR INC").
- **Derive SG&A** from `SellingAndMarketingExpense` + `GeneralAndAdministrativeExpense` (MSFT, CRM, NFLX, BA show "Not reported").
- **Deere's custom tags.** DE reports cost of sales and receivables under its own (non-us-gaap) tags, so 4 of 6 tests show "not enough data".
- **Smaller Worker bundle (not Audit View, site-wide).** wrangler uploads every `.py` under the project folder, including pywrangler's own `.venv-workers` (~700 files, ~10 MB). Moving the app into `src/` with `"main": "src/entry.py"` would stop that and likely cut cold-start time (currently ~3.3 s). Structural change: needs Alex's OK.
