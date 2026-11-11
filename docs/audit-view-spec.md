# Tickerbase — Audit View Module: Build Spec

> Save this file in the repo as `docs/audit-view-spec.md`. It is the source of truth for this build.

---

## 0. Instructions for Claude Code (read first)

- Read this whole spec before doing anything.
- **Start with Phase 0 only.** Do not write feature code until Alex approves the Phase 0 plan.
- **Stop at every CHECK-IN** (Section 8) and wait for Alex.
- Do not change existing Tickerbase features, data sources (Finnhub, Twelve Data), or the design system unless this spec says so.
- No paid services, plan upgrades, or new paid API keys without Alex's OK.
- Alex has to be able to explain every audit concept in an interview. For every audit test you write, add a 1–2 sentence plain-English comment saying what auditors use it for.
- Ideas that come up mid-build go in `docs/audit-view-later.md`, not into the code.

---

## 1. What we're building and why

**Audit View** is a new tab on each Tickerbase ticker page. It does a simple version of what auditors do at the start of an audit (planning analytics):

1. Pull 3 years of annual financials from SEC EDGAR.
2. Set a simple **planning materiality** number.
3. Flag big year-over-year swings (**analytical procedures**).
4. Run a few **red-flag tests** on revenue and earnings quality.
5. Use AI to summarize the company's **revenue recognition policy (ASC 606)** and where the audit risk is.

**Why:** Tickerbase is Alex's portfolio project for Big 4 audit recruiting. Right now it reads as an investing tool. Audit View makes it read as audit analytics, the same kind of work firms now do with tools like PwC Halo and EY Helix.

**Audience:** audit recruiters and interviewers. They should understand the page in 30 seconds.

---

## 2. Definition of Done (binary)

v1 is done when **all** of these are true:

- [ ] Audit View tab is live on the deployed site for any US company that files 10-Ks with XBRL data.
- [ ] It shows: materiality card, 3-year fluctuation table with flags, all F3 red-flag tests, AI revenue-recognition summary, and a short "How this works" note.
- [ ] It works on all 10 test tickers (Section 9) with no crashes. Missing data shows "Not reported" instead of an error.
- [ ] No Cloudflare limit errors (CPU time, subrequests) in the logs across the 10 test tickers.
- [ ] Unit tests pass for every analytics function.
- [ ] Alex has filled in the validation table (Section 12).

When every box is checked, say: **"DoD met. Ship or extend."** Then follow the auto-stop rules (Section 11).

---

## 3. Scope

**In v1:** F1–F4 below, the UI in Section 7, caching, tests, deploy.

**Out of v1 (do not build):**
- Quarterly (10-Q) analysis
- PDF or file upload
- Industry or peer benchmarks
- Charts beyond the table
- User accounts or saved analyses
- Editing thresholds in the UI
- Any other new AI tools on the site
- Restyling existing pages
- Full support for banks and insurers (their statements are built differently). Show a "Limited support for banks/insurers" message instead.

---

## 4. Hard constraints

**Runtime.** Cloudflare Workers. In Phase 0, confirm: Python Workers or JavaScript? Free or Paid plan?

**Free plan limits:** 10 ms CPU time per request, 50 external subrequests per request, 128 MB memory. Waiting on `fetch` does not count as CPU time, but parsing does. So:
- Use the **companyconcept** endpoint (one small JSON per line item). Do **not** use companyfacts (it returns every fact the company has ever filed, which is several MB).
- Never download and parse SEC's full ticker file at request time.
- Never download the full 10-K document in v1 (it can be several MB).

**Dependencies.** No pandas, numpy, or other heavy libraries (they were removed in the Cloudflare migration). Write the analytics as plain-language pure functions with no Workers APIs inside them, so they can be tested locally.

**SEC access rules.**
- Every request to `data.sec.gov` and `www.sec.gov` needs a `User-Agent` header with an app name and contact email. Read it from a secret named `SEC_USER_AGENT` (format: `Tickerbase contact@example.com`). **Never hardcode an email.** Tell Alex the exact `wrangler secret put` command to run.
- Max 10 requests per second. Stay well under it: run requests one after another or in small batches.

**Caching.**
- EDGAR responses: cache for about 24 hours (Cache API or KV).
- AI summaries: cache permanently in KV, keyed by the 10-K accession number (a filed 10-K never changes).

**Cost: $0.** Workers AI's free tier is 10,000 neurons per day. Cache aggressively and never call the AI twice for the same accession number.

---

## 5. Data sources

### 5.1 Ticker → CIK
- Source: `https://www.sec.gov/files/company_tickers.json`
- Write a small local script that builds a compact ticker → CIK lookup, then ship it as a static asset or in KV. Don't fetch it at request time.
- CIKs must be zero-padded to 10 digits in `data.sec.gov` URLs.

### 5.2 Annual financials (XBRL)
Endpoint: `https://data.sec.gov/api/xbrl/companyconcept/CIK{10-digit}/us-gaap/{TAG}.json`
→ `units.USD` is a list of facts with `start`, `end`, `val`, `accn`, `fy`, `fp`, `form`, `filed`.

**Rules:**
- Use only facts where `form` is `10-K` or `10-K/A`.
- **Flow items** (income statement, cash flow): keep facts where `end − start` is 350–380 days.
- **Balance sheet items:** point-in-time facts (no `start`).
- The `fy` field is the fiscal year of the *filing*, not the period. **Line facts up by `end` date.**
- **Fiscal year-ends:** take the 3 most recent annual `end` dates from the revenue series, and line every other item up to those dates.
- If the same `end` date appears in several filings (prior-year comparatives), use the value from the most recent `filed` date. If an older filing had a value more than 1% different, save it for test F3-f.
- Try tags in the order listed and use the first one that has data. A 404 means the company doesn't use that tag, so move to the next.

| Line item | Tags (try in order) | Type |
|---|---|---|
| Revenue | `RevenueFromContractWithCustomerExcludingAssessedTax`, `Revenues`, `SalesRevenueNet` | flow |
| Cost of revenue | `CostOfRevenue`, `CostOfGoodsAndServicesSold` | flow |
| Gross profit | `GrossProfit` (otherwise revenue − cost of revenue) | flow |
| SG&A | `SellingGeneralAndAdministrativeExpense` | flow |
| Operating income | `OperatingIncomeLoss` | flow |
| Pre-tax income | `IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest`, `IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments` | flow |
| Net income | `NetIncomeLoss` | flow |
| Operating cash flow | `NetCashProvidedByUsedInOperatingActivities` | flow |
| Accounts receivable | `AccountsReceivableNetCurrent` | instant |
| Inventory | `InventoryNet` | instant |
| Deferred revenue | `ContractWithCustomerLiabilityCurrent`, `DeferredRevenueCurrent` | instant |
| Total assets | `Assets` | instant |

**Subrequest budget:** stay under 50 total per request. If that won't fit, split the work into separate endpoints that the page calls in parallel.

### 5.3 Latest 10-K revenue policy text
1. `https://data.sec.gov/submissions/CIK{10-digit}.json` → in `filings.recent`, find the first entry with form `10-K` → get `accessionNumber` and `primaryDocument`.
2. `https://www.sec.gov/Archives/edgar/data/{CIK without padding}/{accession without dashes}/FilingSummary.xml` → lists the filing's report pages. Each `Report` has `ShortName`, `HtmlFileName` (e.g. `R9.htm`), and `MenuCategory`.
3. Pick the revenue page: a `ShortName` containing "Revenue". Prefer `MenuCategory` "Policies", then "Notes". Fallback: the "Significant Accounting Policies" page.
4. Fetch that R page (small), strip the HTML to plain text, and cap it at about 12,000 characters.

---

## 6. Features

Put every threshold in one config block at the top of the analytics module.

### F1 — Planning materiality
- Benchmark: **5% of pre-tax income** (latest year).
- If pre-tax income is ≤ 0 or below 1% of revenue, use **0.5% of revenue** instead.
- Show the number, which benchmark was used, and this line: *"Auditors set materiality to decide how big a mistake has to be to matter. Real audits use more judgment; this is a simplified rule."*

### F2 — Fluctuation analysis
- For each line item, compare latest year to prior year, and prior year to two years back: $ change and % change.
- **Flag** a change when **|$ change| ≥ materiality AND |% change| ≥ 10%**.
- Each flagged row gets a short "why an auditor cares" note and the assertion most at risk (existence, completeness, accuracy, cutoff, valuation). Write one note template per line item by hand; no AI here.

### F3 — Red-flag tests (latest year vs. prior year)
Each test returns: status (`flag` / `ok` / `not enough data`), the numbers used, the threshold, a one-line plain-English note, and the assertion at risk.

| # | Test | Flag when | Why auditors care | Assertion |
|---|---|---|---|---|
| a | Receivables growing faster than revenue | AR growth % − revenue growth % > 10 pts. Also show DSO = AR ÷ revenue × 365 for both years | Revenue may be overstated or not collectible | Existence, valuation |
| b | Inventory growing faster than cost of sales | Inventory growth % − cost of revenue growth % > 10 pts. Also show days inventory for both years | Inventory may be slow-moving or obsolete | Valuation |
| c | Gross margin swing | Gross margin changes by more than 2 percentage points either way | Possible cost cutoff errors or misclassification | Cutoff, accuracy |
| d | Profit not backed by cash | (Net income − operating cash flow) ÷ average total assets > 10% | Earnings quality risk | Occurrence, accuracy |
| e | Deferred revenue moving the wrong way | Revenue up > 10% while deferred revenue is down > 10% (skip if no deferred revenue) | Revenue may be getting pulled forward | Cutoff |
| f | Prior-year numbers changed | A prior-year figure differs by more than 1% between filings | A restatement or reclassification needs an explanation | Accuracy, presentation |

### F4 — AI revenue recognition summary
- **Input:** the revenue policy text from 5.3, plus the F2/F3 results (flags only).
- **Model:** a Workers AI instruction-tuned text model. Pick a current one, check its neuron cost per call, and prefer the smallest model that gives solid output. **If Tickerbase already calls an LLM, reuse that setup instead.** Confirm which in Phase 0.
- **System prompt:**
  > You are helping a student explain audit risk. Use ONLY the filing text and numbers provided. Do not invent facts. If the text does not cover something, list it in `not_disclosed`. Write in plain English with short sentences and no hype. Focus on ASC 606 judgment areas: identifying performance obligations, variable consideration (discounts, rebates, returns), principal vs. agent, timing (point in time vs. over time), and bundled arrangements. Return valid JSON only.
- **Output JSON:**
  ```json
  {
    "how_they_make_money": "2-3 sentences",
    "judgment_areas": [
      {"area": "Principal vs. agent", "plain_english": "1-2 sentences", "evidence": "short quote from the filing text"}
    ],
    "top_audit_risks": [
      {"risk": "...", "why": "...", "linked_flag": "F3a or null"}
    ],
    "not_disclosed": ["topics the extracted text did not cover"]
  }
  ```
- Max 3 judgment areas and 3 risks. Evidence quotes of 25 words or fewer.
- Validate the JSON. If it's malformed, retry once, then show "Summary unavailable" (never crash).
- Cache permanently in KV, keyed by accession number.
- Label on the page: *"AI summary of the company's own disclosure. Educational, not an audit opinion."*

### F5 — STRETCH: Critical Audit Matters (only if Alex says go after DoD)
- Fetch the 10-K primary document, find the auditor's report section "Critical Audit Matter(s)", pull out each CAM's title and short description, and show them next to our flags.
- **Measure CPU time first.** If it goes over the free plan's 10 ms limit, stop and report to Alex with the options (upgrade to Workers Paid at $5/month, or skip F5). Do not upgrade on your own.

---

## 7. UI

- A new **Audit View** tab or section on the existing ticker page, using the existing Tickerbase styles.
- **Order on the page:**
  1. Header line: company name, fiscal years covered, "Source: SEC EDGAR (XBRL)"
  2. Materiality card
  3. Red flags: flagged tests first, each with a status pill, the numbers, a one-line note, and the assertion
  4. Fluctuation table (3 years, flagged rows highlighted)
  5. AI revenue recognition summary
  6. **How this works:** 4–5 plain-English lines explaining the method. This is the part recruiters read.
- **Copy:** short, plain, human. No hype and no "AI-powered insights" language.
- **Loading:** show the numbers first. Load the AI summary in a separate request so the page never waits on the AI.
- Mobile: the table can scroll horizontally.
- **Error states:** show "Not reported" for any missing item. If the company has no XBRL 10-K (foreign filers using 20-F, brand-new IPOs), show one clear message for the whole tab.

---

## 8. Build phases and check-ins

**Phase 0 — Look around and plan (no feature code)**
- Read the codebase and report back: language/runtime, framework, Cloudflare plan (Free or Paid), how pages and APIs are organized, whether an LLM is already used, and where tests live.
- Propose the file layout and endpoints for Audit View.
- Point out anything in this spec that conflicts with the codebase.
- **CHECK-IN 1:** wait for Alex's OK.

**Phase 1 — EDGAR data layer**
- Ticker → CIK lookup, companyconcept fetcher with tag fallbacks, lining up annual facts, caching, and the submissions → FilingSummary → R-page text extraction.
- Save fixture JSON for 2 companies (e.g. AAPL and WMT) in `tests/fixtures/` so tests never call SEC.

**Phase 2 — Analytics engine**
- F1, F2, F3 as pure functions, with unit tests. Include edge cases: negative pre-tax income, no inventory, no deferred revenue, a prior-year value of zero, a missing year.
- **CHECK-IN 2 (midway):** print a plain-text F1–F3 report for AAPL, WMT, and CAT. Alex checks whether the flags make sense before any AI or UI work starts.

**Phase 3 — AI summary (F4) and caching**

**Phase 4 — UI, "How this works", deploy**
- Run all 10 test tickers on the live site and check the logs for limit errors.
- **CHECK-IN 3:** report the status of every item on the DoD checklist.

**Phase 5 — F5 stretch** (only if Alex says go)

**Budget:** Phases 0–4 in about 4–5 Claude Code sessions. If Phase 2 isn't done by the end of session 3, stop and tell Alex.

---

## 9. Test tickers

All are large US filers, so their 10-Ks include Critical Audit Matters. If one breaks because of unusual tags, swap in a company from the same industry and make a note of it.

| Ticker | Why it's in the set |
|---|---|
| AAPL | Hardware; baseline |
| MSFT | Software; bundled arrangements |
| CRM | SaaS; large deferred revenue |
| NFLX | Subscriptions |
| WMT | Retail; inventory heavy |
| TGT | Retail; margin swings |
| CAT | Industrial; receivables and inventory |
| DE | Industrial with a financing arm |
| PFE | Pharma; rebates and returns (variable consideration) |
| BA | Aerospace; long-term contracts recognized over time |

---

## 10. Open decisions (defaults — go with these unless Alex says otherwise)

1. **Cloudflare plan:** stay on the plan Tickerbase uses now. If something won't fit, stop and ask; don't upgrade.
2. **AI provider:** Workers AI (free). Reuse the existing LLM setup if Tickerbase already has one.
3. **CAMs (F5):** stretch goal, not part of v1.
4. **Name:** "Audit View".

---

## 11. Auto-stop rules

- After the DoD is met: **no new Audit View features for 2 weeks.** Ship it, write it up, update the resume.
- **Polish cap:** one UI pass after the DoD, max 1 session. Then stop.
- If any phase runs past 1.5× its budget, stop and re-scope with Alex instead of pushing through.
- New ideas go in `docs/audit-view-later.md`.

---

## 12. After the build (Alex's part — accounting reps, not delegated)

**Validation table.** For each test ticker, open the latest 10-K, find the auditor's report, and read the Critical Audit Matters:

| Ticker | Auditor's CAMs | Our top flags | Overlap? (Y / N / Partial) | Notes |
|---|---|---|---|---|
| AAPL | | | | |
| ... | | | | |

Note: many CAMs cover goodwill, taxes, or reserves, which this tool doesn't test. A low overlap number is fine. Report it honestly.

**Resume bullet** (fill in after validation; adjust the stack to match the codebase):
> Built an audit planning module for Tickerbase ([stack], Cloudflare Workers) that pulls 3 years of SEC XBRL financials, sets planning materiality, flags material fluctuations and revenue-recognition red flags, and summarizes ASC 606 risk areas with AI; validated flags against auditor-reported Critical Audit Matters for 10 public companies (overlap in X of 10).

**Interview prep — be able to explain in plain words:**
- Materiality, and why 5% of pre-tax income is a common benchmark
- Analytical procedures, and why auditors run them during planning
- Each of the 6 red-flag tests and its assertion
- The 5 steps of ASC 606
- What a Critical Audit Matter is

---

## 13. Build notes (changes made during the build)

Phase 0 (approved 2026-11-10): Python Workers + FastAPI, Cloudflare **Free** plan, no existing LLM, no tests before this build. Audit View sits behind an "Investor view | Audit View" switch on the single landing page. Ticker → CIK ships as the generated module `sec_tickers.py`.

Phase 1:
- **Tag choice:** a tag is only used if it has a value for the latest fiscal year (revenue: the tag with the most recent data). Plain "first tag with data" would have picked tags companies abandoned years ago (AAPL `SalesRevenueNet` stops in 2017; CAT `NetIncomeLoss` stops in 2010).
- **Extra fallback tags:** `ReceivablesNetCurrent` for accounts receivable (Walmart), `ProfitLoss` for net income (Caterpillar).
- **Revenue policy text:** the revenue rows of the 10-K's accounting-policies page (picked by XBRL element name, e.g. `RevenueRecognitionPolicyTextBlock`), plus a dedicated "Revenue" note if the filing has one (segment pages skipped). Falls back to the whole policies page.
- **Caching:** one KV entry per company per day (not one per SEC response), to stay inside KV's free 1,000 writes/day.
- **WMT revenue** = net sales (`RevenueFromContract...`, $706.4B FY2026), which excludes ~$6.8B of membership income. Follows the spec's tag order.

Phase 2-4:
- F2 kept as specified (flags fast growers heavily; expectation-based version parked in `audit-view-later.md`).
- **AI model:** `@cf/meta/llama-3.3-70b-instruct-fp8-fast` with JSON mode, ~215 neurons per new 10-K, cached forever. Llama 3.1 8B was cheaper but reasoned poorly about risk; Gemma 4 26B failed. Evidence quotes are checked against the filing text and dropped if not found.
- Separate cache versions for SEC data (`AUDIT_DATA_VERSION`) and AI summaries (`AUDIT_AI_VERSION`), so data fixes don't throw away paid-for summaries.
- Deep links: `/?t=CAT&v=audit` opens straight to Audit View.
- Local test environment lives outside the repo (`%USERPROFILE%\.venvs\tickerbase`) because wrangler bundles every `.py` file in the project folder.
